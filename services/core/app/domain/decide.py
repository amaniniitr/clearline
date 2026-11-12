"""Decide: the single place findings are merged (worst wins + P5 caps), plus the two badges, money split,
owner, GL coding, due dates and the one question. Pure Python."""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

from .models import OUTCOME_RANK, Finding
from .money import ZERO, fmt_money, parse_date, q2

SAFETY_RULES = {"PAY-01", "PAY-02", "PAY-03", "PAY-04", "DOC-02", "SEC-01"}


class _Safe(dict):
    def __missing__(self, k):
        return "{" + k + "}"


def render(msg: str, ctx: dict) -> str:
    return msg.format_map(_Safe(ctx))


def terms(inv_date: date | None, due_printed: str | None, terms_text: str | None) -> dict:
    out = {"due_date": None, "discount_pct": None, "discount_deadline": None}
    d, _ = parse_date(due_printed) if due_printed else (None, False)
    t = terms_text or ""
    m = re.search(r"(?i)net\s*(\d+)", t)
    if d:
        out["due_date"] = d
    elif inv_date and m:
        out["due_date"] = inv_date + timedelta(days=int(m.group(1)))
    elif inv_date and re.search(r"(?i)receipt|immediate", t):
        out["due_date"] = inv_date
    dm = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+)", t)
    if dm and inv_date:
        out["discount_pct"] = float(dm.group(1))
        out["discount_deadline"] = inv_date + timedelta(days=int(dm.group(2)))
    return out


def money_split(facts: dict, lines: list[dict], line_checks: list[dict], fails: set[str]) -> tuple[Decimal, Decimal, list[dict]]:
    total = Decimal(str(facts.get("amount_total") or 0))
    checks = {c["idx"]: c for c in line_checks}
    goods_billed = goods_payable = ZERO
    gl: dict[tuple, Decimal] = {}
    per_line = []
    for l in lines:
        if l["line_kind"] not in ("goods", "service"):
            continue
        billed = Decimal(str(l["amount"]))
        goods_billed += billed
        if not l.get("paired"):
            goods_payable += billed
            per_line.append({"idx": l["idx"], "payable": float(billed), "why": "not matched to the PO"})
            continue
        c = checks.get(l["idx"], {})
        qty_billed = Decimal(str(l["qty_billed"]))
        qty = qty_billed
        if c.get("unreceived"):
            qty = min(qty, max(ZERO, Decimal(str(l["qty_received_net"]))))
        if c.get("over_ordered") or "PO-03" in fails:
            qty = min(qty, max(ZERO, Decimal(str(l["qty_ordered_net"]))))
        if c.get("price_fail"):
            gross = Decimal(str(l.get("unit_price_printed") or l["unit_price"])) / Decimal(str(l["unit_price"])) if l["unit_price"] else Decimal(1)
            price = Decimal(str(l["po_unit_price"])) * gross
            pay = q2(qty * price)
        else:
            price = Decimal(str(l.get("unit_price_printed") or l["unit_price"]))
            pay = billed if qty == qty_billed else q2(billed * qty / qty_billed) if qty_billed else ZERO
        goods_payable += pay
        key = (l.get("gl_account"), l.get("cost_center"))
        gl[key] = gl.get(key, ZERO) + pay
        per_line.append({"idx": l["idx"], "payable": float(pay), "qty": float(qty), "price": float(price)})
    tax = Decimal(str(facts.get("tax_amount") or 0))
    if facts.get("tax_inclusive"):
        tax = ZERO  # already inside the line amounts
    freight = Decimal(str(facts.get("freight_amount") or 0))
    tax_pay = q2(tax * goods_payable / goods_billed) if goods_billed else tax
    freight_pay = ZERO if "MAT-03" in fails else freight
    payable = min(total, q2(goods_payable + tax_pay + freight_pay)) if total else q2(goods_payable + tax_pay + freight_pay)
    gl_coding = [{"gl_account": k[0], "cost_center": k[1], "amount": float(v)} for k, v in gl.items()]
    if freight_pay:
        gl_coding.append({"gl_account": "5900-Freight-in", "cost_center": None, "amount": float(freight_pay)})
    if tax_pay:
        gl_coding.append({"gl_account": "2300-Sales-tax", "cost_center": None, "amount": float(tax_pay)})
    return payable, q2(total - payable), gl_coding


QUESTIONS = {
    "verify_bank": "Call {vendor_legal_name} on the vendor-master number ({callback_phone}) to confirm the bank account before paying?",
    "await_receipt": "Pay {payable_fmt} now for what was received and release {held_fmt} automatically when the rest arrives?",
    "short_pay_and_amend": "Pay {payable_fmt} (the remaining PO balance) and ask procurement to amend {po_id} for the other {held_fmt}?",
    "reject_duplicate": "Reject as a duplicate of {duplicate_of} and send the drafted notice to {vendor_legal_name}?",
    "reject": "Reject this invoice and notify {vendor_legal_name}?",
    "request_info": "Send the drafted email asking {vendor_legal_name} for the missing {missing_critical_text}?",
    "file_statement": "File this statement for reconciliation? No payment is needed.",
    "apply_credit": "Apply the {total_fmt} credit against {vendor_legal_name}'s next payment?",
    "short_pay": "Pay at the PO price ({payable_fmt}) and short-pay the {held_fmt} difference?",
    "confirm_field": "Is the {uncertain_first} '{uncertain_first_value}'? I could not confirm it from the document.",
    "confirm_po": "Confirm {po_id} for this invoice?",
    "link_po": "Which PO does this invoice belong to?",
    "confirm_freight": "Accept {freight_fmt} freight from {vendor_legal_name}?",
    "confirm_vendor": "Is '{vendor_name_printed}' the same company as {vendor_legal_name}?",
    "flag_injection": "This document contains text aimed at automated systems. Confirm it is genuine before approving?",
    "sign_off": "Approve {total_fmt}? It is above the AP approval limit.",
    "confirm_not_duplicate": "Is this a separate invoice from {soft_duplicate_of}?",
    "pair_lines": "Which PO line does the unmatched invoice line belong to?",
    "identify_vendor": "Which vendor sent this invoice?",
    "investigate_tampering": "The visible bank details differ from the file's hidden text. Treat as tampering and contact {vendor_legal_name}?",
    "confirm_tax": "Accept the tax as billed?",
    "classify_document": "What kind of document is this?",
    "complete_onboarding": "Finish onboarding {vendor_legal_name} before paying?",
    "reopen_po": "Reopen {po_id} or reject the invoice?",
    "confirm_currency": "Pay in {currency} although the PO is in {po_currency}?",
    "approve_budget": "Approve {total_fmt} against your budget?",
}
ACTION_LABEL = {
    "verify_bank": "Hold payment and verify the bank account by call-back",
    "await_receipt": "Short-pay what was received; hold the rest until goods arrive",
    "short_pay_and_amend": "Short-pay to the PO balance; ask procurement for a PO amendment",
    "reject_duplicate": "Reject as duplicate", "reject": "Reject", "request_info": "Ask the vendor for the missing information",
    "file_statement": "File for reconciliation", "apply_credit": "Apply credit", "short_pay": "Pay at PO price",
    "confirm_field": "Confirm the uncertain field", "confirm_po": "Confirm the inferred PO", "link_po": "Link a PO",
    "confirm_freight": "Approve or short-pay freight", "confirm_vendor": "Confirm the vendor",
    "flag_injection": "Review the flagged text before approving", "sign_off": "Controller sign-off",
    "confirm_not_duplicate": "Confirm it is not a duplicate", "pair_lines": "Match the line to the PO",
    "investigate_tampering": "Treat as possible tampering", "approve": "Approve and post",
}


def decide(findings: list[Finding], facts: dict, lines: list[dict], line_checks: list[dict], caps: list[dict],
           ctx: dict, attempted: list[str]) -> dict:
    fails = [f for f in findings if f.status == "fail"]
    doc_gate = next((f for f in fails if f.rule_id == "DOC-01" and f.outcome in ("NOT_AN_INVOICE", "APPLY_CREDIT")), None)
    if doc_gate:
        # Not a bill: only safety, tampering and duplicate findings still count.
        fails = [f for f in fails if f is doc_gate or f.rule_id in SAFETY_RULES | {"DUP-01"}]
        findings = [f for f in findings if f.status == "pass" or f in fails]
    fail_ids = {f.rule_id for f in fails}
    order = {f.rule_id: k for k, f in enumerate(findings)}
    worst = max(fails, key=lambda f: (OUTCOME_RANK.get(f.outcome or "", 0), -order[f.rule_id]), default=None)
    outcome = worst.outcome if worst else "AUTO_APPROVE"
    primary = worst
    applied_caps = []
    if outcome == "AUTO_APPROVE" and caps:
        outcome = "REVIEW"
        applied_caps = caps
    # Special outcomes for non-invoices take precedence only when nothing worse applies.
    payable, held, gl_coding = (ZERO, ZERO, [])
    total = Decimal(str(facts.get("amount_total") or 0))
    if outcome not in ("REJECT", "NOT_AN_INVOICE", "APPLY_CREDIT"):
        payable, held, gl_coding = money_split(facts, lines, line_checks, fail_ids)
        own_paid = Decimal(str(facts.get("own_paid") or 0))
        if own_paid:
            # Later tranche of a partly paid invoice: pay only what is newly payable.
            payable = max(ZERO, payable - own_paid)
            held = max(ZERO, q2(total - own_paid - payable))
    safety_fail = bool(fail_ids & SAFETY_RULES)
    owed = "no" if outcome in ("REJECT", "NOT_AN_INVOICE") else ("n/a" if outcome == "APPLY_CREDIT" else
                                                                ("partly" if held > Decimal("0.01") else "yes"))
    safe = "n/a" if outcome in ("REJECT", "NOT_AN_INVOICE", "APPLY_CREDIT") else ("no" if safety_fail else "yes")
    sub_status = "awaiting_receipt" if "MAT-02" in fail_ids and not safety_fail and outcome == "HOLD" else None
    if outcome == "HOLD" and "EXT-01" in fail_ids:
        sub_status = "awaiting_vendor"
    c = {**ctx, "payable_fmt": fmt_money(payable, ctx.get("currency")), "held_fmt": fmt_money(held, ctx.get("currency"))}
    if primary is None and applied_caps:
        action = applied_caps[0]["action"]
        owner = applied_caps[0]["owner"]
    elif primary is not None:
        # A payment-safety failure always owns the question when the outcome is HOLD (tampering first).
        if outcome == "HOLD" and safety_fail:
            primary = next(f for f in fails if f.rule_id in SAFETY_RULES and f.outcome == "HOLD")
        elif sub_status == "awaiting_receipt":
            primary = next(f for f in fails if f.rule_id == "MAT-02")
        action, owner = primary.action, primary.owner
    else:
        action, owner = "approve", None
    question = render(QUESTIONS.get(action, ""), c) if outcome != "AUTO_APPROVE" else None
    return {
        "outcome": outcome, "sub_status": sub_status, "owed": owed, "safe_to_pay": safe,
        "recommended_action": ACTION_LABEL.get(action, action), "action_code": action, "owner_role": owner,
        "question": question, "attempted": attempted, "amount_payable": payable, "amount_held": held,
        "gl_coding": gl_coding, "caps": applied_caps, "primary_rule": primary.rule_id if primary else None,
        "findings": [{**f.model_dump(), "message": render(f.message, c)} for f in findings],
    }
