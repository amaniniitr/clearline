# Clearline: vendor invoice in, reasoned decision out

Zamp ASA case study, PS-1. Clearline takes a vendor invoice (text PDF, scan, phone photo, or a forwarded `.eml`) and checks it against the vendor master, the purchase order, goods receipts and billing history. It then makes one decision that answers two questions:

1. **Do we owe this?**
2. **Is it safe to pay where they ask?**

Every step streams live. When a person is needed, they get one specific question, a list of what was already tried, and a recommended action. The system also learns: repeat vendors get cheaper and faster to read.

- Design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Decisions and deviations: [`docs/DECISIONS.md`](docs/DECISIONS.md)
- Demo: [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md)
- Plain-language process map: [`docs/PROCESS_MAP.md`](docs/PROCESS_MAP.md)

## Run it (macOS)

```bash
# one-time
brew install postgresql@16 && brew services start postgresql@16 && createdb clearline
cd services/core && uv sync && uv run playwright install chromium && cd ../..
cd apps/web && npm install && cd ../..
cp .env.example .env            # add VERTEX_API_KEY (Gemini on Vertex AI); optional without it, see below

# generate the scenario documents (PDFs, scan, phone photo, .eml)
cd services/core && PYTHONPATH=. uv run python -m app.scenarios.render && cd ../..

# start (two terminals)
scripts/api.sh                  # FastAPI + DBOS on :8000 (seeds master data on first start)
scripts/web.sh                  # Next.js console on :3000  → open http://localhost:3000
```

**No API key?** The system still runs.
- Saved model responses in `services/core/app/ai/fixtures/` are replayed for the scenarios.
- For anything new, an offline text-layer reader and heuristic judge take over. Both are marked "degraded".

**Tests**
- `PYTHONPATH=. uv run python -m app.eval.run --reset --series` (in `services/core`) runs every scenario, scores it against ground truth, and runs the 14-invoice learning series. Results go to `eval/latest.json`, which the dashboard shows.
- `uv run python -m app.eval.ui_smoke` drives the console in a real browser and fails on console errors.

## What's inside

| Layer | Choice |
|---|---|
| API and workflow | Python 3.12, FastAPI, DBOS Transact (durable steps) on Postgres 16 |
| Reading | Gemini on Vertex AI. Read A: `gemini-3.8-flash` (sees the page, strict JSON schema). Read B: `gemini-3.5-flash-lite` (numbered text lines, cites a line number). Word boxes come from pdfplumber, or RapidOCR for scans and photos. |
| Verification | Field-by-field agreement, grounding on the page, arithmetic reconciliation (tax inclusive or exclusive), and a render check that compares visible pixels with the hidden text layer |
| Policy | GoRules ZEN decision tables compiled from `app/policy/catalog.py`. All tolerances live in the policy. |
| Judgments | Typed yes/no, choice and score questions over **redacted** text. Uses Jev when a key is set; otherwise Gemini, marked degraded. The injection detector always runs as its own request. |
| Agent | Bounded investigator: read-only tools, at most 6 calls, re-checked by code, capped at Review |
| Learning | Layout templates (T3 → T2 → T1 reading ladder), vendor item map, judgment cache, decision-pattern suggestions |
| UI | Next.js 16, Tailwind, Recharts, SSE live run view |

## Scenarios

| ID | Input | Result |
|---|---|---|
| H1 | Clean PDF, exact PO, fully received | Auto-approved, bill posted, read-back OK |
| E1 | `.eml` from a lookalike domain: new IBAN, urgency, text aimed at automated systems, perfect 3-way match | Hold for controller: owed **yes**, safe **no**, call back on the master-data number |
| E2 | 100 billed, 60 received | Hold (awaiting receipt): $6,000 payable, $4,000 held. Posting GRN-883 releases the $4,000 automatically. |
| E3 | Third invoice on a split PO that over-shipped | Hold for procurement: pay the $3,000 PO balance, ask for an amendment on $1,500 |
| E4 | **Phone photo**, Dutch, decimal comma, prices include VAT, no PO printed | Review: "Confirm PO-4460?" (the only open PO that matches all lines, compared net of VAT) |
| G1 | Bilingual English + Hindi GST invoice, CGST/SGST, ₹ lakh grouping | Auto-approved |
| B1 | Scanned typewriter invoice, no invoice number | Hold + drafted vendor email |
| B2 | Prices +1.2% and $40 freight, both inside tolerance | Auto-approved |
| B3 | Duplicate resubmitted as "42" on a new template | Reject |
| B4 | Statement of account | Not an invoice |
| B6 | Tampered PDF: visible IBAN ≠ hidden text-layer IBAN | Hold: possible tampering |
| S01–S14, L2 | A month of Acme invoices, then a new template | T3 → T2 → T1, then "layout changed → full read" |

Pre-generated scenario inputs (PDFs, scans, phone photo, `.eml`) live in `scenarios/out/`, and saved fixture responses live in `services/core/app/ai/fixtures/`.

## Deploy

- **Frontend (`apps/web`) on Vercel**: import the repo with Root Directory set to `apps/web` and set `NEXT_PUBLIC_API` to your live backend URL.
- **Backend (`services/core` + Postgres)**: deploy the root `Dockerfile` (or `render.yaml` Blueprint) to Render, Railway, or Fly.io with a managed PostgreSQL 16 database (`DATABASE_URL`). `AI_MODE=auto` replays saved fixtures for the built-in scenarios and uses live Gemini/Claude models for new uploads when API keys are set.
