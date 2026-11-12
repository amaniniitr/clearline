# Demo script

## Before you start

1. `scripts/api.sh` and `scripts/web.sh` are both running.
2. Open http://localhost:3000.
3. Click **Reset demo**.
4. Set stage pacing to about 350 ms so each step is visible on screen.

## 5-minute video

| Time | What to show | What to say |
|---|---|---|
| 0:00 | **How it works** page | "AP teams open every invoice, find the PO, check the numbers and decide. Clearline does that, and answers two questions separately: do we owe this, and is it safe to pay where they ask." |
| 0:30 | Run **H1** | Point at the stage rail as it fills. "It reads the invoice twice, independently, and highlights where each value sits on the page. Green means Verified: found on the page and confirmed another way." Then show the decision card: auto-approved, bill posted, read-back OK. |
| 1:30 | Run **E1** (the email) | "This one matches the PO perfectly. A plain PO match would pay it." Point at Screen: new bank account, sender `acme-supp1y.com`, Reply-To on Gmail. Point at Judge: bank-change request, pressure, text aimed at automated systems. Decision card: **Owed: yes · Safe: no**. The one question goes to the controller: call back on the master-data number, never the number in the email. Switch the role to AP clerk and click Approve. The server refuses. |
| 2:45 | Run **E2** | "Billed 100, received 60." Show the PO / receipts table with the shortfall highlighted, the agent's tool calls, $6,000 payable and $4,000 held. As AP clerk, **short-pay** $6,000. Go to **Queue → Simulate: receiving posts GRN-883**. A child run re-matches and posts the remaining $4,000 by itself. |
| 3:45 | **Learning** page, then **Simulate the month** on the home page | "Most invoices come from repeat vendors." Cost per invoice drops from about $0.006 (full read) to about $0.0015 (template + cross-check) to $0 (template only), and Clearline still checks the arithmetic, the PO, the bank and the visible pixels every time. Then run **L2**: "Acme changed its template. Clearline notices and does a full read." |
| 4:30 | **Dashboard** | Touchless rate, the dollars it stopped, the eval: 11 of 11 scenarios correct, 0 fields marked Verified that were actually wrong. |

## Live interview: all edge cases

- **E3, split-PO overrun.** State is carried across invoices. Pay the $3,000 PO balance and hold $1,500 for a procurement amendment.
- **E4, phone photo.** A Dutch invoice with prices that include VAT and no PO printed. Show the OCR path, the PO inference, and the agent's re-check: "Confirm PO-4460?"
- **G1, Indian GST.** Bilingual invoice, CGST + SGST, lakh grouping, GSTIN checksum. Auto-approved.
- **B6, tampered PDF.** "Both readers can be fooled by a hidden text layer. The render check compares what is visibly printed." Result: Hold, possible tampering.
- **B1, B3, B4.** Missing number gets a drafted email. A reformatted duplicate is rejected. A statement is filed, not paid.
- **Corrections.** On any Review case, use *Correct a field* to start a child run, and show the lineage.
- **Segregation of duties.** The controller who did the call-back can't also approve the payment.

## If something breaks

- **Model API down:** set `AI_MODE=fixture`. The scenarios replay saved responses at $0.
- **No key at all:** the offline reader takes over, and the stages are marked "degraded".
