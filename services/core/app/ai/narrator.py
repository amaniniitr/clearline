"""Explain: three plain sentences for the buyer, plus a vendor email for HOLD/REJECT.
Writes from `facts` only. Money guard: every figure in the text must appear in the allowed set, else the template wins."""
from __future__ import annotations

import json
import re
from decimal import Decimal

from ..config import settings
from ..domain.money import fmt_money, parse_amount
from . import gateway

SYSTEM = """You write for a finance manager who is not technical. Given a decision on a vendor invoice, write exactly
three short sentences: what the invoice is, what was checked and found, and what happens next.
Use only the figures provided. No jargon (no "rule IDs", "probability", "model"). Do not invent anything."""


def _figures(text: str) -> set[Decimal]:
    out = set()
    for m in re.findall(r"[$€£₹]?\s?\d[\d,]*(?:\.\d+)?", text):
        v = parse_amount(m)
        if v is not None and v >= 10:  # ignore small integers like "3 lines"
            out.add(v.quantize(Decimal("0.01")))
    return out


def template(p: dict, ctx: dict) -> str:
    cur = ctx.get("currency")
    vendor, total = ctx.get("vendor_legal_name") or ctx.get("vendor_name_printed") or "The vendor", fmt_money(ctx.get("total"), cur)
    fails = [f for f in p["findings"] if f["status"] == "fail"]
    passed = len(p["findings"]) - len(fails)
    s1 = f"{vendor} invoice {ctx.get('invoice_number') or '(no number)'} for {total}."
    if not fails:
        s2 = f"All {len(p['findings'])} checks passed, including the match against what was ordered and received."
    else:
        s2 = f"{passed} of {len(p['findings'])} checks passed; the deciding finding: {fails[0]['message']}"
    s3 = {"AUTO_APPROVE": "It has been approved and posted for payment.",
          "REVIEW": "A person needs to confirm one thing before it is paid.",
          "HOLD": "Payment is on hold until the question below is answered.",
          "REJECT": "It should not be paid.",
          "NOT_AN_INVOICE": "Nothing needs to be paid.",
          "APPLY_CREDIT": "The credit can be applied to the next payment."}[p["outcome"]]
    return " ".join([s1, s2, s3])


def narrate(p: dict, ctx: dict) -> tuple[str, str]:
    """Returns (text, source) where source is model | template."""
    fallback = template(p, ctx)
    if not settings().llm_enabled:
        return fallback, "template"
    brief = {"vendor": ctx.get("vendor_legal_name"), "invoice_number": ctx.get("invoice_number"),
             "total": fmt_money(ctx.get("total"), ctx.get("currency")), "outcome": p["outcome"], "owed": p["owed"],
             "safe_to_pay": p["safe_to_pay"], "amount_payable": fmt_money(p["amount_payable"], ctx.get("currency")),
             "amount_held": fmt_money(p["amount_held"], ctx.get("currency")), "question": p.get("question"),
             "failed_checks": [f["message"] for f in p["findings"] if f["status"] == "fail"][:4],
             "passed_checks": len([f for f in p["findings"] if f["status"] == "pass"])}
    allowed = _figures(json.dumps(brief)) | _figures(fallback)
    try:
        s = settings()
        resp, _ = gateway.gemini("narrate", brief, model=s.gemini_light, contents=[json.dumps(brief)], system=SYSTEM,
                                 thinking=None, max_output_tokens=300)
        text = resp["text"].strip()
    except Exception:  # noqa: BLE001
        return fallback, "template"
    if not text or not _figures(text) <= allowed:
        return fallback, "template (money guard)"
    return text, "model"


def vendor_email(p: dict, ctx: dict) -> str | None:
    if p["outcome"] not in ("HOLD", "REJECT"):
        return None
    vendor = ctx.get("vendor_legal_name") or "Supplier"
    num = ctx.get("invoice_number") or "(no number)"
    code = p.get("action_code")
    body = {
        "request_info": f"Your invoice for {fmt_money(ctx.get('total'), ctx.get('currency'))} is missing {ctx.get('missing_critical_text')}. "
                        "Please send a corrected invoice so we can process it.",
        "reject_duplicate": f"Invoice {num} appears to duplicate {ctx.get('duplicate_of')}, which we have already received. "
                            "We will not process it again. Please contact us if this is a separate charge.",
        "short_pay_and_amend": f"Invoice {num} exceeds the remaining balance on {ctx.get('po_id')}. We will pay "
                               f"{fmt_money(p['amount_payable'], ctx.get('currency'))} now; the rest awaits a PO amendment.",
        "await_receipt": f"We have received part of the goods on invoice {num}. We will pay "
                         f"{fmt_money(p['amount_payable'], ctx.get('currency'))} now and the remainder on receipt.",
    }.get(code)
    if code == "verify_bank":
        return None  # never email the sender about bank changes: call back on the master-data number
    if not body:
        body = f"Invoice {num} is on hold: {next((f['message'] for f in p['findings'] if f['status'] == 'fail'), '')}"
    return f"To: {vendor} accounts receivable\nSubject: Invoice {num}\n\nHello,\n\n{body}\n\nKind regards,\nAccounts Payable, Clearline Manufacturing"
