"""Verify: compare the two readings, ground each value on the page, reconcile the arithmetic, grade evidence (§4).

Verified  = located on the page AND independently confirmed (A = B, arithmetic reconciles, or master data matches)
Read      = located on the page only
Uncertain = not located, A and B disagree, or validation failed
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from .models import Extraction, HEADER_FIELDS
from .money import ZERO, parse_amount, parse_date, q2
from .normalise import iban_norm

AMOUNT_FIELDS = {"subtotal", "tax", "freight", "total"}
DATE_FIELDS = {"invoice_date", "due_date"}


def _canon(field: str, v: str | None) -> str | None:
    if v is None or str(v).strip() == "":
        return None
    if field in AMOUNT_FIELDS:
        a = parse_amount(v)
        return str(q2(a)) if a is not None else None
    if field in DATE_FIELDS:
        d, _ = parse_date(v)
        return d.isoformat() if d else str(v).strip()
    if field == "remit_iban":
        return iban_norm(v)
    if field == "currency":
        import unicodedata

        from .money import parse_currency

        plain = unicodedata.normalize("NFKD", str(v)).encode("ascii", "ignore").decode() or str(v)
        return parse_currency(str(v)) or parse_currency(plain) or plain.strip().upper()
    return re.sub(r"\s+", " ", str(v)).strip().upper()


def _squash(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


@dataclass
class FieldState:
    field: str
    value: str | None
    a: str | None = None
    b: str | None = None
    located: bool = False
    agree: bool | None = None
    confirmed_by: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    page: int | None = None
    bbox: list[float] | None = None
    line_idx: int | None = None
    ambiguous: bool = False
    failed_validation: bool = False
    set_by: str = "machine"

    @property
    def evidence(self) -> str:
        if self.set_by == "human":
            return "verified"
        if self.value is None and self.agree is not False:
            return "absent"  # not printed, and no reading claims otherwise
        if not self.located or self.failed_validation or self.agree is False and not self.confirmed_by or self.ambiguous:
            return "uncertain"
        if self.confirmed_by:
            return "verified"
        return "read"

    def confirm(self, how: str) -> None:
        if how not in self.confirmed_by:
            self.confirmed_by.append(how)

    def to_dict(self) -> dict:
        return {"field": self.field, "value": self.value, "evidence": self.evidence,
                "reasons": self.reasons + [f"confirmed: {c}" for c in self.confirmed_by],
                "readings": {"a": self.a, "b": self.b}, "page": self.page, "bbox": self.bbox, "line": self.line_idx,
                "set_by": self.set_by}


def locate(value: str, lines: list[dict], prefer: list[int] | None = None) -> tuple[dict | None, list[float] | None]:
    """Find the line containing value (normalised); narrow the bbox to the matching words."""
    target = _squash(value)
    if not target:
        return None, None
    amount = parse_amount(value) if re.fullmatch(r"[\d,.\s$€£₹()\-]+", value.strip()) else None
    order = ([lines[i] for i in prefer if 0 <= i < len(lines)] if prefer else []) + lines
    for l in order:
        hit = target in _squash(l["text"])
        if not hit and amount is not None:
            hit = any(parse_amount(w["t"]) == amount for w in l.get("words", []) if re.search(r"\d", w["t"]))
        if hit:
            words = [w for w in l.get("words", []) if _squash(w["t"]) and (_squash(w["t"]) in target or
                     (amount is not None and parse_amount(w["t"]) == amount))]
            bb = ([min(w["bbox"][0] for w in words), min(w["bbox"][1] for w in words),
                   max(w["bbox"][2] for w in words), max(w["bbox"][3] for w in words)] if words else l["bbox"])
            return l, bb
    return None, None


def compare_readings(a: Extraction, b: dict | None, line_map: dict, pinned: dict | None = None) -> dict[str, FieldState]:
    lines = line_map["lines"]
    pinned = pinned or {}
    out: dict[str, FieldState] = {}
    for f in HEADER_FIELDS:
        av = getattr(a, f, None)
        brec = (b or {}).get(f) if b is not None else None
        bv = brec.get("value") if brec else None
        st = FieldState(field=f, value=av, a=av, b=bv)
        if f in pinned:
            st.value, st.set_by, st.located = pinned[f], "human", True
            st.reasons.append("set by a reviewer")
            out[f] = st
            continue
        ca, cb = _canon(f, av), _canon(f, bv)
        if b is not None:
            st.agree = (ca == cb) if (ca is not None or cb is not None) else None
            if st.agree:
                st.confirm("both readings agree")
            elif ca != cb:
                st.reasons.append(f"readings differ: A '{av}' vs B '{bv}'")
                if av is None and bv is not None:
                    st.value = bv
        if st.value is None:
            out[f] = st
            continue
        # Grounding. Read B's citation first (and the value must appear in the cited text), then A's quote.
        prefer = brec.get("blocks") if brec else None
        line, bb = locate(str(st.value), lines, prefer)
        if line is None and a.quotes.get(f):
            line, bb = locate(a.quotes[f], lines)
        if line is None and f == "currency":
            # Currency is usually printed as a symbol or inside a label: "€ 8.048,10", "Total Due (USD)".
            code = _canon("currency", st.value)
            sym = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹"}.get(code or "")
            line = next((l for l in lines if (code and re.search(rf"\b{code}\b", l["text"])) or (sym and sym in l["text"])
                         or (code == "INR" and re.search(r"\bRs\.?\b", l["text"]))), None)
            bb = line["bbox"] if line else None
            if line and code:
                st.value = code
        if line is not None:
            st.located, st.page, st.bbox, st.line_idx = True, line["page"], bb, line["i"]
            if prefer and line["i"] not in prefer:
                st.reasons.append("cited line did not contain the value; found elsewhere on the page")
        else:
            st.reasons.append("value not found on the page")
        if f == "currency":
            code = _canon("currency", st.value)
            if code and len(code) == 3 and code.isalpha():
                st.value = code
        if f in DATE_FIELDS:
            _, amb = parse_date(str(st.value))
            if amb:
                hint = date_order_hint(a)
                if hint is None:
                    st.ambiguous = True
                    st.reasons.append(f"ambiguous date '{st.value}' (day/month order)")
                else:
                    dd, _ = parse_date(str(st.value), hint)
                    st.reasons.append(f"printed '{st.value}', read {'day' if hint else 'month'}-first "
                                      f"({'European/Indian' if hint else 'US'} document)")
                    st.value = dd.isoformat()
        out[f] = st
    return out


def date_order_hint(a: Extraction) -> bool | None:
    """Day-first for EUR/INR/GBP documents (or non-US IBAN / GSTIN / EU VAT), month-first for USD. None if unknown."""
    cur = (_canon("currency", a.currency) or "") if a.currency else ""
    if cur in ("EUR", "INR", "GBP", "ZAR", "AUD", "SGD", "AED", "CHF"):
        return True
    tax = (a.vendor_tax_id or "").upper()
    if re.match(r"^\d{2}[A-Z]{5}\d{4}", tax) or re.match(r"^[A-Z]{2}\d", tax):
        return True
    if cur == "USD" or re.fullmatch(r"\d{2}-\d{7}", tax):
        return False
    return None


@dataclass
class Reconciliation:
    lines_sum: Decimal
    subtotal: Decimal | None
    tax: Decimal
    freight: Decimal
    total: Decimal | None
    tax_mode_used: str | None
    reconciles: bool
    delta: Decimal
    line_math_ok: list[bool]
    chosen: dict[str, str] = field(default_factory=dict)


def reconcile(fields: dict[str, FieldState], ex: Extraction, tol: Decimal = Decimal("0.02")) -> Reconciliation:
    """Σ lines = subtotal; subtotal + tax + freight = total (exclusive) or subtotal + freight = total (inclusive).
    When A and B disagree on an amount, keep whichever candidate makes the invoice add up."""
    line_amts, math_ok = [], []
    for l in ex.lines:
        amt, qty, unit = parse_amount(l.amount), parse_amount(l.qty), parse_amount(l.unit_price)
        line_amts.append(amt if amt is not None else ZERO)
        math_ok.append(amt is not None and qty is not None and unit is not None and abs(q2(qty * unit) - amt) <= tol)
    lines_sum = sum(line_amts, ZERO)

    def cands(f: str) -> list[Decimal | None]:
        st = fields.get(f)
        vals = []
        for v in ([st.value, st.a, st.b] if st else []):
            p = parse_amount(v) if v is not None else None
            if p not in vals:
                vals.append(p)
        return vals or [None]

    best = None
    for sub in cands("subtotal"):
        for tax in cands("tax"):
            for fr in cands("freight"):
                for tot in cands("total"):
                    s = sub if sub is not None else lines_sum
                    t, f_ = tax or ZERO, fr or ZERO
                    for mode, expect in (("exclusive", s + t + f_), ("inclusive", s + f_)):
                        if tot is None:
                            continue
                        delta = abs(expect - tot)
                        lines_ok = not ex.lines or abs(lines_sum - s) <= tol * max(1, len(ex.lines))
                        score = (delta <= tol and lines_ok, -delta)
                        if best is None or score > best[0]:
                            best = (score, sub, tax, fr, tot, mode, delta, lines_ok)
    if best is None:
        return Reconciliation(lines_sum, None, ZERO, ZERO, None, None, False, Decimal("0"), math_ok)
    (ok, _), sub, tax, fr, tot, mode, delta, lines_ok = best
    rec = Reconciliation(lines_sum, sub, tax or ZERO, fr or ZERO, tot, mode, bool(ok), q2(delta), math_ok)
    if ok:
        for f, v in (("subtotal", sub), ("tax", tax), ("freight", fr), ("total", tot)):
            st = fields.get(f)
            if st is None or v is None:
                continue
            if st.agree is False:
                st.value = str(q2(v))
                st.reasons.append(f"attempted resolution: arithmetic chose {v:,.2f}")
                st.agree = None
                rec.chosen[f] = str(v)
            st.confirm("invoice arithmetic reconciles")
    return rec
