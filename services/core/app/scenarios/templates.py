"""Six vendor invoice layouts (HTML → PDF). Different vendors format invoices differently, as in the brief.

acme_classic   US, two-column header, SKU table, totals bottom-right (Acme's usual template)
acme_v2        Acme's *new* template (for the "layout changed" learning demo)
northwind      Minimal modern, totals block on the left, different labels ("Invoice #", "Issued", "Your PO")
globex         Dense gridded table, "Bill No." / "Order Ref" / "Amount Payable"
initech        Typewriter-style plain invoice (also used scanned)
gst_bilingual  Indian GST tax invoice, English + Hindi, GSTIN, HSN, CGST/SGST, ₹ with lakh grouping
eu_vat         Dutch/English VAT invoice, decimal comma, prices including 21% BTW
statement      Statement of account (not an invoice)
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal as D
from html import escape

C2 = D("0.01")


def q(x: D) -> D:
    return x.quantize(C2, rounding=ROUND_HALF_UP)


def fmt(x: D, loc: str = "us") -> str:
    x = q(x)
    neg = x < 0
    x = abs(x)
    whole, frac = f"{x:.2f}".split(".")
    if loc == "in":  # 12,34,567.89
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail]) if groups else tail
        out = f"{s}.{frac}"
    elif loc == "eu":  # 1.234,56
        out = f"{int(whole):,}".replace(",", ".") + "," + frac
    else:
        out = f"{int(whole):,}.{frac}"
    return f"-{out}" if neg else out


def totals(s: dict) -> dict:
    lines = [(sku, desc, qty, D(u), q(D(str(qty)) * D(u)), extra) for sku, desc, qty, u, *rest in s["lines"]
             for extra in [rest[0] if rest else None]]
    sub = sum((l[4] for l in lines), D("0"))
    fr = D(s["freight"]) if s.get("freight") else D("0")
    rate = D(s["tax_rate"]) if s.get("tax_rate") else D("0")
    if s.get("tax_mode") == "inclusive":
        total = sub + fr
        tax = q(total - total / (1 + rate / 100)) if rate else D("0")
    else:
        tax = q(sub * rate / 100)
        total = sub + tax + fr
    return {"lines": lines, "subtotal": sub, "tax": tax, "freight": fr, "total": total, "rate": rate}


BASE_CSS = """
@page { size: Letter; margin: 0; }
* { box-sizing: border-box; }
body { margin: 0; font-family: Helvetica, Arial, sans-serif; color: #111; font-size: 10.5pt; }
.page { width: 8.5in; min-height: 11in; padding: 0.6in 0.65in; position: relative; }
table { border-collapse: collapse; width: 100%; }
.r { text-align: right; } .muted { color: #555; } .b { font-weight: 700; }
"""


def _rows(t, loc, sym="", cols=("sku", "desc", "qty", "uom", "unit", "amt")):
    out = []
    for sku, desc, qty, u, amt, extra in t["lines"]:
        cells = {"sku": escape(sku), "desc": escape(desc), "qty": str(qty), "uom": "EA", "unit": sym + fmt(u, loc),
                 "amt": sym + fmt(amt, loc), "hsn": escape(extra or "")}
        out.append("<tr>" + "".join(f'<td class="{"r" if c in ("qty", "unit", "amt") else ""}">{cells[c]}</td>' for c in cols) + "</tr>")
    return "\n".join(out)


def acme_classic(s: dict) -> str:
    v, t = s["vendor"], totals(s)
    meta = ([("Invoice No:", s["number"])] if s.get("number") else []) + [("Invoice Date:", s["date"]), ("Due Date:", s["due"]),
                                                                          ("Terms:", s["terms"])] + ([("PO Number:", s["po"])] if s.get("po") else [])
    tamper = s.get("tamper")
    iban_html = (f'<div style="position:relative;height:16px">IBAN: <span style="position:absolute;left:34px;top:0;color:#fff">{tamper["hidden"]}</span>'
                 f'<img src="{tamper["img"]}" style="position:absolute;left:34px;top:1px;height:13px"></div>') if tamper else f"<div>IBAN: {v['iban']}</div>"
    notes = "".join(f"<div class='{'b' if k == 0 else ''}'>{escape(n)}</div>" for k, n in enumerate(s.get("notes", [])))
    return f"""<html><head><style>{BASE_CSS}
.bar {{ position:absolute; top:0; left:0; right:0; height:16px; background:#1e3a8a; }}
h1 {{ font-size: 20pt; margin: 0 0 6px; }} .title {{ color:#1e3a8a; font-size: 24pt; text-align:right; margin:0 }}
.meta td {{ padding: 1px 0 1px 8px; }} .items th {{ background:#eef2f7; text-align:left; padding:5px 6px; font-size:9pt }}
.items td {{ padding:4px 6px; font-size:9.5pt }} .tot td {{ padding:2px 0 2px 16px }}
</style></head><body><div class="page"><div class="bar"></div>
<table><tr><td style="vertical-align:top"><h1>{escape(v['name'])}</h1>
<div class="muted">{'<br>'.join(map(escape, v['addr']))}<br>Tax ID (EIN): {v['tax_id']}<br>{v['email']}</div></td>
<td style="vertical-align:top"><p class="title">INVOICE</p><table class="meta" style="width:auto;margin-left:auto">
{''.join(f'<tr><td class="r">{a}</td><td>{escape(b)}</td></tr>' for a, b in meta)}</table></td></tr></table>
<div style="margin-top:28px"><div class="b">Bill To:</div>{'<br>'.join(s['bill_to'])}</div>
<table class="items" style="margin-top:28px"><tr><th>SKU</th><th>Description</th><th class="r">Qty</th><th>UoM</th><th class="r">Unit Price</th><th class="r">Amount</th></tr>
{_rows(t, 'us')}</table>
<table class="tot" style="width:auto;margin:18px 0 0 auto;border-top:1px solid #333">
<tr><td class="r">Subtotal:</td><td class="r">{fmt(t['subtotal'])}</td></tr>
{f"<tr><td class='r'>Sales Tax ({s['tax_rate']}%):</td><td class='r'>{fmt(t['tax'])}</td></tr>" if s.get('tax_rate') else ''}
{f"<tr><td class='r'>Freight:</td><td class='r'>{fmt(t['freight'])}</td></tr>" if s.get('freight') else ''}
<tr class="b"><td class="r">Total Due (USD):</td><td class="r">{fmt(t['total'])}</td></tr></table>
<div style="margin-top:34px"><div class="b">Remittance details</div><div>Bank: {v['bank']}</div><div>Account name: {escape(v['name'])}</div>
{iban_html}</div><div style="margin-top:22px;font-size:9.5pt">{notes}</div>
<div class="muted" style="position:absolute;bottom:0.5in;font-size:8pt;font-style:italic">Thank you for your business. Questions: {v['email']}</div>
</div></body></html>"""


def acme_v2(s: dict) -> str:
    v, t = s["vendor"], totals(s)
    return f"""<html><head><style>{BASE_CSS}
body {{ font-family: Georgia, serif; }} .hdr {{ border-bottom: 3px solid #b45309; padding-bottom: 10px; }}
.items th {{ border-bottom: 2px solid #b45309; text-align:left; padding:6px 4px; font-size:9pt; color:#b45309 }}
.items td {{ padding:6px 4px; border-bottom: 1px solid #eee }}
</style></head><body><div class="page">
<div class="hdr"><span style="font-size:22pt;color:#b45309" class="b">ACME</span> <span style="font-size:11pt">Supply Co.</span>
<span style="float:right;font-size:14pt;margin-top:8px">Billing Statement of Charges — Invoice</span></div>
<table style="margin-top:16px"><tr>
<td style="width:50%;vertical-align:top">Reference&nbsp;&nbsp;<b>{s['number']}</b><br>Dated&nbsp;&nbsp;{s['date']}<br>Pay by&nbsp;&nbsp;{s['due']}<br>Customer order&nbsp;&nbsp;{s.get('po') or '—'}</td>
<td style="vertical-align:top">Sold to<br>{'<br>'.join(s['bill_to'][:2])}</td></tr></table>
<table class="items" style="margin-top:22px"><tr><th>Item code</th><th>Item</th><th class="r">Units</th><th>Per</th><th class="r">Rate</th><th class="r">Line total</th></tr>
{_rows(t, 'us')}</table>
<div style="margin-top:16px;width:45%;margin-left:auto">
<div>Net amount <span style="float:right">{fmt(t['subtotal'])}</span></div>
<div>Tax {s['tax_rate']}% <span style="float:right">{fmt(t['tax'])}</span></div>
<div class="b" style="border-top:1px solid #333;margin-top:4px;padding-top:4px">Balance payable USD <span style="float:right">{fmt(t['total'])}</span></div></div>
<div style="margin-top:40px;font-size:9.5pt">{escape(v['name'])} · {' · '.join(v['addr'])} · EIN {v['tax_id']}<br>
Remit to {v['bank']} · IBAN {v['iban']}</div></div></body></html>"""


def northwind(s: dict) -> str:
    v, t = s["vendor"], totals(s)
    return f"""<html><head><style>{BASE_CSS}
body {{ font-family: 'Helvetica Neue', Helvetica, sans-serif; }}
.brand {{ font-size: 16pt; letter-spacing: 0.04em; }} .lbl {{ color:#64748b; font-size:8.5pt; text-transform: uppercase; letter-spacing: .06em }}
.items th {{ color:#64748b; font-weight:500; text-align:left; font-size:8.5pt; padding:6px 0; border-bottom:1px solid #cbd5e1 }}
.items td {{ padding:7px 0; border-bottom:1px solid #f1f5f9 }}
</style></head><body><div class="page">
<div class="brand b">{v.get('brand', escape(v['name']))}</div>
<div class="muted" style="font-size:9pt">{escape(v['name'])} · {' · '.join(v['addr'])} · EIN {v['tax_id']}</div>
<table style="margin-top:34px"><tr>
<td style="vertical-align:top;width:34%"><div class="lbl">Invoice #</div><div class="b" style="font-size:13pt">{s['number']}</div></td>
<td style="vertical-align:top"><div class="lbl">Issued</div>{s['date']}<div class="lbl" style="margin-top:6px">Payment due</div>{s['due']} ({s['terms']})</td>
<td style="vertical-align:top"><div class="lbl">Your PO</div>{s.get('po') or '—'}<div class="lbl" style="margin-top:6px">Billed to</div>{s['bill_to'][0]}</td></tr></table>
<table class="items" style="margin-top:30px"><tr><th>Part</th><th>Description</th><th class="r">Qty</th><th>Unit</th><th class="r">Price</th><th class="r">Amount</th></tr>
{_rows(t, 'us')}</table>
<table style="width:42%;margin-top:22px"><tr><td class="lbl">Subtotal</td><td class="r">${fmt(t['subtotal'])}</td></tr>
{f"<tr><td class='lbl'>Tax {s['tax_rate']}%</td><td class='r'>${fmt(t['tax'])}</td></tr>" if s.get('tax_rate') else ''}
<tr><td class="lbl b" style="padding-top:6px">Amount due</td><td class="r b" style="font-size:13pt;padding-top:6px">${fmt(t['total'])}</td></tr></table>
<div style="margin-top:40px;font-size:9pt"><span class="lbl">Pay to</span><br>{v['bank']} · IBAN {v['iban']}</div>
</div></body></html>"""


def globex(s: dict) -> str:
    v, t = s["vendor"], totals(s)
    return f"""<html><head><style>{BASE_CSS}
body {{ font-size: 9.5pt; }} .g td, .g th {{ border: 1px solid #333; padding: 4px 5px; }} .g th {{ background: #ddd; font-size: 8.5pt }}
.box td {{ border: 1px solid #333; padding: 4px 6px }}
</style></head><body><div class="page">
<table><tr><td><div style="font-size:18pt" class="b">{escape(v['name'].upper())}</div>{' | '.join(v['addr'])}<br>EIN {v['tax_id']} | {v['email']}</td>
<td class="r" style="font-size:16pt;vertical-align:top" class="b">TAX INVOICE</td></tr></table>
<table class="box" style="margin-top:14px"><tr><td>Bill No.: <b>{s['number']}</b></td><td>Bill Date: {s['date']}</td><td>Order Ref: {s.get('po') or ''}</td></tr>
<tr><td colspan="2">Customer: {', '.join(s['bill_to'][:1] + s['bill_to'][2:])}</td><td>Terms: {s['terms']} · Due {s['due']}</td></tr></table>
<table class="g" style="margin-top:14px"><tr><th>#</th><th>Code</th><th>Description of goods</th><th class="r">Qty</th><th>UoM</th><th class="r">Rate</th><th class="r">Value</th></tr>
{''.join(f"<tr><td>{k + 1}</td><td>{escape(l[0])}</td><td>{escape(l[1])}</td><td class='r'>{l[2]}</td><td>EA</td><td class='r'>{fmt(l[3])}</td><td class='r'>{fmt(l[4])}</td></tr>" for k, l in enumerate(t['lines']))}
<tr><td colspan="6" class="r">Sub Total</td><td class="r">{fmt(t['subtotal'])}</td></tr>
{f"<tr><td colspan='6' class='r'>Sales Tax @ {s['tax_rate']}%</td><td class='r'>{fmt(t['tax'])}</td></tr>" if s.get('tax_rate') else ''}
<tr><td colspan="6" class="r b">Amount Payable (USD)</td><td class="r b">{fmt(t['total'])}</td></tr></table>
<div style="margin-top:20px">Bank: {v['bank']} &nbsp; IBAN: {v['iban']}</div>
<div style="margin-top:30px" class="muted">E. &amp; O.E. Goods once accepted are billed against the order reference above.</div>
</div></body></html>"""


def initech(s: dict) -> str:
    v, t = s["vendor"], totals(s)
    rows = "\n".join(f"{l[0]:<10} {l[1][:34]:<34} {l[2]:>5}  {fmt(l[3]):>9}  {fmt(l[4]):>10}" for l in t["lines"])
    head = (f"INVOICE NO:   {s['number']}\n" if s.get("number") else "") + \
        f"DATE:         {s['date']}\nDUE:          {s['due']}\nTERMS:        {s['terms']}\nPO NO:        {s.get('po') or ''}"
    body = f"""{v['name'].upper()}
{chr(10).join(v['addr'])}
TAX ID: {v['tax_id']}     {v['email']}

                                  I N V O I C E

{head}

SOLD TO:      {s['bill_to'][0]}
              {s['bill_to'][2]}, {s['bill_to'][3]}

ITEM       DESCRIPTION                          QTY      PRICE      AMOUNT
---------------------------------------------------------------------------
{rows}
---------------------------------------------------------------------------
                                              SUBTOTAL:      {fmt(t['subtotal']):>10}
""" + (f"                                              SALES TAX {s['tax_rate']}%: {fmt(t['tax']):>9}\n" if s.get("tax_rate") else "") + \
        (f"                                              FREIGHT:       {fmt(t['freight']):>10}\n" if s.get("freight") else "") + \
        f"""                                              TOTAL USD:     {fmt(t['total']):>10}

PLEASE REMIT TO: {v['bank']}  IBAN: {v['iban']}
"""
    return f"""<html><head><style>{BASE_CSS} body {{ font-family: 'Courier New', Courier, monospace; font-size: 10pt }}</style></head>
<body><div class="page"><pre style="font-family:inherit;margin:0">{escape(body)}</pre></div></body></html>"""


def gst_bilingual(s: dict) -> str:
    v, t = s["vendor"], totals(s)
    half = s["tax_rate_half"]
    cg = q(t["subtotal"] * D(half) / 100)
    total = t["subtotal"] + cg + cg
    rows = "".join(f"<tr><td>{k + 1}</td><td>{escape(l[1])}</td><td>{l[5]}</td><td class='r'>{l[2]}</td><td>Pack</td>"
                   f"<td class='r'>{fmt(l[3], 'in')}</td><td class='r'>{fmt(l[4], 'in')}</td></tr>" for k, l in enumerate(t["lines"]))
    return f"""<html><head><style>{BASE_CSS}
body {{ font-family: 'Kohinoor Devanagari', 'Devanagari Sangam MN', Helvetica, sans-serif; font-size: 9.5pt }}
.g td, .g th {{ border: 1px solid #444; padding: 4px 5px; }} .g th {{ background:#f3e8d6; font-size:8.5pt }} .hi {{ color:#7c2d12 }}
</style></head><body><div class="page">
<div style="text-align:center"><div class="b" style="font-size:16pt">कर बीजक / TAX INVOICE</div>
<div class="b" style="font-size:14pt;margin-top:6px">{escape(v['name'])}</div>
<div>{', '.join(v['addr'])}</div><div>GSTIN: {v['tax_id']} &nbsp;|&nbsp; {v['email']}</div></div>
<table class="g" style="margin-top:12px"><tr>
<td>बीजक संख्या / Invoice No: <b>{s['number']}</b></td><td>दिनांक / Invoice Date: {s['date']}</td></tr>
<tr><td>क्रय आदेश / PO No: {s.get('po') or ''}</td><td>भुगतान की अंतिम तिथि / Due Date: {s['due']}</td></tr>
<tr><td colspan="2">प्राप्तकर्ता / Bill To: {escape(s['bill_to_in'])} &nbsp;|&nbsp; GSTIN: {s['buyer_gstin']} &nbsp;|&nbsp; Place of Supply: Maharashtra (27)</td></tr></table>
<table class="g" style="margin-top:10px"><tr><th>#</th><th>विवरण / Description</th><th>HSN</th><th class="r">Qty</th><th>Unit</th><th class="r">Rate (₹)</th><th class="r">Taxable Value (₹)</th></tr>
{rows}
<tr><td colspan="6" class="r">कर योग्य मूल्य / Taxable Value</td><td class="r">{fmt(t['subtotal'], 'in')}</td></tr>
<tr><td colspan="6" class="r">CGST @ {half}%</td><td class="r">{fmt(cg, 'in')}</td></tr>
<tr><td colspan="6" class="r">SGST @ {half}%</td><td class="r">{fmt(cg, 'in')}</td></tr>
<tr><td colspan="6" class="r">कुल कर / Total Tax ({D(half) * 2}%)</td><td class="r">{fmt(cg + cg, 'in')}</td></tr>
<tr><td colspan="6" class="r b">कुल राशि / Grand Total (INR)</td><td class="r b">₹ {fmt(total, 'in')}</td></tr></table>
<div style="margin-top:8px">राशि शब्दों में / Amount in words: <i>{s['words']}</i></div>
<div style="margin-top:14px"><b>बैंक विवरण / Bank details:</b> {v['bank']} &nbsp; A/c No: {v['account']} &nbsp; IFSC: {v['ifsc']}</div>
<div style="margin-top:30px" class="r">कृते / For {escape(v['name'])}<br><br>अधिकृत हस्ताक्षरकर्ता / Authorised Signatory</div>
</div></body></html>"""


def eu_vat(s: dict) -> str:
    v, t = s["vendor"], totals(s)
    rows = "".join(f"<tr><td>{escape(l[0])}</td><td>{escape(l[1])}</td><td class='r'>{l[2]}</td><td class='r'>€ {fmt(l[3], 'eu')}</td>"
                   f"<td class='r'>€ {fmt(l[4], 'eu')}</td></tr>" for l in t["lines"])
    po = f"<div>Uw referentie / Your reference: {s['po']}</div>" if s.get("po") else ""
    return f"""<html><head><style>{BASE_CSS}
body {{ font-family: 'Helvetica Neue', Helvetica, sans-serif; font-size: 10pt }}
.items th {{ text-align:left; border-bottom:2px solid #0e7490; padding:5px 3px; color:#0e7490; font-size:9pt }}
.items td {{ padding:5px 3px; border-bottom:1px solid #e5e7eb }}
</style></head><body><div class="page">
<div style="color:#0e7490;font-size:19pt" class="b">Rijnmond Industrietechniek</div><div class="muted">{' · '.join(v['addr'])} · BTW-nr {v['tax_id']} · KvK 24316512</div>
<table style="margin-top:28px"><tr><td style="vertical-align:top">{'<br>'.join(s['bill_to'])}</td>
<td style="vertical-align:top" class="r"><div style="font-size:15pt" class="b">FACTUUR / INVOICE</div>
<div>Factuurnummer / Invoice no.: <b>{s['number']}</b></div><div>Factuurdatum / Date: {s['date']}</div>
<div>Vervaldatum / Due: {s['due']}</div>{po}</td></tr></table>
<table class="items" style="margin-top:26px"><tr><th>Art.nr.</th><th>Omschrijving / Description</th><th class="r">Aantal</th><th class="r">Prijs incl. BTW</th><th class="r">Bedrag</th></tr>
{rows}</table>
<div style="width:52%;margin:16px 0 0 auto">
<div class="b" style="font-size:12pt">Totaal incl. BTW <span style="float:right">€ {fmt(t['total'], 'eu')}</span></div>
<div class="muted">waarvan BTW {s['tax_rate']}% / incl. VAT {s['tax_rate']}% <span style="float:right">€ {fmt(t['tax'], 'eu')}</span></div></div>
<div style="margin-top:30px">Alle prijzen zijn inclusief {s['tax_rate']}% BTW. / All prices include {s['tax_rate']}% VAT.</div>
<div style="margin-top:14px">Betaling binnen 30 dagen op IBAN <b>{v['iban']}</b> (ABN AMRO) t.n.v. {escape(v['name'])}.</div>
</div></body></html>"""


def statement(s: dict) -> str:
    v = s["vendor"]
    rows = "".join(f"<tr><td>{a}</td><td>{b}</td><td class='r'>{c}</td><td class='r'>{d}</td><td>{e}</td></tr>" for a, b, c, d, e in s["rows"])
    return f"""<html><head><style>{BASE_CSS} td, th {{ padding: 5px 4px; border-bottom: 1px solid #e5e7eb; text-align:left }}</style></head>
<body><div class="page"><div class="b" style="font-size:16pt">{v.get('brand', escape(v['name']))}</div>
<div style="font-size:15pt;margin-top:20px" class="b">STATEMENT OF ACCOUNT</div>
<div>Statement date: {s['date']} · Customer: Clearline Manufacturing Inc.</div>
<table style="margin-top:20px"><tr><th>Reference</th><th>Type</th><th class="r">Amount</th><th class="r">Balance</th><th>Status</th></tr>{rows}</table>
<div class="b" style="margin-top:16px">Balance outstanding: USD 10,000.00</div>
<div style="margin-top:8px">This statement is for information only. Please do not pay from this statement.</div></div></body></html>"""


LAYOUTS = {"acme_classic": acme_classic, "acme_v2": acme_v2, "northwind": northwind, "globex": globex, "initech": initech,
           "gst_bilingual": gst_bilingual, "eu_vat": eu_vat, "statement": statement}
