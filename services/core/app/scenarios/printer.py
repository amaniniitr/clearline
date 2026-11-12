"""HTML → PDF with a headless Chromium, plus scan / photo degradation (PIL).

Uses an isolated --user-data-dir so it never touches the user's browser profile. The browser is killed once the PDF
is written (some Chromium builds don't exit after --print-to-pdf)."""
from __future__ import annotations

import io
import math
import os
import random
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFilter, ImageOps

CANDIDATES = [
    os.environ.get("CHROME_BIN", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    shutil.which("chromium") or "", shutil.which("google-chrome") or "",
]


def browser() -> str:
    for c in CANDIDATES:
        if c and Path(c).exists():
            return c
    raise RuntimeError("No Chromium-based browser found; set CHROME_BIN")


def html_to_pdf(html: str, out: Path, timeout: float = 45) -> Path:
    """Print HTML to PDF with Playwright's bundled Chromium (isolated; never touches a user browser profile)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    page = _page()
    page.set_content(html, wait_until="load", timeout=timeout * 1000)
    page.pdf(path=str(out), format="Letter", print_background=True, margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
    return out


_pw = {"page": None}


def _page():
    if _pw["page"] is None:
        from playwright.sync_api import sync_playwright

        _pw["ctx"] = sync_playwright().start()
        _pw["browser"] = _pw["ctx"].chromium.launch()
        _pw["page"] = _pw["browser"].new_page()
    return _pw["page"]


def html_to_pdf_cli(html: str, out: Path, timeout: float = 45) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "doc.html"
        src.write_text(html, encoding="utf-8")
        tmp_pdf = Path(td) / "out.pdf"
        proc = subprocess.Popen([browser(), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                                 f"--user-data-dir={td}/profile", "--no-pdf-header-footer", f"--print-to-pdf={tmp_pdf}",
                                 src.as_uri()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0, last = time.time(), -1
        try:
            while time.time() - t0 < timeout:
                if tmp_pdf.exists():
                    size = tmp_pdf.stat().st_size
                    if size > 0 and size == last:
                        break
                    last = size
                if proc.poll() is not None and tmp_pdf.exists():
                    break
                time.sleep(0.3)
            else:
                raise TimeoutError("browser did not produce a PDF")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        shutil.copy(tmp_pdf, out)
    return out


def render_page(pdf: Path, scale: float = 2.0) -> Image.Image:
    return pdfium.PdfDocument(str(pdf))[0].render(scale=scale).to_pil().convert("RGB")


def scan(pdf: Path, out: Path, seed: int = 1, grade: str = "moderate") -> Path:
    """Office-scanner look: grayscale, slight skew, speckle, soft focus, JPEG. Output is an image-only PDF (no text layer)."""
    rnd = random.Random(seed)
    img = ImageOps.grayscale(render_page(pdf, 2.0))
    img = img.rotate(rnd.uniform(-1.2, 1.2), resample=Image.BICUBIC, expand=False, fillcolor=255)
    px = img.load()
    w, h = img.size
    for _ in range(int(w * h * (0.0015 if grade == "mild" else 0.004))):
        x, y = rnd.randrange(w), rnd.randrange(h)
        px[x, y] = rnd.randint(0, 120)
    img = img.filter(ImageFilter.GaussianBlur(0.6 if grade == "mild" else 0.9))
    img = ImageOps.autocontrast(img, cutoff=1)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=60)
    Image.open(buf).convert("RGB").save(out, "PDF", resolution=144)
    return out


def photo(pdf: Path, out: Path, seed: int = 4, smudge: tuple[float, float, float] | None = None) -> Path:
    """Phone photo of a printed page on a desk: perspective, rotation, uneven light, shadow, blur, JPEG.
    smudge = (x, y, r) in page fractions: a soft stain over one spot (e.g. one digit of the total)."""
    rnd = random.Random(seed)
    page = render_page(pdf, 1.6)
    if smudge:
        d = ImageDraw.Draw(page, "RGBA")
        x, y, r = smudge[0] * page.width, smudge[1] * page.height, smudge[2] * page.width
        for k in range(14):
            rr = r * (1 - k / 16)
            d.ellipse((x - rr, y - rr * 0.8, x + rr, y + rr * 0.8), fill=(150, 110, 60, 18))
    W, H = int(page.width * 1.25), int(page.height * 1.2)
    desk = Image.new("RGB", (W, H), (96, 78, 60))
    dd = ImageDraw.Draw(desk)
    for k in range(0, H, 9):
        dd.line([(0, k), (W, k + rnd.randint(-6, 6))], fill=(88 + rnd.randint(-6, 6), 70, 54), width=4)
    ox, oy = (W - page.width) // 2, (H - page.height) // 2
    desk.paste(page, (ox, oy))
    # perspective: map the output quad back onto the source
    j = lambda a: a + rnd.uniform(-0.035, 0.035) * W  # noqa: E731
    quad = (j(ox * 0.6), j(oy * 0.5), j(ox * 0.8), j(H - oy * 0.4), j(W - ox * 0.7), j(H - oy * 0.6), j(W - ox * 0.5), j(oy * 0.7))
    img = desk.transform((W, H), Image.QUAD, quad, resample=Image.BICUBIC)
    img = img.rotate(rnd.uniform(-3, 3), resample=Image.BICUBIC, fillcolor=(80, 64, 50))
    # light falloff + a hand shadow
    shade = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(shade)
    for k in range(40):
        sd.ellipse((-W * 0.2 + k * 6, H * 0.55 + k * 4, W * 0.45 - k * 3, H * 1.4), fill=int(4 + k * 2.2))
    shade = shade.filter(ImageFilter.GaussianBlur(40))
    dark = Image.new("RGB", (W, H), (20, 15, 10))
    img = Image.composite(dark, img, shade.point(lambda v: int(v * 0.55)))
    vign = Image.radial_gradient("L").resize((W, H)).point(lambda v: int(v * 0.35))
    img = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, vign)
    img = img.filter(ImageFilter.GaussianBlur(1.0))
    img = img.resize((int(W * 0.8), int(H * 0.8)), Image.LANCZOS)
    img.save(out, "JPEG", quality=58)
    return out


def raster_text(text: str, size_px: int = 26, color=(17, 17, 17)) -> Image.Image:
    """Text drawn as pixels (no text layer) — used to build the tampered-document scenario."""
    from PIL import ImageFont

    font = None
    for f in ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf"):
        if Path(f).exists():
            font = ImageFont.truetype(f, size_px)
            break
    font = font or ImageFont.load_default()
    w = int(font.getlength(text)) + 4
    img = Image.new("RGBA", (w, int(size_px * 1.3)), (255, 255, 255, 0))
    ImageDraw.Draw(img).text((2, 0), text, font=font, fill=color + (255,))
    return img
