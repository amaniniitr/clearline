# Process map

The same content appears on the **How it works** page in the console.

```
 Email / upload
      │
      ▼
 1 Intake ──── keep the original, record the sender, spot re-deliveries
      │
      ▼
 2 Read twice ─ A: looks at the page · B: reads the text lines and points to where each value is
      │         Known layout? Use the learned template (cheap), cross-check, or read fully if anything is off
      ▼
 3 Verify ──── do the two readings agree? does it add up? do the visible pixels match the hidden text?
      │         each field is marked Verified / Read / Uncertain
      ▼
 4 Match ───── vendor → PO (printed or inferred) → each line → price and quantity vs PO and goods received
      │
      ▼
 5 Screen ──── duplicate? bank account = the verified one? sender = the vendor? pressure or text aimed at automation?
      │
      ▼
 6 Policy ──── every check, from a readable decision table; passes are recorded too
      │
      ├─ exception? ─▶ 7 Investigate (read-only assistant; findings re-checked by code)
      ▼
 8 Decide ──── worst finding wins · Owed? · Safe to pay? · amount payable / held · owner · ONE question
      │
      ├─ all clear ─▶ post the bill to the ERP, read it back, learn from it
      └─ person needed ─▶ queue (by due time and dollars held)
                            ├─ approve / short-pay ─▶ post
                            ├─ call-back confirms ─▶ re-run · denies ─▶ reject as fraud
                            ├─ correct a field / link a PO ─▶ re-run (the original stays on record)
                            └─ goods arrive later ─▶ re-match automatically, release what is now received
```

## Decision points

| Question | Where it is answered | If "no" |
|---|---|---|
| Is it an invoice? | Read + policy DOC-01 | Statement: file it · Credit note: apply it |
| Can we trust each value? | Verify (Verified / Read / Uncertain) | Review, with that field as the question |
| Does the visible document match its file? | Render check (DOC-02) | Hold for the controller (possible tampering) |
| Which vendor and PO? | Resolve | Review (confirm) or Hold |
| Ordered, received, at the agreed price? | 3-way match + policy tolerances | Short-pay what is due and hold the rest |
| Already paid? | Duplicate checks | Reject |
| Safe to pay this account? | Payment safety (PAY-01 to PAY-04) | Hold; controller calls back on the master-data number |
