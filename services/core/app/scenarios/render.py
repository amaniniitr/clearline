"""Build every scenario file (PDF / scanned PDF / phone photo / .eml) plus ground truth.

    uv run python -m app.scenarios.render
"""
from __future__ import annotations

import base64
import io
import json
from email.message import EmailMessage
from pathlib import Path

from ..config import settings
from . import printer
from .catalog import BILL_TO, SCENARIOS, series
from .templates import LAYOUTS, totals


def build(s: dict, out_dir: Path) -> Path:
    s = {**s, "bill_to": s.get("bill_to", BILL_TO)}
    if s.get("tamper"):
        img = printer.raster_text(s["tamper"]["visible"], 40)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        s["tamper"] = {**s["tamper"], "img": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}
    html = LAYOUTS[s["layout"]](s)
    pdf = printer.html_to_pdf(html, out_dir / f"{s['id']}.pdf")
    cap = s.get("capture") or {}
    if cap.get("kind") == "scan":
        clean = out_dir / f"{s['id']}_original.pdf"
        pdf.replace(clean)
        printer.scan(clean, out_dir / f"{s['id']}.pdf")
        clean.unlink()
    elif cap.get("kind") == "photo":
        printer.photo(pdf, out_dir / f"{s['id']}.jpg", smudge=cap.get("smudge"))
        pdf.unlink()
        return out_dir / f"{s['id']}.jpg"
    if s.get("email"):
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = s["email"]["from"], "ap@clearline-mfg.com", s["email"]["subject"]
        if s["email"].get("reply_to"):
            m["Reply-To"] = s["email"]["reply_to"]
        m.set_content(s["email"]["body"])
        m.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename=f"{s['number']}.pdf")
        eml = out_dir / f"{s['id']}.eml"
        eml.write_bytes(bytes(m))
        return eml
    return pdf


def render_all(out_dir: Path | None = None, only: set[str] | None = None) -> list[dict]:
    out_dir = out_dir or settings().scenarios_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    truth, made, ser = {}, [], []
    for s in SCENARIOS + series():
        if only and s["id"] not in only:
            continue
        path = build(s, out_dir)
        t = totals(s) if s["lines"] else {}
        rec = {**s["expected"], "file": path.name, "title": s["title"], "layout": s["layout"],
               "computed": {k: str(v) for k, v in t.items() if k != "lines"}}
        if s["tag"] == "Learning series":
            ser.append({"id": s["id"], "title": s["title"], "file": path.name, "story": s["story"], "expected": s["expected"]["outcomes"]})
        else:
            truth[s["id"]] = rec
        made.append({"id": s["id"], "file": path.name})
        print(f"  {s['id']:4} {s['layout']:14} {path.name}")
    if not only:
        (out_dir / "ground_truth.json").write_text(json.dumps(truth, indent=2))
        (out_dir / "series.json").write_text(json.dumps(ser, indent=2))
    return made


if __name__ == "__main__":
    import sys

    render_all(only=set(sys.argv[1].split(",")) if len(sys.argv) > 1 else None)
