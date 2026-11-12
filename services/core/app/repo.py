"""SQL per aggregate. The only module (with workflow/commit) that touches tables directly."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from . import db


def master_snapshot(vendor_id: str | None) -> dict:
    """Everything the deterministic stages read. Stored per run so policy replay is exact."""
    vendors = db.q("SELECT id, legal_name, tax_id, status, requires_po, domains, phone, category FROM vendors ORDER BY id")
    aliases = db.q("SELECT vendor_id, alias FROM vendor_aliases")
    banks = db.q("""SELECT id, vendor_id, iban, account_number, routing_number, bank_name, verified_at, verified_by,
                           callback_phone, added_at, active FROM vendor_bank_accounts""")
    snap = {"vendors": vendors, "aliases": aliases, "banks": banks, "pos": [], "po_lines": [], "received": {},
            "billed": {}, "po_billed": {}, "history": [], "paid_before": 0}
    if vendor_id:
        snap.update(vendor_scope(vendor_id, vendors, banks))
    return snap


def vendor_scope(vendor_id: str, vendors: list[dict], banks: list[dict]) -> dict:
    v = next((x for x in vendors if x["id"] == vendor_id), None)
    related = {vendor_id}
    if v and v.get("tax_id"):
        related |= {x["id"] for x in vendors if x.get("tax_id") == v["tax_id"]}
    my_ibans = {b["iban"] for b in banks if b["vendor_id"] == vendor_id and b.get("iban")}
    related |= {b["vendor_id"] for b in banks if b.get("iban") in my_ibans}
    pos = db.q("SELECT id, vendor_id, currency, status, total, blanket, issued_on FROM purchase_orders ORDER BY id")
    vendor_po_ids = [p["id"] for p in pos if p["vendor_id"] == vendor_id]
    po_lines = db.q("SELECT * FROM po_lines WHERE po_id = ANY(%s) ORDER BY po_id, line_no", (vendor_po_ids,))
    received = {r["po_line_id"]: r["qty"] for r in db.q(
        "SELECT po_line_id, sum(qty) qty FROM grn_lines WHERE po_line_id = ANY(%s) GROUP BY 1", ([l["id"] for l in po_lines],))}
    billed_rows = db.q("""SELECT po_line_id, po_id, invoice_id, qty, amount FROM billed_ledger
                          WHERE vendor_id = %s AND state <> 'reversed'""", (vendor_id,))
    history = db.q("""SELECT id, vendor_id, document_type, invoice_number, invoice_number_norm, total, invoice_date, state, po_id
                      FROM invoices WHERE vendor_id = ANY(%s)""", (list(related),))
    paid_before = db.q1("SELECT count(*) n FROM erp_bills WHERE vendor_id = %s", (vendor_id,))["n"]
    grns = db.q("""SELECT g.id, g.po_id, g.received_on, g.posted_at, l.po_line_id, l.qty FROM goods_receipts g
                   JOIN grn_lines l ON l.grn_id = g.id WHERE g.po_id = ANY(%s) ORDER BY g.posted_at""", (vendor_po_ids,))
    return {"pos": pos, "po_lines": po_lines, "received": received, "billed_rows": billed_rows, "history": history,
            "paid_before": paid_before, "related_vendor_ids": sorted(related), "grns": grns}


def billed_before(snap: dict, invoice_id: int | None) -> tuple[dict, dict, Decimal]:
    """Per PO line and per PO: what other invoices already billed. Plus what this invoice already had paid (tranches)."""
    per_line: dict[str, Decimal] = defaultdict(Decimal)
    per_po: dict[str, Decimal] = defaultdict(Decimal)
    own_paid = Decimal("0")
    for r in snap.get("billed_rows", []):
        if r["invoice_id"] == invoice_id:
            own_paid += Decimal(str(r["amount"]))
            continue
        if r["po_line_id"]:
            per_line[r["po_line_id"]] += Decimal(str(r["qty"] or 0))
        if r["po_id"]:
            per_po[r["po_id"]] += Decimal(str(r["amount"]))
    return per_line, per_po, own_paid
