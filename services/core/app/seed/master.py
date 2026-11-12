"""Seed master data: vendors, verified bank accounts, POs, goods receipts, and billing history.
Dates are relative to today so the demo never goes stale."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

from .. import db
from ..domain.normalise import invoice_number_norm
from ..policy import catalog, engine

TODAY = date.today()


def ago(days: int) -> date:
    return TODAY - timedelta(days=days)


VENDORS = [
    # id, legal name, tax id (EIN), domains, phone, category, aliases, iban, verified days ago
    ("V-1001", "Acme Supply Co.", "84-1234567", ["acme-supply.com"], "+1 415 555 0142", "industrial hardware",
     ["ACME SUPPLY", "Acme Supply Company"], "GB29NWBK60161331926819", 270),
    ("V-1002", "Northwind Components Ltd", "27-5551234", ["northwind-components.com"], "+1 312 555 0187", "electronics",
     ["Northwind Components", "NORTHWIND COMP LTD"], "GB31BARC60161320201555", 380),
    ("V-1003", "Globex Industrial Inc.", "45-6781230", ["globex-industrial.com"], "+1 646 555 0110", "conveyor parts",
     ["Globex Industrial", "GLOBEX IND"], "GB07HBUK60161340051512", 300),
    ("V-1004", "Initech Office Supplies LLC", "61-2223334", ["initech-supplies.com"], "+1 512 555 0199", "office supplies",
     ["Initech", "Initech Office Supplies"], "GB48MIDL60161377665544", 200),
    ("V-1005", "Shakti Fasteners Pvt Ltd", "27AAPCS1234K1Z2", ["shaktifasteners.in"], "+91 22 5550 0142", "fasteners (India)",
     ["SHAKTI FASTENERS", "Shakti Fasteners Private Limited"], "IN-HDFC0000240-50200012345678", 420),
    ("V-1006", "Rijnmond Industrietechniek B.V.", "NL853746333B01", ["rijnmond-it.nl"], "+31 10 555 0170", "industrial parts (EU)",
     ["Rijnmond Industrietechniek", "RIJNMOND IT BV"], "NL91ABNA0417164300", 330),
]

# po id, vendor, issued days ago, blanket, lines[(sku, desc, qty, unit, gl, cost centre)], receipts[(grn, days ago, {line_no: qty})]
POS = [
    ("PO-4404", "V-1001", 30, False, [("BR-M8", "Steel mounting bracket M8", 200, "12.50", "5010-Materials", "CC-PLANT1"),
                                      ("HX-M8", "Hex bolt M8 x 40mm (box of 100)", 50, "18.00", "5010-Materials", "CC-PLANT1"),
                                      ("WS-M8", "Flat washer M8 (box of 100)", 40, "6.50", "5010-Materials", "CC-PLANT1")],
     [("GRN-870", 6, {1: 200, 2: 50, 3: 40})]),
    ("PO-4410", "V-1001", 25, False, [("SH-72", "Industrial steel shelving unit 72in", 20, "245.00", "1520-Equipment", "CC-WH2")],
     [("GRN-874", 5, {1: 20})]),
    ("PO-4405", "V-1001", 20, False, [("BR-M10", "Steel mounting bracket M10", 100, "14.00", "5010-Materials", "CC-PLANT1")],
     [("GRN-876", 4, {1: 100})]),
    ("PO-4406", "V-1001", 18, False, [("CL-50", "Pipe clamp 50mm", 300, "3.20", "5010-Materials", "CC-PLANT1")],
     [("GRN-877", 3, {1: 300})]),
    ("PO-4420", "V-1002", 40, False, [("NW-CTRL2", "Motor controller board v2", 100, "100.00", "5020-Components", "CC-ASSY")],
     [("GRN-880", 7, {1: 60})]),
    ("PO-4430", "V-1003", 60, False, [("GX-BELT", "Conveyor belt replacement section 2m", 100, "150.00", "5030-Maintenance", "CC-PLANT2")],
     [("GRN-861", 45, {1: 50}), ("GRN-868", 20, {1: 60})]),  # 110 received: vendor over-shipped, receiving accepted
    ("PO-4440", "V-1004", 15, False, [("PP-A4-80", "A4 copy paper 80gsm (box of 5 reams)", 50, "32.00", "6100-Office", "CC-ADMIN"),
                                      ("TN-26A", "Toner cartridge HP 26A", 10, "89.00", "6100-Office", "CC-ADMIN")],
     [("GRN-879", 4, {1: 50, 2: 10})]),
    ("PO-4441", "V-1004", 35, False, [("ST-CHAIR", "Ergonomic task chair", 8, "189.00", "1530-Furniture", "CC-ADMIN")],
     [("GRN-866", 25, {1: 8})]),
    # Extra open POs for Acme so PO inference has real candidates to rule out.
    ("PO-4401", "V-1001", 14, False, [("BR-M8", "Steel mounting bracket M8", 150, "12.50", "5010-Materials", "CC-PLANT1"),
                                      ("NT-M8", "Hex nut M8 (box of 100)", 30, "9.00", "5010-Materials", "CC-PLANT1"),
                                      ("WS-M8", "Flat washer M8 (box of 100)", 30, "6.50", "5010-Materials", "CC-PLANT1")],
     [("GRN-878", 2, {1: 150, 2: 30, 3: 30})]),
    ("PO-4442", "V-1004", 12, False, [("LMP-LED", "LED desk lamp, adjustable", 12, "45.00", "6100-Office", "CC-ADMIN")],
     [("GRN-881", 3, {1: 12})]),
    # Acme blanket PO for routine consumables: the learning-loop series bills against it.
    ("PO-4499", "V-1001", 45, True, [("BR-M8", "Steel mounting bracket M8", 5000, "12.50", "5010-Materials", "CC-PLANT1"),
                                     ("HX-M8", "Hex bolt M8 x 40mm (box of 100)", 1000, "18.00", "5010-Materials", "CC-PLANT1"),
                                     ("WS-M8", "Flat washer M8 (box of 100)", 1000, "6.50", "5010-Materials", "CC-PLANT1")],
     [("GRN-860", 40, {1: 5000, 2: 1000, 3: 1000})]),
    # India (INR, GST)
    ("PO-4450", "V-1005", 20, False, [("SF-HB12", "Hex bolt M12 x 60 zinc plated (pack of 50)", 400, "1850.00", "5010-Materials", "CC-PUNE"),
                                      ("SF-NY12", "Nylock nut M12 (pack of 100)", 250, "960.00", "5010-Materials", "CC-PUNE")],
     [("GRN-884", 5, {1: 400, 2: 250})]),
    # EU (EUR, VAT-inclusive pricing); three open POs so the right one must be inferred.
    ("PO-4460", "V-1006", 21, False, [("RM-BRG62", "Kogellager 6204-2RS / Ball bearing 6204-2RS", 200, "17.50", "5040-Spares", "CC-RTM"),
                                      ("RM-SEAL40", "Asafdichting 40x62x7 / Shaft seal 40x62x7", 150, "7.00", "5040-Spares", "CC-RTM"),
                                      ("RM-BELT-A42", "V-snaar A42 / V-belt A42", 120, "17.50", "5040-Spares", "CC-RTM")],
     [("GRN-885", 4, {1: 200, 2: 150, 3: 120})]),
    ("PO-4461", "V-1006", 30, False, [("RM-BRG62", "Kogellager 6204-2RS / Ball bearing 6204-2RS", 80, "17.50", "5040-Spares", "CC-RTM"),
                                      ("RM-CPL50", "Klauwkoppeling 50 mm / Jaw coupling 50 mm", 40, "38.00", "5040-Spares", "CC-RTM")],
     [("GRN-871", 15, {1: 80, 2: 40})]),
    ("PO-4462", "V-1006", 9, False, [("RM-CHN08", "Rollenketting 08B-1 / Roller chain 08B-1 (5 m)", 60, "54.00", "5040-Spares", "CC-RTM")],
     []),
]

# Historical (already processed) invoices: vendor, number, po, days ago, [(line_no, qty)]
HISTORY = [
    ("V-1001", "INV-0998", "PO-4380", 70, None, D("2140.00")),
    ("V-1002", "NW-7702", "PO-4390", 90, None, D("5600.00")),
    ("V-1003", "GX-501", "PO-4430", 40, [(1, 40)], None),
    ("V-1003", "GX-502", "PO-4430", 18, [(1, 40)], None),
    ("V-1004", "INV-0042", "PO-4441", 21, [(1, 8)], None),
    ("V-1005", "SF/25-26/0871", "PO-4381", 60, None, D("412500.00")),
    ("V-1006", "RM-2026-0418", "PO-4382", 50, None, D("2963.50")),
]
CURRENCY = {"V-1005": "INR", "V-1006": "EUR"}


def seed() -> dict:
    db.migrate(reset=True)
    now = datetime.now(timezone.utc)
    with db.tx() as c:
        for vid, name, tax, domains, phone, cat, aliases, iban, vdays in VENDORS:
            c.execute("INSERT INTO vendors (id, legal_name, tax_id, domains, phone, category) VALUES (%s,%s,%s,%s,%s,%s)",
                      (vid, name, tax, domains, phone, cat))
            for a in aliases:
                c.execute("INSERT INTO vendor_aliases VALUES (%s,%s)", (vid, a))
            acct = routing = None
            if iban.startswith("IN-"):  # Indian account: IFSC + account number, no IBAN
                _, routing, acct = iban.split("-", 2)
                iban = None
            c.execute("""INSERT INTO vendor_bank_accounts (vendor_id, iban, account_number, routing_number, bank_name, verified_at,
                         verified_by, callback_phone, added_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                      (vid, iban, acct, routing, "On file", now - timedelta(days=vdays), "controller.priya", phone, now - timedelta(days=vdays)))
        # closed historical POs referenced by history
        for po, vid in [("PO-4380", "V-1001"), ("PO-4390", "V-1002"), ("PO-4381", "V-1005"), ("PO-4382", "V-1006")]:
            c.execute("INSERT INTO purchase_orders (id, vendor_id, status, total, issued_on, currency) VALUES (%s,%s,'closed',%s,%s,%s)",
                      (po, vid, D("0"), ago(120), CURRENCY.get(vid, "USD")))
        for po, vid, days, blanket, lines, receipts in POS:
            total = sum(D(str(q)) * D(u) for _, _, q, u, _, _ in lines)
            c.execute("INSERT INTO purchase_orders (id, vendor_id, total, blanket, issued_on, requester, currency) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                      (po, vid, total, blanket, ago(days), "ops.manager", CURRENCY.get(vid, "USD")))
            for n, (sku, desc, q, u, gl, cc) in enumerate(lines, start=1):
                c.execute("""INSERT INTO po_lines (id, po_id, line_no, sku, description, qty, unit_price, gl_account, cost_center)
                             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (f"{po}-{n}", po, n, sku, desc, q, D(u), gl, cc))
            for grn, gdays, qtys in receipts:
                c.execute("INSERT INTO goods_receipts (id, po_id, received_on, posted_at) VALUES (%s,%s,%s,%s)",
                          (grn, po, ago(gdays), now - timedelta(days=gdays)))
                for ln, q in qtys.items():
                    c.execute("INSERT INTO grn_lines VALUES (%s,%s,%s)", (grn, f"{po}-{ln}", q))
        for vid, num, po, days, lines, amount in HISTORY:
            if lines:
                amt = D("0")
                for ln, q in lines:
                    price = c.execute("SELECT unit_price FROM po_lines WHERE id=%s", (f"{po}-{ln}",)).fetchone()["unit_price"]
                    amt += price * q
            else:
                amt = amount
            inv = c.execute("""INSERT INTO invoices (document_type, invoice_number, invoice_number_norm, vendor_id, po_id, currency,
                                 invoice_date, subtotal, tax, freight, total, state, external, created_at)
                               VALUES ('invoice',%s,%s,%s,%s,%s,%s,%s,0,0,%s,'approved',true,%s) RETURNING id""",
                            (num, invoice_number_norm(num), vid, po, CURRENCY.get(vid, "USD"), ago(days), amt, amt,
                             now - timedelta(days=days))).fetchone()["id"]
            if lines:
                for ln, q in lines:
                    price = c.execute("SELECT unit_price FROM po_lines WHERE id=%s", (f"{po}-{ln}",)).fetchone()["unit_price"]
                    c.execute("""INSERT INTO billed_ledger (po_line_id, po_id, invoice_id, vendor_id, invoice_number_norm, qty, amount, created_at)
                                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                              (f"{po}-{ln}", po, inv, vid, invoice_number_norm(num), q, price * q, now - timedelta(days=days)))
            else:
                c.execute("""INSERT INTO billed_ledger (po_id, invoice_id, vendor_id, invoice_number_norm, amount, created_at)
                             VALUES (%s,%s,%s,%s,%s,%s)""", (po, inv, vid, invoice_number_norm(num), amt, now - timedelta(days=days)))
            c.execute("INSERT INTO erp_bills (id, invoice_id, vendor_id, amount, currency, lines, posted_at) VALUES (%s,%s,%s,%s,%s,'[]',%s)",
                      (f"B-{1000 + inv}", inv, vid, amt, CURRENCY.get(vid, "USD"), now - timedelta(days=days)))
        jdm = engine.build_jdm()
        c.execute("""INSERT INTO policies (version, jdm, line_jdm, text_md, status, drafted_by, approved_by, approved_at)
                     VALUES ('v1', %s, '{}', %s, 'active', 'seed', 'controller.priya', now())""",
                  (db.J(jdm), engine.policy_text(catalog.DEFAULT_TOLERANCES, catalog.VENDOR_TOLERANCES)))
    return {"vendors": len(VENDORS), "pos": len(POS), "history": len(HISTORY)}


if __name__ == "__main__":
    print(seed())
