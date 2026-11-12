"""Layout memory and the reading ladder (ARCHITECTURE §16.2).

Fingerprint: the set of static label texts on a page ("Invoice No:", "Total Due (USD):"...). Two documents share a
layout when the Jaccard similarity of their label sets is >= MATCH. A template is learned only from Verified fields:
for each field it records the label it sits next to and how to take the value from that line/row; for the line table
it records the header and the column x-ranges.
"""
from __future__ import annotations

import re
from collections import defaultdict

from ..domain.models import Extraction, ExtractedLine, HEADER_FIELDS
from ..domain.money import parse_amount

MATCH = 0.85
T2_AFTER = 3      # shadow agreements in a row before the template reads (with a cross-check)
T1_AFTER = 10     # further agreements at T2 (no corrections) before the template reads alone
AUDIT_EVERY = 10  # at T1, 1 in N invoices still gets the cross-check
LEARNABLE = [f for f in HEADER_FIELDS]
TEMPLATE_DECISION_FIELDS = ["invoice_number", "total", "po_number", "vendor_name", "subtotal"]


def _label(text: str) -> str | None:
    t = text.strip()
    if ":" in t:
        lab = t.split(":", 1)[0].strip()
        if 1 < len(lab) <= 30 and not re.search(r"\d{3,}", lab):
            return lab.lower()
    if len(t) <= 40 and not re.search(r"\d", t) and len(t.split()) <= 5:
        return t.lower()
    return None


def fingerprint(line_map: dict) -> list[str]:
    labs = {_label(l["text"]) for l in line_map["lines"]}
    return sorted(x for x in labs if x)


def similarity(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


def best_layout(line_map: dict, layouts: list[dict]) -> tuple[dict | None, float]:
    fp = fingerprint(line_map)
    best, score = None, 0.0
    for lay in layouts:
        if lay["status"] == "retired":
            continue
        s = similarity(fp, lay["fingerprint"])
        if s > score:
            best, score = lay, s
    return (best, score) if score >= MATCH else (None, score)


def _rows(lines: list[dict]) -> dict[tuple, list[dict]]:
    rows: dict[tuple, list[dict]] = defaultdict(list)
    for l in lines:
        rows[(l["page"], l.get("row", l["i"]))].append(l)
    for k in rows:
        rows[k].sort(key=lambda l: l["bbox"][0])
    return rows


def learn_template(line_map: dict, fields: dict[str, dict], ex: Extraction) -> dict:
    """fields: verify output {field: {value, evidence, line}}. Only Verified fields are learned."""
    lines = line_map["lines"]
    by_i = {l["i"]: l for l in lines}
    rows = _rows(lines)
    rules = {}
    for f in LEARNABLE:
        st = fields.get(f)
        if not st or st.get("evidence") != "verified" or st.get("line") is None or not st.get("value"):
            continue
        line = by_i.get(st["line"])
        if not line:
            continue
        text, val = line["text"].strip(), str(st["value"]).strip()
        lab = _label(text)
        if ":" in text and lab and val.replace(" ", "") in text.split(":", 1)[1].replace(" ", ""):
            rules[f] = {"kind": "after_label", "label": text.split(":", 1)[0].strip()}
            continue
        row = rows[(line["page"], line.get("row", line["i"]))]
        others = [l for l in row if l["i"] != line["i"] and _label(l["text"])]
        if others and val.replace(" ", "") in text.replace(" ", ""):
            rules[f] = {"kind": "row_segment", "label": others[0]["text"].strip()}
            continue
        if text == val:
            rules[f] = {"kind": "line_equals", "value": val}
            continue
        # "Reference  INV-1200", "Balance payable USD 1,004.40": a fixed prefix without a colon.
        if text.replace(" ", "").endswith(val.replace(" ", "")):
            prefix = text[: len(text) - len(val)].strip() if text.endswith(val) else None
            if prefix and 1 < len(prefix) <= 40 and not re.search(r"\d{3,}", prefix):
                rules[f] = {"kind": "after_prefix", "prefix": prefix}
    table = _learn_table(lines, rows, ex, rules, fields, by_i)
    return {"fields": rules, "table": table}


def _learn_table(lines, rows, ex: Extraction, rules: dict | None = None, fields: dict | None = None, by_i: dict | None = None) -> dict | None:
    if not ex.lines:
        return None
    first = ex.lines[0]
    hdr = next((l for l in lines if re.search(r"(?i)\b(description|item)\b", l["text"]) and not re.search(r"\d{2,}", l["text"])), None)
    stop = next((l for l in lines if re.match(r"(?i)^sub\s*-?total", l["text"].strip())), None)
    stop_label = re.split(r":", stop["text"])[0].strip() if stop else None
    if stop_label is None and rules and fields and by_i:
        # No "Subtotal" label: the table ends at the row of the first verified total-like field.
        for f in ("subtotal", "tax", "total"):
            r = rules.get(f)
            if r:
                stop_label = r.get("label") or r.get("prefix")
                break
    if not hdr or not stop_label:
        return None
    # Column ranges from the first item row: locate each value's word box.
    target_row = None
    for key, segs in rows.items():
        joined = " ".join(s["text"] for s in segs)
        if first.amount and first.amount in joined and (not first.sku or first.sku in joined):
            target_row = segs
            break
    if not target_row:
        return None
    words = [w for s in target_row for w in s["words"]]
    cols = {}
    for name, val in (("sku", first.sku), ("qty", first.qty), ("uom", first.uom), ("unit_price", first.unit_price), ("amount", first.amount)):
        if not val:
            continue
        w = next((w for w in words if w["t"] == val), None)
        if w:
            cols[name] = [w["bbox"][0] - 8, w["bbox"][2] + 8]
    if "amount" not in cols or "qty" not in cols:
        return None
    return {"header": hdr["text"].strip(), "stop": stop_label, "cols": cols}


def apply_template(line_map: dict, tpl: dict) -> tuple[Extraction | None, list[str]]:
    """Read a document with a learned template. Returns (extraction, misses). Never invents values."""
    lines = line_map["lines"]
    rows = _rows(lines)
    out: dict = {}
    quotes: dict = {}
    misses = []
    for f, r in tpl["fields"].items():
        val, src = None, None
        if r["kind"] == "after_label":
            for l in lines:
                t = l["text"].strip()
                if ":" in t and t.split(":", 1)[0].strip() == r["label"]:
                    val, src = t.split(":", 1)[1].strip(), t
                    break
        elif r["kind"] == "row_segment":
            for key, segs in rows.items():
                if any(s["text"].strip() == r["label"] for s in segs):
                    vals = [s for s in segs if s["text"].strip() != r["label"]]
                    if vals:
                        val, src = vals[-1]["text"].strip(), " ".join(s["text"] for s in segs)
                    break
        elif r["kind"] == "line_equals":
            hit = next((l for l in lines if l["text"].strip() == r["value"]), None)
            if hit:
                val, src = hit["text"].strip(), hit["text"]
        elif r["kind"] == "after_prefix":
            hit = next((l for l in lines if l["text"].strip().startswith(r["prefix"] + " ")), None)
            if hit:
                val, src = hit["text"].strip()[len(r["prefix"]):].strip(), hit["text"]
        if val:
            out[f], quotes[f] = val, src
        else:
            misses.append(f)
    items = []
    t = tpl.get("table")
    if t:
        keys = sorted(rows)
        start = next((k for k in keys if any(s["text"].strip().startswith(t["header"].split()[0]) and
                                                re.search(r"(?i)description|item", " ".join(x["text"] for x in rows[k])) for s in rows[k])), None)
        stop = next((k for k in keys if any(s["text"].strip().startswith(t["stop"]) for s in rows[k])), None)
        if start and stop:
            for k in keys:
                if not (start < k < stop):
                    continue
                words = [w for s in rows[k] for w in s["words"]]
                cells: dict[str, list[str]] = defaultdict(list)
                for w in words:
                    cx = (w["bbox"][0] + w["bbox"][2]) / 2
                    col = next((n for n, (a, b) in t["cols"].items() if a <= cx <= b), "description")
                    cells[col].append(w["t"])
                amt = " ".join(cells.get("amount", []))
                if not amt or parse_amount(amt) is None:
                    continue
                items.append(ExtractedLine(description=" ".join(cells.get("description", [])), sku=" ".join(cells.get("sku", [])) or None,
                                           qty=" ".join(cells.get("qty", [])) or None, uom=" ".join(cells.get("uom", [])) or None,
                                           unit_price=" ".join(cells.get("unit_price", [])) or None, amount=amt,
                                           quote=" ".join(w["t"] for w in words)))
        else:
            misses.append("lines")
    if "tax" in out:
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", quotes.get("tax", ""))
        if m:
            out["tax_rate"] = m.group(1)
    out["tax_mode"] = "exclusive" if out.get("tax") else "none"
    if not out.get("currency"):
        m = re.search(r"\(([A-Z]{3})\)", quotes.get("total", ""))
        if m:
            out["currency"] = m.group(1)
    try:
        return Extraction(document_type="invoice", lines=items, quotes=quotes, **out), misses
    except Exception:  # noqa: BLE001
        return None, misses + ["schema"]


def agrees(template_ex: Extraction, fields: dict[str, dict]) -> tuple[bool, list[str]]:
    """Shadow comparison: does the template's reading match the verified full read on decision fields?"""
    diffs = []
    for f in TEMPLATE_DECISION_FIELDS:
        st = fields.get(f) or {}
        full = st.get("value")
        mine = getattr(template_ex, f, None)
        if full is None and mine is None:
            continue
        a, b = parse_amount(full) if f in ("total", "subtotal") else full, parse_amount(mine) if f in ("total", "subtotal") else mine
        if f not in ("total", "subtotal"):
            a, b = (str(a or "").strip().upper(), str(b or "").strip().upper())
        if a != b:
            diffs.append(f)
    return not diffs, diffs
