"""Facts contract (§7): raw measurements only. No tolerance or threshold is applied here."""
from __future__ import annotations

import re
from decimal import Decimal

from stdnum import ean  # noqa: F401  (ensures stdnum import works)
from stdnum.in_ import gstin
from stdnum.us import ein

from .money import ZERO, fmt_money, parse_amount, parse_date, q2, to_usd
from .models import DECISION_FIELDS


def tax_id_valid(raw: str | None) -> bool | None:
    if not raw:
        return None
    s = raw.strip().upper()
    if re.fullmatch(r"\d{2}-?\d{7}", s):
        return ein.is_valid(s)
    if re.fullmatch(r"\d{2}[A-Z0-9]{13}", s):
        return gstin.is_valid(s)
    return None  # unknown scheme: not judged


def build(C: dict) -> tuple[dict, dict]:
    """Returns (facts for the policy, ctx for message templates)."""
    f = C["fields"]
    val = lambda k: (f.get(k) or {}).get("value")  # noqa: E731
    total = parse_amount(val("total"))
    sub = parse_amount(val("subtotal"))
    tax = parse_amount(val("tax")) or ZERO
    freight = parse_amount(val("freight")) or ZERO
    rec = C["reconciliation"]
    inv_date, ambiguous = parse_date(val("invoice_date"))
    ven, po, scr, jv = C["vendor"], C["po"], C["screen"], C.get("judge") or {}
    vrow = ven.get("row") or {}
    lines = C["lines"]
    goods_amt = sum((Decimal(str(l["amount"])) for l in lines if l["line_kind"] in ("goods", "service")), ZERO)
    # PO consumption is measured at PO prices; price variance is MAT-01's job, not PO-03's.
    goods_at_po = sum((Decimal(str(l["extended_po"])) if l.get("paired") else Decimal(str(l["amount"]))
                       for l in lines if l["line_kind"] in ("goods", "service")), ZERO)
    po_row = po.get("row") or {}
    po_total = Decimal(str(po_row["total"])) if po_row.get("total") is not None else None
    po_billed_before = Decimal(str(C.get("po_billed_before", 0)))
    missing = [k for k in ("invoice_number", "total", "vendor_name") if not val(k)]
    uncertain = [k for k in DECISION_FIELDS if val(k) and (f.get(k) or {}).get("evidence") == "uncertain"]
    if inv_date is None and val("invoice_date"):
        uncertain.append("invoice_date")
    if ambiguous:
        uncertain.append("invoice_date")
    if not val("invoice_date"):
        uncertain.append("invoice_date")
    # Tax arithmetic: rate x base (base = goods subtotal), only when a rate is printed.
    tax_delta = None
    rate = parse_amount(C["extraction"].get("tax_rate"))
    if rate is not None and sub is not None:
        if rec.get("mode") == "inclusive":
            gross = total if total is not None else sub
            expected_tax = q2(gross - gross / (1 + rate / 100))
        else:
            expected_tax = q2(sub * rate / 100)
        tax_delta = float(abs(expected_tax - tax))
    j = lambda k, fld="p": (jv.get(k) or {}).get(fld)  # noqa: E731
    facts = {
        "document_type": C["doc_type"],
        "vendor_id": ven.get("vendor_id"), "vendor_status": vrow.get("status"), "vendor_requires_po": vrow.get("requires_po", True),
        "vendor_resolution_method": ven["method"],
        "missing_critical": missing, "uncertain_fields": sorted(set(uncertain)),
        "reconciles": rec["reconciles"], "reconcile_delta_abs": float(rec["delta"]),
        "render_matches_text": C.get("render", {}).get("matches"),
        "po_status": po["status"], "po_id": po.get("po_id"), "po_currency_matches":
            (po_row.get("currency") == (val("currency") or po_row.get("currency"))) if po_row else None,
        "po_total": float(po_total) if po_total is not None else None,
        "po_billed_before": float(po_billed_before),
        "po_cumulative_after": float(po_billed_before + goods_at_po) if po_total is not None else None,
        "invoice_goods_billed": float(goods_amt),
        "open_po_count": po.get("open_po_count", 0),
        "lines": lines,
        "unpaired_goods_lines": sum(1 for l in lines if l["line_kind"] in ("goods", "service") and not l["paired"]),
        "min_pair_p": min((l["pair_p"] for l in lines if l.get("pair_method") == "description"), default=None),
        "freight_amount": float(freight + sum((Decimal(str(l["amount"])) for l in lines if l["line_kind"] == "freight"), ZERO)),
        "tax_amount": float(tax), "amount_total": float(total) if total is not None else 0.0,
        "tax_delta_abs": tax_delta, "tax_id_valid": tax_id_valid(val("vendor_tax_id")),
        **{k: scr.get(k) for k in ("hard_duplicate", "soft_duplicate", "bank_present", "bank_checksum_valid", "bank_matches_verified",
                                   "days_since_bank_change", "first_payment", "sender_domain_distance", "reply_to_mismatch")},
        "jev_requests_bank_change": j("requests_bank_change"), "jev_pressure_level": j("pressure", "level"),
        "jev_instructs_automation": j("instructs_automation"),
        "npo_above_usual": False, "own_paid": float(C.get("own_paid", 0)),
        "amount_total_base": float(to_usd(total, val("currency") or po_row.get("currency") or "USD") or 0),
        "tax_inclusive": rec.get("mode") == "inclusive",
    }
    cur = val("currency") or po_row.get("currency") or "USD"
    ctx = {
        "vendor_legal_name": vrow.get("legal_name"), "vendor_name_printed": val("vendor_name"), "invoice_number": val("invoice_number"),
        "currency": cur, "total": total, "total_fmt": fmt_money(total, cur), "po_id": po.get("po_id"),
        "po_number_printed": val("po_number"), "po_currency": po_row.get("currency"), "open_po_count": po.get("open_po_count", 0),
        "document_type": C["doc_type"], "missing_critical_text": ", ".join(m.replace("_", " ") for m in missing) or "—",
        "uncertain_text": ", ".join(u.replace("_", " ") for u in sorted(set(uncertain))) or "—",
        "uncertain_first": (sorted(set(uncertain)) or [""])[0].replace("_", " "),
        "uncertain_first_value": val((sorted(set(uncertain)) or [""])[0]) if uncertain else "",
        "reconcile_delta_fmt": fmt_money(Decimal(str(rec["delta"])), cur),
        "po_cumulative_after_fmt": fmt_money(Decimal(str(facts["po_cumulative_after"] or 0)), cur),
        "po_total_fmt": fmt_money(po_total, cur), "freight_fmt": fmt_money(Decimal(str(facts["freight_amount"])), cur),
        "tax_delta_fmt": fmt_money(Decimal(str(tax_delta or 0)), cur),
        "unpaired_goods_lines": facts["unpaired_goods_lines"], "credit_ref": C["extraction"].get("original_invoice_ref") or "an earlier invoice",
        "render_mismatch_field": C.get("render", {}).get("field", "value"),
        **{k: scr.get(k) for k in ("duplicate_of", "soft_duplicate_of", "invoice_bank_masked", "verified_bank_masked",
                                   "callback_phone", "sender_domain", "vendor_domain", "days_since_bank_change")},
    }
    ctx["price_fail_text"] = "; ".join(
        f"{l['description'][:40]}: billed {fmt_money(Decimal(str(l['unit_price'])), cur)} vs PO {fmt_money(Decimal(str(l['po_unit_price'])), cur)}"
        for l in lines if l.get("paired") and l.get("price_delta_abs", 0) > 0) or "Price above PO"
    ctx["qty_unreceived_text"] = "; ".join(
        f"{l['description'][:40]}: billed {l['qty_billed']:g}, received {max(0, l['qty_received_net']):g}"
        for l in lines if l.get("paired") and l["qty_billed"] > l.get("qty_received_net", 0)) or "Billed more than received"
    return facts, ctx
