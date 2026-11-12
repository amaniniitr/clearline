# Decisions

## D-01 · 2026-11-09 · Build Clearline from scratch around dual-read verification

**Context.** Existing open-source invoice extractors typically rely on either single-pass regex/Tesseract templates (which break on unseen vendor layouts), JSON-only inputs, or heavy multi-container setups without audit trails.

**Decision.** Build Clearline per `docs/ARCHITECTURE.md` around independent dual reads, deterministic grounding/arithmetic verification, GoRules ZEN policy tables, and DBOS durable execution.

**Why.** Handling messy, unseen vendor formats reliably—while never letting an unverified model output release a payment—is the core operational requirement.

## D-02 · 2026-11-09 · Claude API, pay per call; never the Claude Code CLI

**Decision.** All model calls go through `ANTHROPIC_API_KEY` in the AI gateway.
- Read A on Sonnet 5.5 ($2 / $10 per million tokens).
- Read B, the narrator and the Jev fallback on Haiku 5.5 ($0.10 / $0.50 per million tokens).

**Cost control.**
- Estimated ~$0.02 per invoice: about 3k input and 1k output tokens on Sonnet, plus a few hundred tokens on Haiku. This will be measured per run and shown on the dashboard.
- Every live call is recorded as a fixture. Rehearsals and the scripted demo replay those fixtures at $0.
- A daily spend cap (`DAILY_SPEND_CAP_USD`, default $10).
- With no key, an offline reader and a heuristic judge run instead. Both are marked "degraded".
- The 181-document benchmark costs about $4 to run once, then replays from fixtures.

## D-03 · 2026-11-09 · Inputs: three layers

1. **Scripted scenarios.** H1, E1–E4 and B1–B6, tied to our PO dataset, on about 6 vendor templates. Some get scan or photo damage.
2. **Unseen documents.** The Invoice Extraction Benchmark (181 documents, CC BY 4.0, attributed), scored for field accuracy by category.
3. **Real invoices** from invoice2data (MIT): AWS, QualityHosting (German), Flipkart and oyo (Indian). Shown live, with an expected outcome of "no PO or vendor on file → REVIEW".

## D-04 · 2026-11-09 · Default models: Gemini on Vertex AI (supersedes the provider part of D-02)

**Context.** The author has Google Cloud free-trial credit. The free trial can't pay for partner models offered as a managed API, which includes Claude on Vertex. Gemini is covered.

**Decision.**
- Read A: `gemini-3.8-flash`.
- Read B, narrator and judgment fallback: `gemini-3.5-flash-lite`.
- Re-read on disagreement: `gemini-4-argon`.
- Claude stays available behind the same `ModelPort`.

D-02's cost controls still apply: fixtures, the spend cap and offline mode. Estimated ~$0.007 per full read. See ARCHITECTURE §17.

## D-05 · 2026-11-09 · Learning loop with a reading ladder (ARCHITECTURE §16)

**Decision.** Learn per-vendor layout templates in shadow mode, then promote them (T3 full read → T2 template + cheap cross-check → T1 template only, with 10% audit sampling). Demote immediately inside the same run on any failed check.
- Also learn aliases, the vendor item map, cached judgments and reading hints.
- Repeated human decisions only produce *suggested* policy patches through teach-the-agent: at least 5 consistent decisions, none contrary, from at least 2 different people.
- Learn only from clean runs. Never learn bank details, duplicate checks or locked safety rules.

**Why.** Most invoices come from recurring vendors, so cost and questions should fall with volume. Money decisions stay governed.

## D-06 · 2026-11-09 · Where the build differs from ARCHITECTURE v5 (and why)

| Design said | Built | Why |
|---|---|---|
| Investigator on Pydantic AI + DBOS | A Gemini function-calling loop with the same read-only tools, ≤ 6 calls and ≤ 30 s, plus a deterministic **playbook** that uses the same tools when no model is available | Fewer moving parts. Each investigation runs inside a durable DBOS step. The playbook keeps the stage working offline. |
| Jev as primary judge, `system-one-adapter` as fallback | No Jev key, so Gemini answers the same typed questions with a strict probability schema (marked degraded), with keyword heuristics as the last resort | The adapter's Gemini provider calls an Interactions endpoint that Vertex express mode doesn't serve (404). Jev slots in as soon as `OPENROUTER_API_KEY` is set. |
| Read B uses Claude citations | Gemini cites a **line number** in a strict schema, and code checks that the value is on that line | Gemini has no Anthropic-style citations. The guarantee is the same, because code does the check. |
| Render check by OCR of a cropped region | Full-page OCR, cached per document page. Boxes overlapping the field are compared, tolerating separator noise but not changed digits | Small crops produced false tamper alarms ("1 1,857. 7.60"). With full-page OCR there were 0 false positives across all 24 scenario PDFs. |
| shadcn/ui | Hand-written shadcn-style primitives on Tailwind v4 (`src/components/ui.tsx`) | The shadcn CLI init hung and left a half-configured project. Same look, fewer dependencies. |
| `docker compose` single host | Local scripts (`scripts/api.sh`, `scripts/web.sh`) on Homebrew Postgres | Docker isn't installed on the build machine. A compose file is still to do. |
| Policy replay and "teach the agent" | Not built yet. Decision-pattern **suggestions** are surfaced on the Learning page. | Time went to the core pipeline, the learning ladder and the edge cases. Facts and master snapshots are already stored per run, which is what replay needs. |

## D-07 · 2026-11-09 · Locale handling (answers the language question)

- English UI and outputs.
- Documents can be in any language the reader handles. Tested on Hindi (G1), Dutch (E4) and German (QualityHosting, a real invoice).
- Amounts are parsed per locale: lakh grouping, decimal comma, currency symbols.
- Dates:
  - German, Dutch and French month names are understood.
  - An ambiguous `03/04` is read day-first for EUR/INR/GBP documents and month-first for USD, and the reason is recorded.
  - With no locale signal it is flagged Uncertain rather than guessed.
- Approval limits compare USD equivalents, using fixed demo FX rates.
