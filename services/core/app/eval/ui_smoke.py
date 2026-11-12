"""UI smoke test: drive the web console in a real browser, collect console errors, take screenshots.

    uv run python -m app.eval.ui_smoke
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

from ..config import ROOT

WEB, API = "http://localhost:3000", "http://127.0.0.1:8000"
OUT = ROOT / "var" / "shots"


def api(method: str, path: str):
    req = urllib.request.Request(API + path, method=method)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1600, "height": 1000})
        page.on("console", lambda m: m.type == "error" and errors.append(f"{page.url}: {m.text}"))
        page.on("pageerror", lambda e: errors.append(f"{page.url}: {e}"))
        page.goto(WEB + "/")
        page.wait_for_selector("text=Edge cases", timeout=20000)
        page.screenshot(path=str(OUT / "01-home.png"), full_page=True)
        # Run E1 from the UI, watch it stream, screenshot the case view.
        page.click("text=Valid invoice, unsafe payment")
        page.wait_for_url("**/runs/**", timeout=20000)
        page.wait_for_selector("text=The one question", timeout=90000)
        time.sleep(1.5)
        page.screenshot(path=str(OUT / "02-case-E1.png"), full_page=True)
        for path, name in [("/queue", "03-queue"), ("/dashboard", "04-dashboard"), ("/learning", "05-learning"),
                           ("/vendors", "06-vendors"), ("/policy", "07-policy"), ("/how", "08-how"), ("/history", "09-history")]:
            page.goto(WEB + path)
            time.sleep(2.5)
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
        # An auto-approved run (case view after posting)
        runs = [r for r in api("GET", "/api/runs") if r["scenario"] == "H1"]
        if runs:
            page.goto(WEB + f"/runs/{runs[0]['id']}")
            page.wait_for_selector("text=Decision", timeout=20000)
            time.sleep(2)
            page.screenshot(path=str(OUT / "10-case-H1.png"), full_page=True)
        b.close()
    print("screenshots:", sorted(x.name for x in OUT.glob("*.png")))
    print("console errors:", len(errors))
    for e in errors[:20]:
        print("  ", e[:300])


if __name__ == "__main__":
    main()
