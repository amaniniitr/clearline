"""Resolve vendor and PO against master data. Pure: master data is passed in (and snapshotted for replay)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from rapidfuzz import fuzz

from .money import parse_amount
from .normalise import iban_norm, name_norm, po_norm


@dataclass
class VendorResolution:
    vendor_id: str | None
    method: str            # tax_id | bank | alias | fuzzy | jev | none
    candidates: list[dict] = field(default_factory=list)
    note: str = ""
    needs_judgment: tuple[str, str] | None = None  # (printed, candidate legal name) for Jev same_entity


def _digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def resolve_vendor(printed_name: str | None, tax_id: str | None, iban: str | None, vendors: list[dict],
                   banks: list[dict], aliases: list[dict]) -> VendorResolution:
    if tax_id and _digits(tax_id):
        hits = [v for v in vendors if _digits(v["tax_id"]) == _digits(tax_id)]
        if len(hits) == 1:
            return VendorResolution(hits[0]["id"], "tax_id", note=f"tax ID {tax_id} on file")
    if iban:
        hits = {b["vendor_id"] for b in banks if b.get("active") and iban_norm(b.get("iban")) == iban_norm(iban)}
        if len(hits) == 1:
            return VendorResolution(hits.pop(), "bank", note="bank account on file")
    pn = name_norm(printed_name)
    if pn:
        for a in aliases:
            if name_norm(a["alias"]) == pn:
                return VendorResolution(a["vendor_id"], "alias", note=f"known alias '{a['alias']}'")
        for v in vendors:
            if name_norm(v["legal_name"]) == pn:
                return VendorResolution(v["id"], "alias", note="legal name")
        scored = sorted(({"vendor_id": v["id"], "legal_name": v["legal_name"],
                          "score": fuzz.token_set_ratio(pn, name_norm(v["legal_name"]))} for v in vendors),
                        key=lambda c: -c["score"])
        if scored and scored[0]["score"] >= 92 and (len(scored) == 1 or scored[0]["score"] - scored[1]["score"] >= 10):
            return VendorResolution(scored[0]["vendor_id"], "fuzzy", scored[:3], note="name similarity")
        if scored and scored[0]["score"] >= 70:
            return VendorResolution(None, "none", scored[:3], needs_judgment=(printed_name, scored[0]["legal_name"]))
    return VendorResolution(None, "none", note="no match in vendor master")


@dataclass
class PoResolution:
    po_id: str | None
    status: str            # explicit | inferred | none | not_found | other_vendor | closed
    printed: str | None
    candidates: list[dict] = field(default_factory=list)
    note: str = ""


def resolve_po(printed: str | None, vendor_id: str | None, pos: list[dict], po_lines: list[dict],
               inv_lines: list[dict]) -> PoResolution:
    norm = po_norm(printed)
    if norm:
        po = next((p for p in pos if p["id"] == norm), None)
        if po is None:
            return PoResolution(None, "not_found", printed)
        if vendor_id and po["vendor_id"] != vendor_id:
            return PoResolution(po["id"], "other_vendor", printed)
        if po["status"] != "open":
            return PoResolution(po["id"], "closed", printed)
        return PoResolution(po["id"], "explicit", printed, note="printed on the invoice")
    # Inference: rank the vendor's open POs by how many invoice lines they explain.
    open_pos = [p for p in pos if p["vendor_id"] == vendor_id and p["status"] == "open"]
    ranked = []
    for p in open_pos:
        pl = [l for l in po_lines if l["po_id"] == p["id"]]
        hits = 0
        for il in inv_lines:
            if any((il.get("sku") and il["sku"] == l["sku"]) or fuzz.token_set_ratio(il["description"], l["description"]) >= 90
                   for l in pl):
                hits += 1
        ranked.append({"po_id": p["id"], "lines_matched": hits, "lines_total": len(inv_lines), "po_lines": len(pl)})
    ranked.sort(key=lambda r: -r["lines_matched"])
    if ranked and inv_lines and ranked[0]["lines_matched"] == len(inv_lines) and \
            (len(ranked) == 1 or ranked[1]["lines_matched"] < ranked[0]["lines_matched"]):
        return PoResolution(ranked[0]["po_id"], "inferred", None, ranked,
                            note=f"{ranked[0]['po_id']} matches {len(inv_lines)} of {len(inv_lines)} lines")
    return PoResolution(None, "none", None, ranked, note=f"{len(open_pos)} open POs checked, no unique fit")
