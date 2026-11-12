# Clearline: AP invoice processing (Zamp ASA case study, PS-1)

Status: **v5**. Adds the learning loop (§16) and moves the default models to Gemini on Vertex AI, because the free-trial credit doesn't cover Claude (§17). Claude remains an alternative behind the same interface. Earlier wording: v4 used Claude APIs for reading, writing and one bounded investigation agent. Jev handles typed judgment calls. Includes a tech-stack comparison. Revised after a second independent review (§15). Author: Aman. Date: 2026-11-09.

Clearline takes a vendor invoice (PDF, scan or forwarded email) and checks it against the vendor master, the purchase order and the goods receipt. It returns one decision that answers two questions:

1. **Do we owe this?** Is the invoice valid, and does it match what was ordered and received?
2. **Is it safe to pay where they ask?** Do the remittance details match the verified vendor record?

Every stage is visible live as it runs. When a person is needed, the system says what it already tried and asks **one specific question**.

---

## 0. Design principles

| # | Principle | Why |
|---|---|---|
| P1 | **Workflow first, agent only where the path is open-ended.** Claude *reads, writes and investigates exceptions* through read-only tools. Jev *judges* text (typed decisions with calibrated probabilities). Deterministic code *decides money*. | Each tool is used where it is strongest (Anthropic's "workflows vs agents" guidance). No model can release a payment. |
| P2 | **Confidence is evidence we can show.** Each field is **Verified**, **Read** or **Uncertain** (§4). | Finance users trust "reconciles with the lines and matches the PO" more than "0.87". |
| P3 | **Every check is a named rule with evidence. Passes are recorded too.** | The UI shows the full checklist, and an auditor can see everything that was checked. |
| P4 | **Worst finding wins. Every outcome has an owner, a recommended action and one question.** | "Review" on its own is not actionable. |
| P5 | **No model can approve on its own.** Model outputs can add findings. Wherever a model resolves an ambiguity (vendor identity, line pairing, a PO found by the agent), the policy caps the outcome at REVIEW unless a deterministic check also confirms it. | A hallucination cannot pay an invoice. |
| P6 | **Proposals are immutable. Human decisions are separate records. A correction starts a new run.** | The audit trail stays intact, and machine accuracy stays measurable. |
| P7 | **One append-only event log drives the live view, history and audit.** | One source of truth, and old runs can be replayed. |
| P8 | **Policy is data.** The outcome and routing logic is a decision table that a finance person can read and edit. It is versioned and pinned to each run. | Matches Zamp's framing (process owners edit the operating procedure). Allows "replay history under a new policy". |
| P9 | **Degrade, don't fail.** If Jev is unavailable, the same questions run on Claude Haiku through the same interface, with that backend's own thresholds and a "degraded" mark on the stage. If Claude is unavailable, a text-layer parser runs below the confidence bar. Live mode can fall back to recorded fixtures. | The demo cannot break. |
| P11 | **Plain language for the buyer.** The UI never says "Noul p=0.97", "Hungarian" or "JDM". It says "Asks to change bank details: very likely". Technical detail sits behind a "details" toggle. | We are graded on explaining this to a non-technical buyer. |
| P10 | **Eval-driven.** Every scenario has ground truth, a scorer and a calibration curve. | Proves the process works, not just that it looks finished. |

---

## 1. Tech-stack options considered

Eight complete stacks were compared, each with real examples (sources in §14).

| | Stack | Live UI | Human pause/resume | Strengths | Weaknesses for us |
|---|---|---|---|---|---|
| **A** | **Python: FastAPI + DBOS + Postgres + Next.js** | SSE from FastAPI | Run status + `apply_decision` workflow (or `DBOS.recv/send`) | Best document and finance libraries (pdfplumber, RapidOCR, python-stdnum, schwifty, ZEN Python, Jev Python SDK). One process, no extra orchestrator. | Two languages; API types must be generated (OpenAPI → TS) |
| B | TS: Next.js + Vercel AI SDK + Trigger.dev v4 + Drizzle | `useRealtimeRun` hooks (no SSE code) | Waitpoint tokens (`wait.forToken`), zero cost while waiting | One language, Zod schemas shared end to end, ready-made realtime | Weak Node PDF/OCR/validator ecosystem. Adds a hosted orchestrator. |
| C | TS: Next.js + Inngest + Anthropic SDK | Inngest Realtime channels | `step.waitForEvent` matched on invoice ID | Event-driven, has a documented invoice-approval pattern | Steps are bound by HTTP timeouts. Event plumbing is harder to follow. |
| D | Convex + Next.js | Every `useQuery` is live | Status mutation + workflow component | Fastest live dashboards | Lock-in. V8 isolates cannot run Python OCR or validators. Not SQL. |
| E | Mastra + Next.js | Workflow stream piped to SSE | `suspend()` / `resume()` with typed schemas | Clean TS workflow model | Newer ecosystem. Same Node document-library gap as B. |
| T | Python + Temporal | Custom SSE | Signals / `wait_condition` | Most battle-tested durable execution. Official invoice + Claude + HITL sample. | Separate server cluster, plus workflow determinism rules. DBOS gives the same guarantees in-process. |
| F | LangGraph + Platform | Server SSE stream | `interrupt()` / `Command(resume)` | Time-travel debugging, graph visualisation | Heavy abstraction for a mostly deterministic pipeline. Platform hosting is complex. |
| G | n8n + custom frontend | Database polling or Supabase Realtime | Wait node + `$execution.resumeUrl` | Fastest prototype, ERP connectors | Logic lives in JSON. Live UI needs workarounds. Hard to review and test. |

Restate and Hatchet were also looked at. Both are viable, but each adds infrastructure that DBOS avoids.

**Decision: Stack A.** The hard parts of this problem are documents (layout, word boxes, OCR) and financial validation (IBAN, ABA routing, VAT/GSTIN checksums, Decimal arithmetic). Python has mature libraries for all of them, and so do the two engines we rely on: the ZEN rules engine and the Jev SDK. The live UI is a single SSE endpoint, which is a small cost. **Trigger.dev v4 (B) is the runner-up.** If we went all-TypeScript, it would replace our SSE code with ready-made realtime hooks. We record that trade-off rather than pretend B has no advantages.

Live channel: SSE for the pipeline view (one-way, low latency, reconnects natively, works through proxies). Supabase Realtime was rejected because WAL replication adds latency and ordering quirks for a sub-second stage view.

### Final stack

| Layer | Choice |
|---|---|
| API | Python 3.12, FastAPI, Pydantic v2, uv |
| Workflow | DBOS Transact, in-process on Postgres, with a pinned `application_version`. Two short workflows (§3.4). |
| Database | Postgres 16, direct connection (no transaction pooler, because advisory locks are used) |
| Reading and writing | **Anthropic Claude API**: Sonnet 5.5 for extraction, Haiku 5.5 for the second reading, the narrator and the Jev fallback. Model IDs are pinned and recorded on every run. |
| Investigation agent | **Pydantic AI** agent on Claude Sonnet 5.5 with **DBOS durable execution** (first-party integration), so each tool call is a durable, streamed step (§6). We chose it over the Claude Agent SDK, which wraps the Claude Code runtime and its file/shell tools. That is more capability and attack surface than a read-only server-side investigator needs. |
| Typed judgments | **Jev** (TypeSafe System One) via `typesafe-sdk`, pinned to `jev-1.14.0`. **Primary route is OpenRouter** (`/api/v1/systemone`, no waitlist), with direct TypeSafe as the alternative. Fallback is TypeSafe's official `system-one-adapter` on Claude Haiku (§5). |
| Rules / policy | **GoRules ZEN 2.0** (Rust, Python bindings, MIT) running JDM decision tables, with assertions and static type checks used as a policy CI gate. The **`@gorules/jdm-editor`** React component sits behind an "advanced" view. Docker images must be glibc-based, because ZEN has no musl/Alpine build. |
| Word boxes | pdfplumber for PDFs with a text layer. RapidOCR (ONNX) for scans; it behaves the same on Mac and Linux. |
| Matching / validators | rapidfuzz, scipy (Hungarian assignment), schwifty (IBAN/BIC), python-stdnum (ABA, EIN, EU VAT, GSTIN) |
| Frontend | Next.js 16, shadcn/ui, TanStack Table, react-pdf with an SVG bbox overlay, Recharts. Types generated from the FastAPI OpenAPI spec. |
| Observability | OpenTelemetry → Langfuse (self-hostable). Every model call is a traced span carrying cost. |
| Hosting | One VM running `docker compose` (Postgres, core, web). The browser talks to FastAPI SSE directly. |

---

## 2. System context

```
                     ┌──────────────────── docker compose (one host) ─────────────────────┐
 Upload / .eml ────▶ │ FastAPI ─ Ingest ─▶ DBOS workflow: process_invoice                   │
                     │   ▲                  preprocess → read → verify → resolve → match    │
 Browser ◀── SSE ────┤   │                  → screen → judge → rules(ZEN) → decide → explain │
 (Next.js console)   │   │                  ─▶ proposal ─▶ apply_decision → commit → post   │
                     │   │                                                                  │
                     │ Postgres: master data · documents · invoices · runs · run_events ·   │
                     │           proposals · decisions · ledger · policies · audit          │
                     └───────┬──────────────────────────┬──────────────────────────┬────────┘
                             │ AI gateway (pin, retry,  │                          │
                             ▼ cost, fixtures, traces)  ▼                          ▼
                     Claude API                 Jev (TypeSafe / OpenRouter)     Langfuse
               Sonnet 5.5 · Haiku 5.5           fallback: system-one-adapter→Haiku
```

---

## 3. The pipeline

### 3.1 Stages

```
process_invoice (seconds; Investigate adds ≤ 30 s only on exceptions):
 Intake → Pre-process → Read (A ∥ B) → Verify → Resolve vendor → Resolve PO → Pair lines
   → 3-way match → Screen → Judge (Jev) → Rules (ZEN) → [Investigate (agent)] → Decide → Explain ─▶ proposal

apply_decision (on auto-approve or human action):
 Validate action, role & segregation of duties → Commit (locks, re-check PO balance + duplicate, write ledger)
   → Post bill to mock ERP → Read back & compare → Notify

on_event (new GRN posted · vendor email reply):
 awaiting_receipt / awaiting_vendor cases → new child run → re-match → release or re-ask
```

| Stage | What it does | Example live event |
|---|---|---|
| **Intake** | sha256 (an exact re-delivery is linked to the original run). MIME sniffing, size and page limits. Accepts PDF, JPEG/PNG/HEIC photos (normalised to an image block) and `.eml`. For `.eml`: pulls out the attachments plus From / Reply-To / Return-Path / Subject / body, and Jev triages the email (invoice / statement / dunning / bank-change notice / vendor reply). | "PDF, 2 pages, emailed from billing@acme-supp1y.com, triaged as invoice" |
| **Pre-process** | Detect the text layer. Render pages with pypdfium2. Build a **line map**: each text line with page, bbox and word boxes (pdfplumber, or RapidOCR for scans and photos). | "Scanned image, 41 text lines located" |
| **Read A** (Claude Sonnet, structured outputs) | Send the PDF document block (Claude sees page images and extracted text), or an image block for photos. Require a strict JSON schema: header, lines, `document_type`, `tax_mode`, terms, remittance, and a `quote` per field. Effort is set low to keep latency down. | "Read 15 fields and 4 lines (Sonnet 5.5, 2.1 s)" |
| **Read B** (Claude Haiku, **citations**) | Send the **line map as a custom-content document**, one content block per located line, with citations enabled. Claude answers `field: value` lines for **header and remittance fields**. Each citation returns **block indices**, which map exactly to our line bboxes. The value is then narrowed to word boxes by substring position. Line items are grounded through A's quotes instead. This reading differs from A in model, input (text only) and output format (citation-bound). Structured outputs and citations can't be combined in one call, so two calls are needed anyway. | "Read B: 13 of 15 header fields cited to a line on the page" |
| **Verify** | Compare A and B per field. Ground each field. Check that each value appears in its cited text (deterministic normalised match, not a model call). Reconcile: Σlines vs subtotal, + tax + freight vs total, qty × unit vs amount, trying both exclusive and inclusive tax. **Render check:** for payment-critical fields (bank details, total, invoice number, PO), OCR the rendered page region with RapidOCR and compare it with the text-layer value. This catches hidden or overlaid text that would fool both reads. If A and B disagree and arithmetic can't decide, **re-read** a high-resolution crop with Sonnet. Normalise money (Decimal), dates (flag ambiguous ones like 03/04) and currency (ISO). Mark each field Verified, Read or Uncertain. | "Total Verified (A = B, reconciles, render matches). Invoice date Uncertain (A 03/04, B 04/03)." |
| **Resolve vendor** | Tax ID, then bank account, then alias fuzzy match. If the top two candidates are close, ask **Jev**: "Is `printed_name` the same legal entity as `candidate`?" A vendor resolved only by Jev caps the outcome at REVIEW. | "Acme Steel Pvt Ltd: tax ID match" |
| **Resolve PO** | Explicit: canonical match within this vendor's POs. Inferred: rank the vendor's open POs by amount, lines and date, and accept only a unique plausible winner. Non-PO vendors take the non-PO path. | "No PO printed. 4 open POs checked. PO-4401 matches 3 of 3 lines." |
| **Pair lines** | SKU exact match first. Otherwise ask **Jev** one Noul per candidate pair ("does invoice line X describe the same item as PO line Y?"). The probabilities feed a Hungarian assignment with a 0.6 floor. A **deterministic spec-token veto** applies first: numbers and units in the two descriptions (M8 vs M10, 6 mm vs 8 mm, kg vs lb) must match, because Jev can't compare numbers. Jev's `line_kind` Choice keeps freight and fees out of PO pairing. Auto-approval needs a SKU match or p ≥ 0.95 that passes the veto. | "3 goods lines paired (2 by SKU, 1 by description p = 0.97), 1 freight line" |
| **3-way match** | Per line: price vs PO. Qty vs received − already billed. Qty vs ordered − already billed. Header: cumulative billed vs PO total. Python computes **raw measurements** (`price_delta_abs`, `price_delta_pct`, `qty_billed`, `qty_received_net`, `freight_amount`, …). Tolerances are applied in the policy (§7), not here. | "Line 2: billed 100, received net 60" |
| **Screen** | Hard duplicate: vendor + normalised number, checked across vendor records that share a tax ID or bank account. Soft duplicate: same amount within 30 days, *excluding* invoices with a different service period or on a blanket PO, so monthly rent and SaaS invoices don't trigger it. **Payment safety:** invoice bank vs verified account. IBAN/ABA checksum. Days since the last bank change in master data. First payment to this vendor. Sender domain vs vendor domain (edit distance + homoglyphs). Reply-To mismatch. | "New IBAN not on the vendor record. Sender domain is 1 edit from acme-supply.com." |
| **Judge (Jev)** | Two separate calls over **redacted** text (account numbers and amounts replaced with `[BANK_ACCOUNT]` / `[AMOUNT]`). Call 1: pressure language, payment-change request, document type check. Call 2, kept separate so injected text can't move call 1's answers: "does this text address an automated system?" The raw probabilities are stored as facts. | "Pressure: strong · Asks to change bank details: very likely · Text addresses an automated system: likely" |
| **Rules (ZEN)** | ZEN evaluates the **pinned policy** over the facts. Tolerances and thresholds live in the policy, and each check table emits an explicit pass or fail row per rule ID. Returns findings with evidence, the tolerances it applied, and the trace. | "19 checks: 16 pass, 2 warn, 1 block (policy v4)" |
| **Investigate** (agent, only on investigable exceptions) | A bounded Claude agent tries to explain or resolve the exception with read-only tools. The result is re-checked by deterministic code (§6). | "Searched 4 open POs · found GRN-883 posted yesterday (+40 units) · re-matched: now fully received" |
| **Decide** | Pure Python, and the single place where findings are merged: the worst outcome wins, plus caps from P5, the two badges (Owed / Safe), `attempted[]`, the question, GL coding (from the PO line, or a Jev Choice over the vendor's past GL accounts for non-PO invoices), and `due_date` and the discount deadline using the policy's date basis. Computes `amount_payable` and `amount_held` from the applied tolerances. | "HOLD · Controller · Verify bank by call-back" |
| **Explain** | Claude Haiku turns `facts` and findings into 3 plain sentences, plus a vendor email for HOLD/REJECT. A money guard checks that every figure in the text appears in the facts; otherwise a template is used. | "Summary written (4 of 4 figures verified)" |
| **Commit → Post** | In one transaction: advisory locks on `po_id` **and** `(vendor_id, invoice_number_norm)`, a duplicate re-check, a PO balance re-check, then the `billed_ledger` write. A partial unique index on `(vendor_id, invoice_number_norm) WHERE state <> 'reversed'` is the final guard. Only the latest proposal for an invoice can be acted on, because older ones are marked superseded. Then the bill is written to the mock ERP, read back and compared. | "Bill B-1042 posted, read-back OK, PO-4404 remaining $3,000" |

### 3.2 Outcomes

| Outcome | Meaning | Owner | Example question |
|---|---|---|---|
| `AUTO_APPROVE` | Owed, safe to pay, every check passes | none | none |
| `REVIEW` | Probably owed; a person confirms one thing | AP clerk / manager | "Pay $6,000 for the 60 units received and hold $4,000 until the rest arrives?" |
| `HOLD` | Can't decide yet; information or verification needed | Procurement / vendor / controller | "Call Acme on the vendor-master number (+91 …) to confirm the new account?" |
| `HOLD · awaiting_receipt` | Part of the invoice is billed ahead of delivery. The held amount releases on its own when a matching goods receipt is posted. | Receiving | "Release automatically when the remaining 40 units are received?" |
| `REJECT` | Must not be paid | AP manager | "Send the drafted notice for the duplicate of INV-0042?" |
| `NOT_AN_INVOICE` | A statement or another document type | AP clerk | "File this statement for reconciliation?" |
| `APPLY_CREDIT` | A credit note | AP clerk | "Apply the $500 credit against Acme's next payment?" |

The decision card shows **Owed: yes / partly / no** and **Safe to pay: yes / no** side by side.

### 3.3 Event contract

`GET /api/runs/{id}/events` is SSE. It polls `run_events` by `seq` every 250 ms, sends a heartbeat every 15 s, sets `X-Accel-Buffering: no`, and resumes from `Last-Event-ID`.

```json
{"seq": 9, "stage": "judge", "type": "stage.finished", "status": "block", "ts": "…", "duration_ms": 112,
 "summary": "Requests bank change (p=0.97) · pressure language (p=0.94)",
 "data": {"model": "jev-1.14.0", "answers": {...}}}
```

### 3.4 Runs, waiting and corrections

- `process_invoice` ends when it writes a proposal. "Waiting for a person" is a run status with an `sla_due_at`, not a parked workflow, so a deploy never strands long-lived workflows.
- A human action starts `apply_decision`. Each decision is consumed exactly once.
- **Correcting a field** or **linking a PO** starts a *new run* (`parent_run_id`, `trigger=correction`) with the human's value pinned as Verified. The original proposal is never changed.
- **Policy replay** re-runs the **deterministic stages** (match → screen → rules → decide) from the stored extraction and a **snapshot of master data and the ledger as of the original run**, under a draft policy. It writes shadow proposals only and has no money effects (§7).
- **Event-driven re-matching:** when a new goods receipt is posted or a vendor reply arrives (classified by Jev as confirms / sends credit note / disputes / other), matching `awaiting_receipt` / `awaiting_vendor` cases start a child run automatically.
- A scheduled job escalates cases that pass their SLA and logs an event for it.

---

## 4. Field evidence

Published work shows that self-reported LLM confidence and logprobs are poorly calibrated, while disagreement between *structurally different* extraction calls is a strong error signal (ExtractConf, arXiv 2606.24420; ConfBench, arXiv 2608.01792). Clearline's two readings differ in both model and mode: Read A is Sonnet with vision and a strict schema, and Read B is Haiku with text-only citations. That pair gives us exactly that signal.

| Level | Condition |
|---|---|
| **Verified** | Located on the page (Read B's citation or A's quote), **and** confirmed independently: A and B agree, the arithmetic reconciles, or it matches master data (tax ID, existing PO) |
| **Read** | Located on the page only |
| **Uncertain** | Not located, A and B disagree, or the field failed validation |

- **Disagreement resolution:** if A and B disagree on a number, keep whichever reading reconciles with the lines and mark it Verified. If neither reconciles, a high-resolution crop is re-read once with Sonnet. The event log records the step, e.g. "attempted resolution: arithmetic chose 8,450". This is how E4 works.
- **Text-layer tampering:** A and B both depend on the PDF text layer, so a forged text layer would fool both. The render check (OCR of the visible pixels compared with the text layer) is the independent check. A mismatch triggers DOC-02 → HOLD.
- **Gate:** any Uncertain decision field (vendor, invoice number, total, currency, PO, and invoice date whenever it changes the due date or discount) caps the outcome at REVIEW. That field becomes the question.
- **Calibration:** the eval suite runs every scenario through `degrade()` at increasing noise levels. It plots touchless rate against field-error rate, and the dashboard shows the curve.

---

## 5. Where Jev fits

**What Jev is.** Jev is TypeSafe AI's "System One" model, launched in September 2026. You send it a state plus typed questions: Noul (a yes/no probability), Choice (up to 255 labels) and Score (2–10 ordered levels). It answers all of them in one parallel pass in about 70–500 ms, at $0.042 per million input tokens, with free output. The answers are **calibrated probabilities** and always fit the schema. It does not generate text.

**What it is bad at** (TypeSafe documents this itself as "jaggedness"):
- It cannot do arithmetic or date comparison.
- It reads indirection and double negatives too literally.
- Irrelevant context makes it worse.
- It is **not yet hardened against adversarial input**.
- A wrong answer can still be valid under the schema.

**Rules for Clearline:**
- Jev answers *judgment questions about text*. It never compares numbers, dates or money.
- Text is **redacted** before it is sent: account numbers become `[BANK_ACCOUNT]` and amounts become `[AMOUNT]`. This follows the no-numbers rule, and it reduces what leaves our system, since TypeSafe's zero-data-retention option is enterprise-only.
- The injection detector runs in its **own request**, so injected text cannot shift the other answers.
- Wherever a Jev answer would resolve an ambiguity, the outcome is capped at REVIEW unless a deterministic check agrees (P5).

| Use | Question (type) | State sent | Why Jev and not code or Claude |
|---|---|---|---|
| Intake triage | `email_kind` Choice: invoice / statement / dunning / bank-change notice / vendor reply / other | Redacted email | Sends each email to the right path before we spend extraction calls on it |
| Document type | `doc_type` Choice: invoice / credit_note / statement / proforma / purchase_order / other | First-page text | Confirms Read A's label. If they disagree, the field is Uncertain. |
| Line kind | `line_kind` Choice per line | One line description | Keeps freight and fees out of PO pairing |
| Line ↔ PO-line pairing | `same_item` Noul per candidate pair (after the spec-token veto) | Two descriptions | Calibrated probabilities feed the Hungarian assignment, so we don't need an embedding pipeline |
| Vendor identity (only when ambiguous) | `same_entity` Noul | Printed name + candidate legal name | Handles "ACME STL PVT" vs "Acme Steel Private Limited". Caps the outcome at REVIEW. |
| Non-PO GL coding | `gl_account` Choice over this vendor's past GL accounts | Line descriptions + vendor category | Picks from a fixed list, so it can't invent an account. Confidence below the bar → REVIEW. |
| BEC pressure | `pressure` Score: none / mild / strong | Redacted email body + invoice notes | Urgency and secrecy are common signals of payment-redirection fraud (BEC) |
| Payment change | `requests_bank_change` Noul | Same | Supports PAY-01. The deterministic bank comparison remains the main control. |
| Prompt-injection alarm | `instructs_automation` Noul, **separate request** | Redacted invoice text | Shown in the UI as an alarm, not a defence. The defence is the architecture: no tools, schema-bound output, and no model can approve. |
| Vendor reply | `reply_intent` Choice: confirms / sends correction / sends credit note / disputes / other | Redacted reply | Drives the `awaiting_vendor` loop |
| Reviewer note | `note_kind` Choice: one-off / generalisable rule | The reviewer's reason text | A generalisable note suggests "teach the agent" (§7), which connects human review to policy |

**Thresholds.** Thresholds are set **per question and per backend** in the policy, and tuned on the eval set. Each run records `decision_backend`. Pin `jev-1.14.0` and log `response.model`. A Noul near 0.5 means "can't tell", not "medium", and maps to Uncertain.

**Access and fallback.**
- TypeSafe paused console signups on 22 Sep 2026. The **primary route is OpenRouter** (`typesafe/jev-1.14`, `POST /api/v1/systemone`), which needs no waitlist. The SDK works there by changing the base URL. Only official endpoints are used; copycat "Jev" sites exist.
- Clearline calls Jev through a `DecisionPort` interface with two implementations:
  1. `typesafe-sdk`, using OpenRouter or TypeSafe directly.
  2. TypeSafe's **`system-one-adapter`** (`llm_answer_mode="probabilities"`), which answers the same questions with Claude Haiku. Haiku's probabilities are self-reported and less well calibrated, so this backend has its own thresholds and the stage shows as "degraded".
- **Jev is presented as a measured experiment, not a pillar.** The eval compares accuracy, calibration (reliability diagrams) and latency for both backends on every question. In the interview the claim is "we measured whether it earns its place", not "we used the new model".

---

## 6. The investigation agent

Zamp's own description of an AP agent is one that "gathers the records needed to test possible explanations" and searches eligible open orders "without inventing a match". It recommends starting with read-and-propose permissions. Clearline follows that. The pipeline is a fixed workflow, and **one bounded agent** runs only where the path is genuinely open-ended.

| | |
|---|---|
| **When** | After Rules, before Decide. Only if the outcome isn't AUTO_APPROVE **and** a failing rule is investigable: PO-01 (no or inferred PO), MAT-02 (short receipt), PO-03 (overrun), DUP-02 (soft duplicate), PAY-01/04 (bank), VEN-01 (vendor ambiguous). |
| **How** | Pydantic AI agent on Claude Sonnet 5.5 with strict tool use, running on DBOS durable execution. Every tool call is a durable step, and each one streams to the live view as an "agent step". |
| **Tools** (read-only, scoped to the resolved vendor) | `search_open_pos(vendor, amount, lines)` · `get_grns(po, since)`, including receipts posted after the invoice · `vendor_invoice_history(vendor, n)` · `bank_change_history(vendor)` · `find_similar_invoices(invoice)` · `get_vendor_contact(vendor)` (master data only) · `draft_vendor_email(facts)`, which only drafts; a human sends |
| **Input** | `facts` plus the Jev flags and the failing findings. **No raw invoice or email text.** If it needs text, it gets quoted fields only, so injected text can't steer the tool loop. |
| **Budget** | ≤ 6 tool calls and ≤ 30 s, enforced by the harness |
| **Output** | Typed `Investigation{hypotheses[], evidence_refs[], proposed_resolution, question}` |
| **Re-check** | Deterministic code validates every proposed resolution. If the agent finds a PO, the PO is re-matched. If it finds a later GRN, receipt quantities are recomputed. The agent's result fills `attempted[]` and sharpens the question. It **cannot** raise the outcome above REVIEW. |

**Examples:**
- **E2:** the agent finds GRN-883, posted yesterday with +40 units. After re-matching, the invoice is fully received and goes to REVIEW: "Approve full payment? The remaining units arrived yesterday (GRN-883)."
- **E4:** the agent searches the vendor's 4 open POs. One matches 3 of 3 lines, and the question becomes "Confirm PO-4401?"

**Case copilot.** The same tools sit behind a chat panel in the case view, so a reviewer can ask follow-ups ("Has Acme short-shipped before?"). Answers cite the tool results they came from.

---

## 7. Rules and policy (ZEN decision tables)

**The facts contract.** Python computes **raw measurements only**, with no thresholds applied:
- per line: `price_delta_abs`, `price_delta_pct`, `qty_billed`, `qty_received_net`, `qty_ordered_net`, `line_kind`
- per invoice: `freight_amount`, `po_cumulative_after`, `po_total`, `bank_matches_verified`, `days_since_bank_change`, `first_payment`, `sender_domain_distance`, `render_matches_text`, `uncertain_fields[]`, `vendor_resolution_method`
- Jev results as raw probabilities: `jev.requests_bank_change`, `jev.pressure`, `jev.instructs_automation`, …

Money comparisons arrive as precomputed Decimal results, so ZEN never does floating-point money arithmetic.

**The policy** is a JDM decision graph that contains **every tolerance and threshold**:
1. **Check tables.** There is one per rule. Each emits an explicit **pass or fail row per rule ID** using ZEN 2.0's per-column collect, so passes appear in the trace. Each row carries `rule_id`, `status`, `outcome`, `owner`, `action`, the message template, and the tolerance it applied.
2. **Routing table.** It maps an outcome plus an amount to an owner and an SLA.

ZEN returns findings plus the trace. **Python `decide()` is the single place where findings are merged**: worst outcome wins, plus the P5 caps. It then computes `amount_payable` and `amount_held` from the tolerances ZEN returned.

| ID | Check | On failure |
|---|---|---|
| DOC-01 | Document type is invoice | statement → NOT_AN_INVOICE · credit note → APPLY_CREDIT (must reference the original invoice, goes through duplicate checks, reverses ledger lines) |
| DOC-02 | Visible (rendered) value matches the text layer for payment-critical fields | **HOLD, controller** (possible tampering) |
| EXT-01 | Critical fields present | HOLD + vendor email asking for the missing field |
| EXT-02 | No decision field is Uncertain | REVIEW, with that field as the question |
| EXT-03 | Reconciles in either tax mode (± $0.01 per line) | REVIEW |
| VEN-01/02 | Vendor resolved without ambiguity, status approved. A Jev-only resolution is capped at REVIEW. | HOLD / REVIEW / REJECT |
| PO-01/02 | PO resolved, open, same vendor, same currency. An inferred PO is capped at REVIEW. | REVIEW / HOLD / REJECT |
| PO-03 | `po_cumulative_after ≤ po_total + tolerance` | HOLD, short-pay the remainder |
| MAT-01 | Price delta within `max(pct × ext, abs)`. A favourable variance passes. | REVIEW + short-pay at the PO price |
| MAT-02 | `qty_billed ≤ qty_received_net` | **HOLD · awaiting_receipt** for the difference, with auto-release |
| MAT-03 | Freight within the allowance (per vendor) | REVIEW |
| DUP-01/02 | No hard duplicate (also across vendor records sharing a tax ID or bank account) / no soft duplicate | REJECT / REVIEW |
| PAY-01 | Invoice bank details = verified account | **HOLD, controller only. Call back on the number in the vendor master.** |
| PAY-02 | Sender domain is the vendor's (no lookalike, Reply-To consistent) | HOLD, controller |
| PAY-03 | `jev.requests_bank_change < t1` and `jev.pressure < strong` | HOLD, controller |
| PAY-04 | Not the first payment to this vendor, and no bank change in master data in the last N days | HOLD, call-back required even when the bank details match |
| SEC-01 | `jev.instructs_automation < t2` | Flag + REVIEW (a document that tries to instruct the system is never auto-approved) |
| TAX-01 | tax = rate × base (± rounding) · tax ID checksum valid | REVIEW |
| NPO-01 | Non-PO amount within the vendor's usual range | REVIEW, budget owner |
| APR-01 | Amount > AP manager limit | REVIEW, controller sign-off |

**Locked tables.** PAY-\*, DOC-02, DUP-01 and SEC-01 can't be changed through "teach the agent". Only an admin can change them, through a reviewed change.

**Segregation of duties** (enforced on the server, in `apply_decision`):
- The person who verifies a bank account can't approve payment on the same invoice.
- The person who drafts a policy change can't approve it.
- The person who corrects a field can't approve that run.

### Teach the agent

1. A reviewer writes guidance in plain English, e.g. "Acme always bills freight separately; up to $50 is fine." Jev's `note_kind` also suggests this flow when a review note looks like a general rule.
2. Claude Sonnet returns a **typed policy patch** (structured output), e.g. `{table: "freight_allowance", op: "upsert_row", match: {vendor_id: "V-1001"}, set: {abs: 50}}`. It never writes raw JDM. The patch is validated against the table's column types and rendered into JDM.
3. **Gates:**
   - ZEN static checks and assertions run on the patched policy.
   - The **golden scenario suite** must still pass: E1 still HOLDs, B3 still REJECTs, and so on.
   - The patch can't touch the locked tables listed above.
4. **Replay:** the deterministic stages re-run over the last N invoices using the run-time snapshots. The result is shown as a plain-English diff, e.g. "3 past REVIEWs would auto-approve. 0 cases a human held or rejected would now auto-approve." That second number is the regression check. The JDM editor stays available as an "advanced" view.
5. A *different* person (the controller) approves. The version is bumped, and new runs pin the new version.

---

## 8. AI gateway and guardrails

| Call | Model | Mode | Guardrails |
|---|---|---|---|
| Read A | Claude Sonnet 5.5 (exact ID resolved via the Models API and pinned) | PDF document or image block + `output_config.format` JSON schema, low effort | Schema-constrained. Enum values compared case-insensitively. Never invents an invoice or PO number. No tools. |
| Read B | Claude Haiku 5.5 | Custom-content document (line map) + `citations.enabled`, `field: value` prompt (not JSON) | Every header value must carry ≥ 1 citation and appear in its cited text, otherwise Uncertain |
| Targeted re-read | Claude Sonnet 5.5 | High-resolution crop of the disputed region | Runs only when A ≠ B and arithmetic can't decide |
| Judge | `jev-1.14.0` (OpenRouter) → adapter on Haiku | System One typed questions over redacted text | Results are facts only. P5 caps apply. Injection detector runs in a separate request. |
| Investigate | Claude Sonnet 5.5 via Pydantic AI + DBOS | Strict tool use, read-only tools, typed output | ≤ 6 calls and ≤ 30 s. Sees facts, not raw text. Every proposal is re-checked by deterministic code. Outcome capped at REVIEW. |
| Explain / vendor email | Claude Haiku 5.5 | Plain text from `facts` only | Money guard + template fallback. A human always sends. |
| Policy translator | Claude Sonnet 5.5 | JSON schema = typed policy patch | Static checks, golden suite, replay, and approval by a different person |

The gateway, `ai/gateway.py`, handles:
- **Pinned model IDs.**
- **Retries with jitter** on 429, 5xx and 529.
- **Prompt caching where it actually applies.** A cache hit needs an identical prefix on the same model, so caching can't be shared between Read A and Read B. What we cache is each call type's static system prompt, schema and instructions (≥ 512 tokens) across invoices, plus the document block when the same model is asked about the same document again (targeted re-read, investigator, copilot). `cache_read_input_tokens` is shown on the cost panel.
- **A trace span per call** with tokens, latency and cost.
- **A daily spend cap.**
- **Fixture record/replay**, so the scripted demo and CI cost $0 and run deterministically. The UI shows a **Live / Fixture** toggle.

**Prompt injection.** Invoice and email text are data.
- The reading models have no tools.
- The investigator's tools are read-only, and it never sees raw text.
- Outputs are schema- or citation-bound.
- The narrator only sees `facts`.
- No model output can approve anything.

Jev's `instructs_automation` makes an attempt *visible*, which is useful in the demo. It is a detector, not the defence.

### Unlimited OCR 2: considered, not used

Baidu released Unlimited OCR 2 on 27 Oct 2026: 3.34B parameters, MIT licence. We left it out because:
- Its advance, SR-SWA, targets documents with hundreds of pages. Invoices are 1–3 pages.
- It outputs Markdown and layout, not key-value fields.
- It is CUDA-first, with a custom SGLang wheel and `trust_remote_code`. On a Mac it only runs through patched llama.cpp GGUF builds.
- Its benchmarks are self-reported, there is no invoice-specific benchmark, and a hands-on test reported digit misreads.

Claude reads invoices directly. The `Reader` interface leaves room for a local model for on-prem customers.

---

## 9. Domain model

```
Vendor ─< VendorAlias   Vendor ─< VendorBankAccount (verified_at, verified_by, callback_phone)   Vendor.domains[]
Vendor ─< PurchaseOrder ─< POLine (gl_account, cost_center) ─< GRNLine >─ GoodsReceipt
Document (sha256, channel, email headers) ─ Invoice ─< InvoiceField (versioned) , ─< InvoiceLine (line_kind)
Invoice ─< Run (parent_run_id, trigger, policy_version, model_versions) ─< RunEvent
Run ─ Facts (raw measurements) ─ MasterSnapshot ─ [Investigation] ─ Proposal (immutable, superseded_by) ─< Decision (human)
POLine ─< BilledLedger      Policy (version, jdm json, text_md, approved_by)      AuditLog (append-only)
```

| Table | Notable columns |
|---|---|
| `documents` | `sha256 UNIQUE`, mime, pages, text_layer, channel, `email_from`, `email_reply_to`, `email_subject`, `line_map jsonb` |
| `invoices` | document_type, invoice_number, `invoice_number_norm`, vendor_id, po_id, currency, subtotal, tax, freight, total, tax_mode, terms, due_date, discount_terms, remit_bank |
| `invoice_fields` | field, value, evidence, reasons[], `readings{a,b}`, citation_blocks[], page, bbox, version, set_by |
| `invoice_lines` | line_kind, qty, uom, unit_price, amount, po_line_id, pair_p |
| `runs` | parent_run_id, trigger (ingest / correction / policy_replay / grn_posted / vendor_reply), status, sla_due_at, policy_version, model_versions, `decision_backend` (jev / adapter), mode (live / fixture), cost_usd |
| `run_events` | run_id, seq, stage, type, status, ts, duration_ms, summary, data (append-only) |
| `facts` | run_id, `facts jsonb` (raw measurements, no thresholds applied), `master_snapshot jsonb` (vendor, PO, GRN and ledger state at run time). This is the replay input. |
| `investigations` | run_id, steps[] (tool, args, result ref, ms), hypotheses[], proposed_resolution, question, recheck_result |
| `proposals` | outcome, sub_status (awaiting_receipt / awaiting_vendor), owed, safe_to_pay, recommended_action, owner_role, question, attempted[], amount_payable, amount_held, gl_coding[], narrative, findings, zen_trace, `superseded_by` |
| `decisions` | proposal_id, actor, role, action, reason (required), amount, decided_at |
| `billed_ledger` | po_line_id, invoice_id, vendor_id, invoice_number_norm, qty, amount, state (billed / reversed). Partial unique index on `(vendor_id, invoice_number_norm) WHERE state <> 'reversed'`. |
| `policies` | version, jdm, text_md, approved_by, approved_at, replay_summary |
| `audit_log` | actor, action, entity, before, after, at. UPDATE, DELETE and TRUNCATE are revoked. |

---

## 10. Human-in-the-loop and UI

### Screens

1. **Live run**
   - Scenario picker or upload, with a story card for the scenario.
   - Stage rail: pending, running, pass, warn, block, degraded, with timings.
   - Streaming event log.
   - PDF with bbox highlights that appear as fields are grounded, coloured by evidence level.
   - Jev answers shown in plain words ("Asks to change bank details: very likely"), with probability bars under "details".
   - **Agent steps panel:** each investigation tool call as it happens, with its result and the deterministic re-check.
   - Checklist that ticks off live.
   - Decision card: Owed / Safe badges, the question, the recommended action.
2. **Case view**, laid out as: PDF | fields (Verified / Read / Uncertain, with A vs B readings on hover) | invoice / PO / GRN table with offending cells highlighted | rule checklist with the ZEN trace. A header strip shows the summary, "what I tried", the question and the action. Run lineage and audit timeline sit below. A **case copilot** chat panel answers follow-up questions using the investigator's read-only tools.
3. **Queue**: sorted by due date, then dollars held. Filters by role. Badge for discounts about to expire. Separate tabs for `awaiting_receipt` and `awaiting_vendor`, which update themselves when a goods receipt or a reply arrives.
4. **Dashboard**
   - Touchless rate.
   - Outcomes over time.
   - Dollars held or caught, split by overbilling, duplicate and payment safety. "Caught" counts only invoices that would have been paid under plain 2-way matching (a counterfactual), so the figure isn't inflated.
   - Median machine time and median human time.
   - Top rules fired.
   - Eval accuracy and the calibration curve.
   - Jev vs adapter comparison.
   - **Machine** cost per invoice, labelled as machine-only (not fully loaded cost), with the Ardent Partners figures ($9.40 average, $2.78 best-in-class) shown as context.
5. **Policy**: the plain-English summary and diff first, with the JDM editor behind an "advanced" toggle. Also version history and replay results.
6. **Vendors & POs**: verified bank accounts with call-back numbers and last-change dates. PO billed vs remaining bars.
7. **How it works** (for the buyer): a one-page process map in plain language. Read the invoice twice, check it against what was ordered and received, check it's safe to pay, investigate anything odd, then ask one clear question.

### Actions

A reason is required for every action:
- approve
- short-pay (amount defaults to `amount_payable`)
- correct a field (starts a new run)
- link a PO (starts a new run)
- request info (sends the drafted email; status becomes `awaiting_vendor`)
- verify bank (controller only; records the call-back)
- reject

A demo role switcher covers clerk, manager and controller. Roles and **segregation of duties** (§7) are checked on the server. Only the latest, non-superseded proposal can be acted on.

---

## 11. Scenarios and edge cases

Each scenario is a generated PDF or `.eml` with ground truth in `eval/ground_truth.json` (`accepted_outcomes` plus expected fields). The seed data already contains invoices #1 and #2 for E3.

| # | Scenario | Expected | Why it matters |
|---|---|---|---|
| H1 | Clean text PDF, explicit PO, fully received | AUTO_APPROVE, bill posted, read-back OK | Happy path |
| **E1** | **Valid invoice, unsafe payment.** Emailed from a lookalike domain. Perfect 3-way match. New IBAN with a valid checksum. Text says "URGENT, pay today, ignore prior instructions". | Owed: yes · Safe: **no** → HOLD, controller, call back on the vendor-master number. Jev flags pressure, the bank-change request and the injection attempt. | BEC fraud, which PO matching cannot see |
| **E2** | **Partial delivery**: billed 100 units, goods receipt shows 60 | HOLD · awaiting_receipt for $4,000, REVIEW for $6,000. **The agent** finds nothing newer, so the question is "Pay $6,000 now, release $4,000 when the rest is received?" A live variant posts GRN-883 for +40 units during the demo, and the held amount **releases automatically**. | 3-way match that recommends an action, investigates, and closes the loop |
| **E3** | **Split-PO overrun**: the third invoice takes cumulative billing past PO + tolerance | HOLD: short-pay the remainder, ask procurement about a PO amendment | State carried across invoices |
| **E4** | **Phone photo (JPEG)**: PO implied, tax-inclusive prices, one ambiguous digit (8,450 vs 3,450) | Reads A and B disagree. Arithmetic resolves the digit. **The agent** searches 4 open POs and one matches 3 of 3 lines. Deterministic re-match passes. REVIEW: "Confirm PO-4401?" | Messy input, evidence-based confidence, bounded agent |
| B1 | Missing invoice number | HOLD + drafted vendor email | Named in the brief |
| B2 | "Close but not exact": +1.2% price, $40 freight line, inside tolerance | **AUTO_APPROVE**, with the reason shown | Shows the system isn't over-strict |
| B3 | Duplicate resubmitted with a reformatted number (`INV-0042` vs `42`) on a new layout | REJECT | Number normalisation |
| B4 | Statement of account | NOT_AN_INVOICE | No false rejection notice sent |
| B5 | "Teach the agent": the freight rule from §7 | Typed patch → static checks + golden suite → replay ("3 would auto-approve, 0 human-held cases would now pay") → B2-style cases auto-approve | Matches how Zamp describes editing an operating procedure |
| B6 | Tampered text layer: the visible IBAN differs from the hidden text-layer IBAN | DOC-02 → HOLD, controller | Both reads agree, and the render check still catches it |

The 5-minute video covers H1, E1 and E2. The live interview covers all four edge cases, plus B5 if time allows.

---

## 12. Evaluation

`eval/run.py` runs every scenario through the real workflow, in fixture mode (CI) or live mode. It reports:
- Field accuracy.
- **Evidence calibration**: Verified fields that turned out wrong, which should be 0.
- Decision accuracy against `accepted_outcomes`.
- Dollars caught.
- Latency and cost per invoice.
- **Jev vs adapter-on-Haiku**: accuracy, calibration (reliability diagram) and latency for each judgment question.

A noise sweep with `degrade()` produces the gate curve. Results are stored and shown on the dashboard.

---

## 13. Non-functional

| Concern | Approach |
|---|---|
| Security | Upload limits and MIME sniffing. Files stored outside the web root. Keys in env only (`ANTHROPIC_API_KEY`, `TYPESAFE_API_KEY` / `OPENROUTER_API_KEY`). Append-only audit. Server-side role checks. |
| Data handling | Page images and text go to Claude. Only **redacted** text goes to Jev (account numbers and amounts removed), because TypeSafe's zero data retention is enterprise-only. No secrets go in prompts. Data residency is covered by the `Reader` port (local model, or Azure Document Intelligence / Textract) and Claude's zero data retention option. |
| Idempotency | sha256 at intake. Workflow ID = run ID. Each decision is consumed once. |
| Concurrency | At commit, advisory locks are taken on `po_id` and on `(vendor_id, invoice_number_norm)`. The duplicate check and PO balance are re-checked inside the transaction, with a partial unique index as the final guard. If anything changed, the case goes back to review. |
| Observability | OTel spans for every stage and model call, sent to Langfuse. Durations and cost appear on the event log. `/health`. |
| Cost (measured, not guessed) | Per clean invoice: 1 Sonnet call, 1–2 Haiku calls, 2 Jev calls. Exceptions add the investigator (≤ 6 tool calls). The static prompt prefix is cached per call type. Cost and cache reads are shown per run and on the dashboard. |
| Demo reliability | Single host (glibc images for ZEN), warm-up ping, fixture default for scripted runs, reset-to-seed button. |

---

## 14. Repository layout and sources

```
clearline/
  apps/web/              Next.js 16 + shadcn/ui + jdm-editor + react-pdf
  services/core/app/
    api/                 routes, SSE
    workflow/            process_invoice, apply_decision, scheduler
    domain/              PURE: money, normalise, evidence, vendor, po, pairing, match, ledger, screen, facts, decide
    policy/              ZEN loader, JDM files, replay
    ai/                  gateway, read_a, read_b (citations), reread, decision_port (jev | adapter), redact, narrator, money_guard, fixtures/
    agent/               investigator (Pydantic AI + DBOS), read-only tools, recheck, copilot
    repo/                SQL per aggregate
  migrations/  seed/  scenarios/ (generator + PDFs + .eml)  eval/
  docs/                  ARCHITECTURE, DECISIONS, PROCESS_MAP, DEMO_SCRIPT
  references/            cloned prior art (not shipped)
```

**Prior art studied** (our code is our own; ideas are credited in DECISIONS.md):
- **ledgic**: AI reads, Decimal decides; payable_qty / held amount; money guard.
- **recon-agent**: confidence from validation penalties and from two readings agreeing; the agent may only escalate; the event log as the integration seam; a degradation generator.
- **zamp-invoice-workflow**: worst-wins findings; billed ledger across split invoices.
- **APilot**: owner and action routing.
- **invo-match**: a checklist that records passes; per-PO tolerance.

**Sources:**
- [Claude citations](https://platform.claude.com/docs/en/build-with-claude/citations)
- [Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) (incompatible with citations, hence Reads A and B)
- [Claude models](https://platform.claude.com/docs/en/models/overview)
- Jev: [zeke/jev notes](https://github.com/zeke/jev), [flaviocopes deep dive](https://flaviocopes.com/jev/), [Python SDK guide](https://flaviocopes.com/jev-python/), [OpenRouter Jev](https://openrouter.ai/docs/guides/community/jev), [system-one-adapter](https://github.com/typesafe-ai/system-one-adapter-python)
- Rules: [GoRules ZEN](https://github.com/gorules/zen), [JDM editor](https://gorules.io/editor)
- Workflow and realtime: [DBOS HITL](https://docs.dbos.dev/ai/hitl), [Trigger.dev waitpoints](https://trigger.dev/human-in-the-loop-ai-agents), [Inngest HITL](https://www.inngest.com/docs/ai-patterns/human-in-the-loop), [Mastra suspend/resume](https://mastra.ai/docs/workflows/suspend-and-resume), [Pydantic AI framework comparison](https://pydantic.dev/articles/best-python-ai-agent-frameworks)
- Claude: [prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching), [PDF support](https://platform.claude.com/docs/en/build-with-claude/pdf-support), [vision coordinates](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates)
- Agents: [Pydantic AI on DBOS](https://ai.pydantic.dev/durable_execution/overview/), [Anthropic: building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- Reference AP architectures: [Temporal invoice processor (Claude + HITL)](https://temporal.io/code-exchange/temporal-invoice-processor-mcp-claude-human-in-the-loop), [AWS Bedrock invoice sample](https://github.com/aws-samples/sample-ai-invoice-processing-with-amazon-bedrock)
- Confidence: [ExtractConf](https://arxiv.org/abs/2606.24420v1), [ConfBench](https://arxiv.org/abs/2608.01792)
- Zamp's own AP framing: [AI agents for AP](https://www.zamp.ai/blogs/ai-agents-for-accounts-payable), [Invoice processing agents](https://www.zamp.ai/blogs/ai-agents-automate-invoice-processing)

---

## 16. Learning loop: the system gets cheaper and more automatic with use

Most of a company's invoices come from the same few dozen vendors, using the same layouts and billing the same items. Today Clearline spends the same model calls on the 50th Acme invoice as on the first, and asks a person the same question every time a known exception repeats. The learning loop fixes that.

The rule: **Clearline learns to read cheaper and to ask fewer questions. It never learns to pay more loosely on its own.** Anything that would change a payment decision goes through the governed policy path (§7), and the safety rules are never learned.

### 16.1 What it learns

| # | Memory | Learned from | What it changes | Effect |
|---|---|---|---|---|
| L1 | **Layout templates**, per vendor and layout | Fields marked **Verified** on past invoices from that layout, with their word boxes | Reading moves down the reading ladder (§16.2) | Model cost per invoice moves towards $0. Latency drops from seconds to milliseconds. |
| L2 | **Vendor aliases** | A person confirming "ACME STL PVT = Acme Steel" | Vendor resolution becomes an exact match (`alias`), not a model judgment | Removes the P5 cap, so one fewer REVIEW |
| L3 | **Vendor item map** (vendor's description or SKU → our PO item) | SKU pairings, plus pairings a person confirmed | Line pairing becomes deterministic. No same-item questions. | Removes pairing questions and the cost of asking them |
| L4 | **Judgment cache** | Typed answers (line kind, document type) keyed on redacted text, question and model version | Repeated text isn't asked again | Most per-line judgment calls disappear for known vendors |
| L5 | **Reading hints**, per vendor | Corrections, e.g. "this vendor prints the PO as 'Your ref'" | Short notes added to the cached prompt prefix for that vendor's full reads | Fewer repeat corrections on the same vendor |
| L6 | **Decision patterns** | Repeated human decisions on the same exception | A *suggested* policy change, through teach-the-agent (§7) | Fewer repeat REVIEWs, with a person approving every change |

### 16.2 The reading ladder (L1)

Each layout is identified by a **fingerprint**: the static words that appear on almost every invoice from that vendor (labels such as "Invoice No:" and "Total Due (USD):"), with their positions rounded to a grid. A new invoice matches a known layout when its static words overlap the fingerprint's by at least 85% (Jaccard similarity). A template stores, for each field, the label it sits next to, where the value sits relative to that label, and the value's type. For line items it stores the table header and the column boundaries.

| Tier | When it applies | How the invoice is read | Model cost | Promotion and demotion |
|---|---|---|---|---|
| **T3 Full read** | New vendor, new layout, scan or photo, or any doubt | Read A + Read B (§3) | ~$0.007 (Gemini, §17) | After each Verified run, the template is learned or updated in **shadow mode**: it reads the invoice too, but nothing uses its output. |
| **T2 Template + cross-check** | Shadow template matched the full read on every decision field on **3 invoices in a row** | Template reads the invoice. A small model (Read B only) cross-checks it. | ~$0.001 | Promote after **10 more agreements and no human corrections** |
| **T1 Template only** | Promoted from T2 | Template reads the invoice. Deterministic checks confirm it: arithmetic reconciles, master data matches, render check on payment fields, and every value is located on the page. | $0 | **Audit sample:** 1 in 10 still gets the cross-check, so silent drift is caught. |

**Demotion is immediate and happens inside the same run.** Any of the following re-reads the invoice at T3 and drops the template one tier, logged as `attempted: template failed, full read`:
- the invoice doesn't reconcile;
- a value isn't found on the page;
- the fingerprint match falls below the threshold (the vendor changed its layout);
- the cross-check disagrees;
- a person corrects a field.

The invoice never fails; it just costs a full read. Scans and photos top out at T2, because OCR noise makes T1 too risky.

### 16.3 Guardrails on learning

- **Clean sources only.** Templates, aliases and item maps learn only from runs that:
  - came from the vendor's real channel (no lookalike domain, no PAY or SEC findings),
  - were posted and read back successfully,
  - and used fields that were Verified or confirmed by a person.

  An attacker can't train the system by sending invoices.
- **Never learned:**
  - Bank details always come from the vendor master and are checked every time.
  - The payment-critical render check (DOC-02) runs at every tier.
  - Duplicate checks and the locked policy tables (§7) stay as they are.
- **Learned decision patterns are suggestions only (L6).** For example: "Initech freight between $30 and $45 was approved 6 times out of 6, with the reason 'contract allows freight'. Suggest a freight allowance of $50 for Initech." It goes through the typed patch, static checks, golden suite, replay, and approval by a different person (§7). Locked tables are excluded. A suggestion needs **at least 5 consistent decisions, none contrary, from at least 2 different people**, so one person's habit can't become policy.
- **Everything learned is versioned and reversible.** Each artifact records the runs it learned from, its accuracy statistics, its tier history, and who approved it, where approval applies. A replay pins the template versions it used, so replays stay deterministic.
- **Isolated per company.** Nothing learned crosses between tenants.

### 16.4 Measuring it (the learning curve)

The dashboard gains a **Learning** panel:
- cost per invoice per week;
- touchless rate per week;
- share of invoices read at each tier;
- questions asked per 100 invoices;
- per vendor: tier, invoices seen, corrections, last demotion.

The eval suite gets a **repeat-vendor run**: 20 invoices from 3 vendors in date order. It reports cost and touchless rate at invoice 1, 5, 10 and 20, and fails if any field a template read as Verified was wrong.

**Demo moment.** Process Acme's 1st invoice: full read, $0.007, 4 s. Then process Acme's 15th: "Known layout (T1) · read in 40 ms · $0.000 · checks: arithmetic reconciles, PO matches, bank matches master". Then upload Acme's invoice in a **new layout**: "Layout changed → full read", which shows the system doesn't trust the template blindly.

**Expected economics** (to be measured, not promised). At 500 invoices a month with 80% from 40 recurring vendors, month 1 runs at about $3.50 in model cost. Once most recurring layouts reach T1/T2, it settles at about $1 a month. Model cost is already small next to people's time (about $9.40 per invoice industry average, §10). So the larger saving comes from L2, L3 and L6, which remove repeat questions.

### 16.5 Domain additions

| Table | Notable columns |
|---|---|
| `layouts` | vendor_id, fingerprint (anchor words + grid positions), first_seen_run, status (shadow / t2 / t1 / retired), agree_streak, audit_rate, version |
| `layout_fields` | layout_id, field, anchor_text, offset box, value_type, column ranges (lines), learned_from_runs[] |
| `vendor_item_map` | vendor_id, invoice_text_norm, sku, po_item_key, source (sku / human), confirmed_by, uses |
| `judgment_cache` | key (hash of redacted text + question + backend + model version), answer, hits, created_at |
| `reading_hints` | vendor_id, hint_text, source_correction_id, active |
| `policy_suggestions` | pattern (vendor, rule, measurement band, action), support_count, contrary_count, deciders[], proposed_patch, status |
| `learning_events` | append-only: promoted, demoted, learned, cache_hit, suggestion_made (also written to `run_events` for the live view) |

The pipeline gains one stage and changes another:
- A **Learn** stage after Commit. It updates layouts, aliases, the item map and suggestions from clean runs only.
- The **Read** stage chooses its tier first and shows it live, e.g. "Known layout · T1 · template v3".

---

## 17. Model providers: Gemini on Vertex AI (free-trial credit)

**Constraint.** The author has Google Cloud free-trial credit. Google's free-trial terms exclude partner models offered as a managed API, which **includes Claude on Vertex**. Gemini usage is covered. ([Google Cloud Free Program](https://docs.cloud.google.com/free/docs/free-cloud-features))

**Decision.** Gemini on Vertex AI becomes the default provider. Claude stays as an alternative behind the same `ModelPort`, for a paid Vertex account or an Anthropic key. Model IDs and prices are pinned in config and recorded on every run.

| Call | Gemini (default) | Price per million tokens (in / out) | Claude (alternative) |
|---|---|---|---|
| Read A (vision + strict JSON schema) | `gemini-3.8-flash`, low thinking, high media resolution for scans and photos | $0.75 / $3.75 until 31 Dec 2026, then $1.50 / $7.50 | `claude-sonnet-5-5` |
| Read B (line map, cited by line number) | `gemini-3.5-flash-lite` | $0.30 / $2.50 | `claude-haiku-5-5` with native citations |
| Targeted re-read (only when A ≠ B and arithmetic can't decide) | `gemini-4-argon` | $2 / $10, rising to $4 / $20 on 1 Dec 2026 | `claude-sonnet-5-5` |
| Narrator, judgment fallback (`system-one-adapter`, provider `gemini`), copilot | `gemini-3.5-flash-lite` | $0.30 / $2.50 | `claude-haiku-5-5` |
| Investigation agent (Pydantic AI, Google provider) | `gemini-3.8-flash` | as above | `claude-sonnet-5-5` |

**Changes this forces, and how we keep the guarantees:**
- **Read B citations.** Gemini has no Anthropic-style citations. Read B instead gets the numbered line map and must return `{field, value, line: int}` in a strict schema. Code then checks that the value appears on that line. This gives the same grounding guarantee, because code does the check rather than the model.
- **Independence of A and B.** Both reads now come from one model family, so the signal is weaker. We keep the input mode different (vision vs text), the output mode different, and the model tier different. Arithmetic reconciliation and master data stay the main confirmation. The eval reports how often A and B were wrong in the same way, so this weakness is measured rather than assumed.
- **Batch:** Vertex batch prediction for the 181-document benchmark and for backfills.
- **Caching:** Gemini context caching on the static system prompt and schema.

**Cost per full read (T3):** about $0.007. Read A is ~3k tokens in and ~1k out (≈ $0.006); Read B is ~1.5k in and 0.3k out (≈ $0.001). The credit expires 90 days after signup, so the credit is the limit, not the price. Every live call is still recorded as a fixture, so rehearsals and the scripted demo cost $0. The daily spend cap still applies.

**Auth:**
- Either a service-account JSON key (`GOOGLE_APPLICATION_CREDENTIALS`) plus `GOOGLE_CLOUD_PROJECT`, with `GOOGLE_CLOUD_LOCATION=global`,
- or a Vertex API key in express mode.

Keys live in `.env` and are never logged.

---

## 15. Review log

**v1 → v2** (first independent review):
- Gemini 2.5 Flash is deprecated, so the model was replaced.
- Docling was dropped for pdfplumber + RapidOCR.
- DBOS is no longer used for parked workflows; corrections start child runs instead.
- Confidence moved from invented thresholds to evidence levels.
- Added GL coding, a non-PO path, terms and due date, ERP post with read-back, statements and credit notes, and the one-question handoff.
- Hosting moved to a single host.

**v2 → v3** (requests from the author):
- Removed time-boxing.
- Moved to the Claude APIs.
- Added Jev and the tech-stack comparison.

**v3 → v4** (second independent review, with web verification):

| Issue | Fix |
|---|---|
| Policy replay couldn't work because facts had thresholds baked in | Facts are raw measurements. Every threshold lives in the policy. Replay re-runs the deterministic stages from snapshots of the run's data. A regression means "a case a human held or rejected would now pay". |
| P5 overstated: Jev pairing and vendor-identity answers sat on the approve path | P5 reworded. Model-resolved ambiguity is capped at REVIEW. Spec-token veto before pairing. |
| No agent, although Zamp sells AP *agents* | Bounded **Investigate** stage (Pydantic AI + DBOS, read-only tools, deterministic re-check) and a case copilot |
| Prompt-caching claim was wrong (caches can't be shared across models or prefixes) | Corrected: the static prefix is cached per call type, plus same-model re-asks |
| `supports_value` misused Jev; numbers were sent to Jev; injection detector shared a request | Replaced with a deterministic check. Text is redacted before it goes to Jev. The injection detector runs in its own request. |
| Read A and B both rely on the same text layer, so tampering fools both | DOC-02 render-vs-text check, plus an image path for photos |
| Duplicate race across different files; non-PO invoices had no lock | Locks on vendor + number, re-check at commit, partial unique index, superseded proposals |
| Missing controls | PAY-04 (first payment / recent bank change), segregation of duties, `awaiting_receipt` auto-release, recurring-invoice false positives, credit-note linking, invoice-date gate |
| "Worst wins" defined in two places; passes not shown | Merge happens only in Python `decide()`. Explicit pass rows per rule. Locked safety tables. ZEN assertions and the golden suite act as a CI gate. |
| Fallback calibration; jargon in the buyer UI | Thresholds per backend, plus a "degraded" mark. OpenRouter is the primary Jev route. Plain-language UI and a buyer process map. |

**v4 → v5** (requests from the author):

| Request | Change |
|---|---|
| "The system should learn over time, automate repeats and reduce cost" | §16 learning loop: layout templates with a T3 → T2 → T1 reading ladder (shadow mode, promotion on measured agreement, immediate demotion), vendor aliases, vendor item map, judgment cache, reading hints, and decision patterns turned into *suggested* policy changes. Learns from clean runs only. Safety rules are never learned. A learning curve on the dashboard and a repeat-vendor eval. |
| "Use the Vertex AI models covered by my free credit" | §17: Gemini on Vertex AI is the default (`gemini-3.8-flash`, `gemini-3.5-flash-lite`, `gemini-4-argon` for re-reads). The free trial excludes partner models such as Claude, so Claude stays as an alternative. Read B grounds by line number, checked in code. The weaker independence of A and B is measured in the eval. |
