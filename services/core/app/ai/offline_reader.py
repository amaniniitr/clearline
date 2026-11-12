"""Fallback reader (P9): a label-based parser over the line map. Used when Claude is unavailable.

It is honest about what it is: the stage is marked "degraded", it never invents values, and every value it returns
is a substring of a located line, so grounding and the evidence rules still apply.
"""
from __future__ import annotations

import re
from collections import defaultdict

from ..domain.models import Extraction, ExtractedLine

AMT = r"[\(\-]?[\d][\d,\.]*\d\)?"

LABELS = {
    "invoice_number": r"(?i)^(?:tax\s+)?invoice\s*(?:no\.?|number|#)\s*:?\s*([A-Z0-9][\w\-/]*)",
    "invoice_date": r"(?i)^(?:invoice\s+)?date\s*:?\s*(.+)$|^invoice date\s*:?\s*(.+)$",
    "due_date": r"(?i)^due\s*date\s*:?\s*(.+)$",
    "payment_terms": r"(?i)^(?:payment\s+)?terms\s*:?\s*(.+)$",
    "po_number": r"(?i)^(?:po|p\.o\.|purchase\s+order)\s*(?:no\.?|number|#)?\s*:?\s*([A-Z]*-?\d{3,})",
    "vendor_tax_id": r"(?i)(?:tax\s*id|ein|gstin|vat(?:\s*no)?)\s*(?:\([A-Z]+\))?\s*:?\s*([A-Z0-9][A-Z0-9\-]{6,})",
    "remit_iban": r"(?i)^iban\s*:?\s*([A-Z]{2}\d{2}[A-Z0-9 ]{10,30})$",
    "remit_bank_name": r"(?i)^bank\s*:?\s*(.+)$",
    "remit_account_number": r"(?i)^account\s*(?:no\.?|number)\s*:?\s*([\d\- ]{6,})$",
    "remit_routing_number": r"(?i)^(?:routing|aba)\s*(?:no\.?|number)?\s*:?\s*(\d{9})$",
}
TOTALS = {
    "subtotal": r"(?i)^sub\s*-?total",
    "tax": r"(?i)^(?:sales\s+)?(?:tax|vat|gst|igst)(?!\s*(?:id|identification|reg|no\b|number|\(ein))",
    "freight": r"(?i)^(?:freight|shipping|delivery|carriage)",
    "total": r"(?i)^(?:total\s*(?:due|amount)?|amount\s+due|balance\s+due)",
}
LINE_RE = re.compile(rf"^(?P<sku>[A-Z0-9][A-Z0-9\-]{{2,}})\s+(?P<desc>.+?)\s+(?P<qty>\d[\d,\.]*)\s+(?P<uom>[A-Z]{{1,4}})\s+"
                     rf"(?P<unit>{AMT})\s+(?P<amt>{AMT})$")
NOTE_RE = re.compile(r"(?i)\b(important|urgent|bank(ing)? details|changed|automated|pre-approved|skip verification|remit to)\b")


def parse(line_map: dict) -> Extraction:
    lines = line_map["lines"]
    rows: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for l in lines:
        rows[(l["page"], l.get("row", l["i"]))].append(l)
    row_list = [sorted(v, key=lambda l: l["bbox"][0]) for _, v in sorted(rows.items())]

    ex: dict = {}
    quotes: dict[str, str] = {}
    head = " ".join(l["text"] for r in row_list[:3] for l in r).lower()
    doc_type = "invoice"
    if "statement" in head:
        doc_type = "statement"
    elif "credit note" in head or "credit memo" in head:
        doc_type = "credit_note"
    elif "proforma" in head or "pro forma" in head:
        doc_type = "proforma"

    for l in lines:
        t = l["text"].strip()
        for field, rx in LABELS.items():
            if field in ex:
                continue
            m = re.search(rx, t)
            if m:
                val = next(g for g in m.groups() if g) if any(m.groups()) else None
                if val:
                    ex[field] = val.strip()
                    quotes[field] = t
    # Vendor name: first heading line that is not a document title.
    for r in row_list[:3]:
        for l in r:
            if not re.search(r"(?i)^(tax\s+)?invoice$|statement|credit|bill to", l["text"]):
                ex.setdefault("vendor_name", l["text"].strip())
                quotes.setdefault("vendor_name", l["text"].strip())
                break
        if "vendor_name" in ex:
            break
    # Totals: label segment and amount segment share a visual row.
    for r in row_list:
        label = r[0]["text"].strip()
        amount = next((l["text"] for l in reversed(r) if re.fullmatch(AMT, l["text"].strip())), None)
        if amount is None:
            m = re.search(rf"({AMT})\s*$", label)
            amount = m.group(1) if m else None
        for field, rx in TOTALS.items():
            if field not in ex and re.search(rx, label) and amount:
                ex[field] = amount.strip()
                quotes[field] = " ".join(l["text"] for l in r)
                if field == "tax":
                    m = re.search(r"(\d+(?:\.\d+)?)\s*%", label)
                    if m:
                        ex["tax_rate"] = m.group(1)
                if field == "total":
                    m = re.search(r"\(([A-Z]{3})\)|\b(USD|EUR|GBP|INR)\b", label)
                    if m:
                        ex["currency"] = m.group(1) or m.group(2)
                break
    items = []
    for r in row_list:
        joined = " ".join(l["text"] for l in r)
        m = LINE_RE.match(joined)
        if m:
            items.append(ExtractedLine(description=m["desc"], sku=m["sku"], qty=m["qty"], uom=m["uom"],
                                       unit_price=m["unit"], amount=m["amt"], quote=joined))
    notes = [l["text"] for l in lines if NOTE_RE.search(l["text"]) and not l["text"].lower().startswith("remittance")]
    if notes:
        ex["notes"] = " ".join(notes)
    ex["tax_mode"] = "exclusive" if ex.get("tax") else "none"
    if ex.get("remit_iban"):
        ex["remit_iban"] = ex["remit_iban"].replace(" ", "")
    return Extraction(document_type=doc_type, lines=items, quotes=quotes, **ex)
