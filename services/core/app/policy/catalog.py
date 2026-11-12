"""The policy, written as data and compiled to JDM (GoRules ZEN decision graphs).

Every tolerance and threshold lives here, in the Tolerances table. Facts are raw measurements.
Each rule is one decision table (hit policy "first"): failure rows in priority order, then a pass row,
so passes are recorded too (P3). Python `decide()` is the only place findings are merged.
"""
from __future__ import annotations

# Tables that "teach the agent" can never modify (§7). Only an admin, through a reviewed change.
LOCKED = {"PAY-01", "PAY-02", "PAY-03", "PAY-04", "DOC-02", "DUP-01", "SEC-01"}

# Default tolerances (row "-" in the Tolerances table) and per-vendor overrides.
DEFAULT_TOLERANCES: dict[str, float | int] = {
    "price_pct": 0.02,          # MAT-01: unfavourable price variance allowed, as share of the PO extended amount
    "price_abs": 5.00,          # MAT-01: ...or this absolute amount, whichever is larger
    "freight_abs": 50.00,       # MAT-03: freight allowance per invoice
    "po_overrun_pct": 0.01,     # PO-03: cumulative billing may exceed the PO total by this share
    "reconcile_abs": 0.02,      # EXT-03: rounding allowed when lines + tax + freight are summed
    "tax_abs": 0.05,            # TAX-01: rounding allowed on tax = rate x base
    "jev_bank_change": 0.50,    # PAY-03: probability above which a bank-change request is assumed
    "jev_pressure_level": 2,    # PAY-03: 0 none · 1 mild · 2 strong
    "jev_instructs": 0.50,      # SEC-01: probability above which the text is addressing an automated system
    "lookalike_max": 3,         # PAY-02: edit distance treated as a lookalike sender domain (1..N)
    "bank_change_days": 30,     # PAY-04: master-data bank change within N days needs a call-back
    "apr_limit": 25000,         # APR-01: above this, controller sign-off
    "pair_p_min": 0.95,         # pairing by description needs at least this probability to auto-approve
}
VENDOR_TOLERANCES: dict[str, dict[str, float]] = {
    "V-1001": {"freight_abs": 0.00},  # Acme's contract price includes delivery
}

# Rule tables. Each fail row: (condition, outcome, owner, action, message template).
# Conditions are ZEN expressions over facts + `tol`. Messages use {placeholders} filled from facts by Python.
RULES: list[dict] = [
    {"id": "DOC-01", "title": "Document is an invoice", "stage": "rules",
     "fail": [
         ("document_type == 'statement'", "NOT_AN_INVOICE", "ap_clerk", "file_statement",
          "This is a statement of account, not an invoice."),
         ("document_type == 'credit_note'", "APPLY_CREDIT", "ap_clerk", "apply_credit",
          "This is a credit note for {credit_ref}."),
         ("not (document_type in ['invoice'])", "REVIEW", "ap_clerk", "classify_document",
          "Document type is '{document_type}', not an invoice."),
     ]},
    {"id": "DOC-02", "title": "Visible text matches the text layer", "stage": "verify",
     "fail": [("render_matches_text == false", "HOLD", "controller", "investigate_tampering",
               "The visible {render_mismatch_field} differs from the PDF's hidden text layer. Possible tampering.")]},
    {"id": "EXT-01", "title": "Critical fields present", "stage": "verify",
     "fail": [("len(missing_critical) > 0", "HOLD", "ap_clerk", "request_info",
               "Missing on the invoice: {missing_critical_text}.")]},
    {"id": "EXT-02", "title": "No decision field is uncertain", "stage": "verify",
     "fail": [("len(uncertain_fields) > 0", "REVIEW", "ap_clerk", "confirm_field",
               "Could not confirm {uncertain_text} from the document.")]},
    {"id": "EXT-03", "title": "Lines, tax and freight add up to the total", "stage": "verify",
     "fail": [("reconciles == false", "REVIEW", "ap_clerk", "confirm_field",
               "Invoice arithmetic is off by {reconcile_delta_fmt}.")]},
    {"id": "VEN-01", "title": "Vendor identified without ambiguity", "stage": "resolve_vendor",
     "fail": [("vendor_resolution_method == 'none'", "HOLD", "ap_clerk", "identify_vendor",
               "Vendor '{vendor_name_printed}' is not in the vendor master."),
              ("vendor_resolution_method in ['fuzzy', 'jev']", "REVIEW", "ap_clerk", "confirm_vendor",
               "Vendor matched by name only ({vendor_name_printed} → {vendor_legal_name}).")]},
    {"id": "VEN-02", "title": "Vendor is approved", "stage": "resolve_vendor",
     "fail": [("vendor_status == 'blocked'", "REJECT", "ap_manager", "reject",
               "{vendor_legal_name} is blocked in the vendor master."),
              ("vendor_status != 'approved' and vendor_status != null", "HOLD", "procurement", "complete_onboarding",
               "{vendor_legal_name} has not finished onboarding.")]},
    {"id": "PO-01", "title": "Purchase order identified", "stage": "resolve_po",
     "fail": [("po_status == 'none' and vendor_requires_po == true", "REVIEW", "procurement", "link_po",
               "No PO on the invoice and none of the vendor's {open_po_count} open POs fits."),
              ("po_status == 'not_found'", "REVIEW", "procurement", "link_po",
               "PO {po_number_printed} does not exist for this vendor."),
              ("po_status == 'inferred'", "REVIEW", "ap_clerk", "confirm_po",
               "No PO printed. {po_id} was inferred from the lines.")]},
    {"id": "PO-02", "title": "PO is open, same vendor, same currency", "stage": "resolve_po",
     "fail": [("po_status == 'other_vendor'", "REJECT", "ap_manager", "reject",
               "PO {po_number_printed} belongs to a different vendor."),
              ("po_status == 'closed'", "HOLD", "procurement", "reopen_po", "PO {po_id} is closed."),
              ("po_currency_matches == false", "REVIEW", "ap_clerk", "confirm_currency",
               "Invoice currency {currency} differs from PO currency {po_currency}.")]},
    {"id": "PO-03", "title": "Cumulative billing within the PO total", "stage": "match",
     "fail": [("po_total != null and po_cumulative_after > po_total * (1 + tol.po_overrun_pct)", "HOLD", "procurement",
               "short_pay_and_amend",
               "Billed to date would reach {po_cumulative_after_fmt} against a PO of {po_total_fmt}.")]},
    {"id": "MAT-00", "title": "Every goods line matches a PO line", "stage": "pair",
     "fail": [("unpaired_goods_lines > 0 and po_status != 'none'", "REVIEW", "ap_clerk", "pair_lines",
               "{unpaired_goods_lines} invoice line(s) do not match any PO line."),
              ("min_pair_p != null and min_pair_p < tol.pair_p_min", "REVIEW", "ap_clerk", "pair_lines",
               "A line was matched to the PO by description with low certainty.")]},
    {"id": "MAT-01", "title": "Prices match the PO", "stage": "match",
     "fail": [("price_fail_lines > 0", "REVIEW", "ap_clerk", "short_pay",
               "{price_fail_text}")]},
    {"id": "MAT-02", "title": "Billed quantity received", "stage": "match",
     "fail": [("qty_unreceived_lines > 0", "HOLD", "receiving", "await_receipt",
               "{qty_unreceived_text}")]},
    {"id": "MAT-03", "title": "Freight within allowance", "stage": "match",
     "fail": [("freight_amount > tol.freight_abs", "REVIEW", "ap_clerk", "confirm_freight",
               "Freight {freight_fmt} exceeds the allowance for this vendor.")]},
    {"id": "DUP-01", "title": "Not a duplicate", "stage": "screen",
     "fail": [("hard_duplicate == true", "REJECT", "ap_manager", "reject_duplicate",
               "Same vendor and invoice number as {duplicate_of}, already billed.")]},
    {"id": "DUP-02", "title": "No near-duplicate", "stage": "screen",
     "fail": [("soft_duplicate == true", "REVIEW", "ap_clerk", "confirm_not_duplicate",
               "Same amount as {soft_duplicate_of} from this vendor within 30 days.")]},
    {"id": "PAY-01", "title": "Bank details match the verified account", "stage": "screen",
     "fail": [("bank_present == true and bank_checksum_valid == false", "HOLD", "controller", "verify_bank",
               "The bank account on the invoice fails its checksum."),
              ("bank_present == true and bank_matches_verified == false", "HOLD", "controller", "verify_bank",
               "Bank account {invoice_bank_masked} is not the verified account {verified_bank_masked}.")]},
    {"id": "PAY-02", "title": "Sent from the vendor's own domain", "stage": "screen",
     "fail": [("sender_domain_distance != null and sender_domain_distance > 0 and sender_domain_distance <= tol.lookalike_max",
               "HOLD", "controller", "verify_bank",
               "Sent from {sender_domain}, a lookalike of {vendor_domain}."),
              ("reply_to_mismatch == true", "HOLD", "controller", "verify_bank",
               "Reply-To address differs from the sender.")]},
    {"id": "PAY-03", "title": "No bank-change request or pressure", "stage": "judge",
     "fail": [("jev_requests_bank_change != null and jev_requests_bank_change >= tol.jev_bank_change", "HOLD", "controller",
               "verify_bank", "The message asks to change bank details."),
              ("jev_pressure_level != null and jev_pressure_level >= tol.jev_pressure_level", "HOLD", "controller",
               "verify_bank", "The message uses urgency or secrecy pressure.")]},
    {"id": "PAY-04", "title": "Established payee, no recent bank change", "stage": "screen",
     "fail": [("first_payment == true", "HOLD", "controller", "verify_bank",
               "First payment to this vendor. Call back before paying."),
              ("days_since_bank_change != null and days_since_bank_change < tol.bank_change_days", "HOLD", "controller",
               "verify_bank", "Vendor bank details changed {days_since_bank_change} days ago.")]},
    {"id": "SEC-01", "title": "Document does not address automated systems", "stage": "judge",
     "fail": [("jev_instructs_automation != null and jev_instructs_automation >= tol.jev_instructs", "REVIEW", "controller",
               "flag_injection", "The document contains text aimed at automated systems.")]},
    {"id": "TAX-01", "title": "Tax arithmetic and tax ID valid", "stage": "verify",
     "fail": [("tax_delta_abs != null and tax_delta_abs > tol.tax_abs", "REVIEW", "ap_clerk", "confirm_tax",
               "Tax does not equal rate x base (off by {tax_delta_fmt})."),
              ("tax_id_valid == false", "REVIEW", "ap_clerk", "confirm_tax", "Vendor tax ID fails its checksum.")]},
    {"id": "NPO-01", "title": "Non-PO amount within the usual range", "stage": "rules",
     "fail": [("po_status == 'none' and vendor_requires_po == false and npo_above_usual == true", "REVIEW",
               "budget_owner", "approve_budget", "Amount is above this vendor's usual range.")]},
    {"id": "APR-01", "title": "Within AP approval limit", "stage": "rules",
     "fail": [("amount_total_base > tol.apr_limit", "REVIEW", "controller", "sign_off",
               "Amount above the AP approval limit (USD equivalent); controller sign-off.")]},
]

RULE_TITLES = {r["id"]: r["title"] for r in RULES}
RULE_STAGE = {r["id"]: r["stage"] for r in RULES}
