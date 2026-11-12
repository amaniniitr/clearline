"""Pre-process: detect the text layer, render pages, build the line map (text lines with page, bbox, word boxes).

Line map entry: {"i": 0, "page": 1, "text": "...", "bbox": [x0, top, x1, bottom], "words": [{"t": "..", "bbox": [...]}]}
Coordinates are in PDF points, top-left origin, plus the page size so the UI can scale overlays.
"""
from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageOps


@lru_cache(maxsize=1)
def _ocr():
    from rapidocr import RapidOCR

    return RapidOCR()


def ocr_image(img: Image.Image) -> list[tuple[str, list[float], float]]:
    """[(text, [x0, top, x1, bottom], score)] in image pixels."""
    import numpy as np

    res = _ocr()(np.array(img.convert("RGB")))
    out = []
    if res is None or res.boxes is None:
        return out
    for box, txt, score in zip(res.boxes, res.txts, res.scores):
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        out.append((str(txt), [float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))], float(score)))
    return out


def _clean(t: str) -> str:
    """Drop control characters (some PDFs emit NUL inside Indic text) — Postgres JSON can't store \\u0000."""
    return "".join(ch for ch in t if ch >= " " or ch in "\t").strip()


def _group_lines(words: list[dict], page_no: int, start: int) -> list[dict]:
    words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines: list[list[dict]] = []
    for w in words:
        # One visual row = one line. Word boxes keep the finer positions for highlighting.
        if lines and abs(lines[-1][0]["top"] - w["top"]) < 3:
            lines[-1].append(w)
        else:
            lines.append([w])
    lines = [sorted(ws, key=lambda w: w["x0"]) for ws in lines]
    # Split a row into segments at wide gaps (two-column headers). `row` keeps the visual row for table parsing.
    segs: list[tuple[int, list[dict]]] = []
    for r, ws in enumerate(lines):
        cur = [ws[0]]
        for w in ws[1:]:
            if w["x0"] - cur[-1]["x1"] > 60:
                segs.append((r, cur))
                cur = [w]
            else:
                cur.append(w)
        segs.append((r, cur))
    out = []
    for k, (r, ws) in enumerate(segs):
        out.append({
            "i": start + k, "page": page_no, "row": r,
            "text": " ".join(w["text"] for w in ws),
            "bbox": [min(w["x0"] for w in ws), min(w["top"] for w in ws), max(w["x1"] for w in ws), max(w["bottom"] for w in ws)],
            "words": [{"t": w["text"], "bbox": [w["x0"], w["top"], w["x1"], w["bottom"]]} for w in ws],
        })
    return out


def build_pdf_line_map(path: Path) -> dict:
    lines: list[dict] = []
    pages = []
    text_layer = False
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            pages.append({"page": pno, "width": float(page.width), "height": float(page.height)})
            words = page.extract_words(keep_blank_chars=False, x_tolerance=1.5, y_tolerance=2)
            for w in words:
                w["text"] = _clean(w["text"])
            words = [w for w in words if w["text"]]
            if words:
                text_layer = True
                lines.extend(_group_lines(words, pno, len(lines)))
    if not text_layer:
        # Scanned PDF: OCR each rendered page (scale 3 ≈ 216 dpi; scale 2 misses small digits on scans).
        doc = pdfium.PdfDocument(str(path))
        for pno in range(len(doc)):
            img = doc[pno].render(scale=3).to_pil()
            lines.extend(_ocr_lines(img, pno + 1, len(lines), scale=3))
    return {"pages": pages, "lines": lines, "text_layer": text_layer, "source": "pdfplumber" if text_layer else "rapidocr"}


def _ocr_lines(img: Image.Image, page_no: int, start: int, scale: float = 1.0) -> list[dict]:
    out = []
    res = sorted(ocr_image(img), key=lambda r: (r[1][1], r[1][0]))
    row, last_top = -1, None
    for k, (txt, bb, score) in enumerate(res):
        bb = [v / scale for v in bb]
        h = bb[3] - bb[1]
        if last_top is None or abs(bb[1] - last_top) > max(4, h * 0.6):
            row += 1
            last_top = bb[1]
        out.append({"i": start + k, "page": page_no, "row": row, "text": _clean(txt), "bbox": bb, "ocr_score": round(score, 3),
                    "words": [{"t": w, "bbox": bb} for w in _clean(txt).split()] or [{"t": _clean(txt), "bbox": bb}]})
    return out


def build_image_line_map(path: Path) -> dict:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img).convert("RGB")
    up = 2.0 if max(img.size) < 2400 else 1.0  # small phone photos: upscale so OCR sees small digits
    big = img.resize((int(img.width * up), int(img.height * up)), Image.LANCZOS) if up != 1 else img
    lines = _ocr_lines(big, 1, 0, scale=up)
    return {"pages": [{"page": 1, "width": float(img.width), "height": float(img.height)}], "lines": lines,
            "text_layer": False, "source": "rapidocr"}


def render_page_png(path: Path, page: int, scale: float = 2.0) -> bytes:
    doc = pdfium.PdfDocument(str(path))
    img = doc[page - 1].render(scale=scale).to_pil()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@lru_cache(maxsize=8)
def page_ocr(path_str: str, page: int, scale: float = 3.0) -> tuple:
    """Full-page OCR of the rendered pixels (cached per document page): ((text, bbox_in_pdf_points), ...)."""
    img = _rendered(path_str, page, scale)
    return tuple((t, [v / scale for v in bb]) for t, bb, _ in ocr_image(img))


def ocr_near(path: Path, page: int, bbox: list[float], pad: float = 3.0) -> str:
    """What the *visible pixels* say at a text-layer location: OCR boxes whose centre falls in the (padded) bbox row."""
    x0, top, x1, bottom = bbox
    hits = []
    for t, (a, b, c, d) in page_ocr(str(path), page):
        cy = (b + d) / 2
        if top - pad <= cy <= bottom + pad and c >= x0 - pad and a <= x1 + pad:
            hits.append((a, t))
    return " ".join(t for _, t in sorted(hits))


@lru_cache(maxsize=8)
def _rendered(path_str: str, page: int, scale: float):
    doc = pdfium.PdfDocument(path_str)
    return doc[page - 1].render(scale=scale).to_pil()


def ocr_region(path: Path, page: int, bbox: list[float], pad: float = 4.0, scale: float = 3.0) -> str:
    """Render check: OCR the *visible pixels* of a region and return the text. Independent of the text layer."""
    img = _rendered(str(path), page, scale)
    x0, top, x1, bottom = bbox
    crop = img.crop((max(0, (x0 - pad) * scale), max(0, (top - pad) * scale), (x1 + pad) * scale, (bottom + pad) * scale))
    return " ".join(t for t, _, _ in ocr_image(crop))


def line_map_text(line_map: dict) -> str:
    return "\n".join(l["text"] for l in line_map["lines"])
