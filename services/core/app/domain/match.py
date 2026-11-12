"""Pair invoice lines to PO lines, then compute raw 3-way-match measurements. No tolerances here (§7)."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np
from scipy.optimize import linear_sum_assignment

from .money import ZERO, parse_amount, q2
from .normalise import spec_tokens

PAIR_FLOOR = 0.6


@dataclass
class Pairing:
    pairs: dict[int, dict] = field(default_factory=dict)  # inv line idx -> {po_line_id, method, p}
    vetoed: list[dict] = field(default_factory=list)
    asked: int = 0


def spec_veto(a: str, b: str) -> bool:
    """True if the two descriptions carry conflicting specs (M8 vs M10, 6mm vs 8mm). Jev can't compare numbers."""
    ta, tb = spec_tokens(a), spec_tokens(b)
    return bool(ta and tb and ta != tb and not (ta <= tb or tb <= ta))


def pair_lines(inv_lines: list[dict], po_lines: list[dict], same_item) -> Pairing:
    """inv_lines: [{idx, description, sku, line_kind}]. same_item(a, b) -> probability (Jev Noul)."""
    res = Pairing()
    goods = [l for l in inv_lines if l["line_kind"] in ("goods", "service")]
    used = set()
    for l in goods:
        if l.get("sku"):
            m = next((p for p in po_lines if p["sku"] and p["sku"].upper() == l["sku"].upper() and p["id"] not in used), None)
            if m:
                res.pairs[l["idx"]] = {"po_line_id": m["id"], "method": "sku", "p": 1.0}
                used.add(m["id"])
    rest = [l for l in goods if l["idx"] not in res.pairs]
    cands = [p for p in po_lines if p["id"] not in used]
    if rest and cands:
        m = np.zeros((len(rest), len(cands)))
        for i, l in enumerate(rest):
            for j, p in enumerate(cands):
                if spec_veto(l["description"], p["description"]):
                    res.vetoed.append({"invoice": l["description"], "po": p["description"]})
                    continue
                m[i, j] = same_item(l["description"], p["description"])
                res.asked += 1
        rows, cols = linear_sum_assignment(-m)
        for i, j in zip(rows, cols):
            if m[i, j] >= PAIR_FLOOR:
                res.pairs[rest[i]["idx"]] = {"po_line_id": cands[j]["id"], "method": "description", "p": round(float(m[i, j]), 3)}
    return res


def line_measures(inv_lines: list[dict], pairing: Pairing, po_lines: dict[str, dict], received: dict[str, Decimal],
                  billed_before: dict[str, Decimal], inclusive_rate: Decimal | None = None) -> list[dict]:
    """Raw per-line measurements for the policy. Signed price delta: positive = billed above PO (unfavourable).
    PO prices are net; when the invoice prices include tax, the invoice unit price is converted to net first."""
    out = []
    for l in inv_lines:
        qty, unit = parse_amount(l.get("qty")), parse_amount(l.get("unit_price"))
        gross_unit = unit
        if unit is not None and inclusive_rate:
            unit = unit / (1 + inclusive_rate / 100)
        pr = pairing.pairs.get(l["idx"])
        rec = {"idx": l["idx"], "line_kind": l["line_kind"], "paired": pr is not None, "description": l["description"],
               "sku": l.get("sku"), "qty_billed": float(qty or 0), "unit_price": float(round(unit, 4)) if unit is not None else 0.0,
               "unit_price_printed": float(gross_unit or 0), "amount": float(parse_amount(l.get("amount")) or 0)}
        if pr:
            pl = po_lines[pr["po_line_id"]]
            po_unit = Decimal(str(pl["unit_price"]))
            q = qty or ZERO
            rec.update({
                "po_line_id": pl["id"], "pair_method": pr["method"], "pair_p": pr["p"],
                "po_unit_price": float(po_unit), "po_description": pl["description"],
                "price_delta_abs": float(q2((unit - po_unit) * q)) if unit is not None else 0.0,
                "price_delta_pct": float(round((unit - po_unit) / po_unit, 4)) if unit is not None and po_unit else 0.0,
                "extended_po": float(q2(po_unit * q)),
                "qty_ordered": float(pl["qty"]),
                "qty_received": float(received.get(pl["id"], ZERO)),
                "qty_billed_before": float(billed_before.get(pl["id"], ZERO)),
                "qty_received_net": float(received.get(pl["id"], ZERO) - billed_before.get(pl["id"], ZERO)),
                "qty_ordered_net": float(Decimal(str(pl["qty"])) - billed_before.get(pl["id"], ZERO)),
                "gl_account": pl.get("gl_account"), "cost_center": pl.get("cost_center"),
            })
        out.append(rec)
    return out
