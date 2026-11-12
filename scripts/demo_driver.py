#!/usr/bin/env python3
"""Clearline 5-Minute Demo Automation Driver.

Drives the live web console at http://localhost:3000 through the complete
evaluation flow while synchronizing with an interactive terminal teleprompter.

Usage:
    ./scripts/demo_driver.sh
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright, Page, Locator

WEB = "http://localhost:3000"
API = "http://127.0.0.1:8000"

# Terminal formatting
BOLD = "\033[1m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RED = "\033[91m"
MAGENTA = "\033[95m"
DIM = "\033[2m"
RESET = "\033[0m"


def print_banner(step_title: str, elapsed_start: float, total_sec: int, script_text: str):
    elapsed = int(time.time() - elapsed_start)
    mins = elapsed // 60
    secs = elapsed % 60
    tot_mins = total_sec // 60
    tot_secs = total_sec % 60

    print("\n" + "=" * 76)
    print(f"{BOLD}{CYAN}⏱️  [{mins:02d}:{secs:02d} / {tot_mins:02d}:{tot_secs:02d}]  {step_title}{RESET}")
    print("=" * 76)
    print(f"{BOLD}{YELLOW}🎙️  WHAT TO SAY (READ ALOUD INTO YOUR MIC):{RESET}")
    print(f"{BOLD}{GREEN}\"{script_text}\"{RESET}")
    print("-" * 76)
    sys.stdout.flush()


def reset_api():
    try:
        req = urllib.request.Request(f"{API}/api/admin/reset", method="POST")
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"Warning: Could not reset API: {e}")
        return None


def inject_custom_cursor(page: Page):
    """Injects a smooth glowing visual cursor so viewers clearly follow the mouse."""
    js = """
    (() => {
        if (document.getElementById('demo-cursor-dot')) return;
        const dot = document.createElement('div');
        dot.id = 'demo-cursor-dot';
        dot.style.position = 'fixed';
        dot.style.top = '0px';
        dot.style.left = '0px';
        dot.style.width = '20px';
        dot.style.height = '20px';
        dot.style.borderRadius = '50%';
        dot.style.backgroundColor = 'rgba(239, 68, 68, 0.85)';
        dot.style.border = '2px solid white';
        dot.style.boxShadow = '0 0 12px rgba(239, 68, 68, 0.9)';
        dot.style.pointerEvents = 'none';
        dot.style.zIndex = '999999';
        dot.style.transition = 'transform 0.08s ease-out, background-color 0.2s';
        dot.style.transform = 'translate(-100px, -100px)';
        document.body.appendChild(dot);

        window.addEventListener('mousemove', (e) => {
            dot.style.transform = `translate(${e.clientX - 10}px, ${e.clientY - 10}px)`;
        });
        window.addEventListener('mousedown', () => {
            dot.style.transform += ' scale(0.7)';
            dot.style.backgroundColor = 'rgba(16, 185, 129, 0.9)';
        });
        window.addEventListener('mouseup', () => {
            dot.style.backgroundColor = 'rgba(239, 68, 68, 0.85)';
        });
    })();
    """
    try:
        page.evaluate(js)
    except Exception:
        pass


def smooth_scroll(page: Page, y_delta: int, duration_sec: float = 2.0):
    steps = int(duration_sec * 30)
    step_delta = y_delta / steps
    for _ in range(steps):
        try:
            page.evaluate(f"window.scrollBy(0, {step_delta});")
        except Exception:
            break
        time.sleep(duration_sec / steps)


def spotlight(page: Page, selector: str, duration: float = 1.5):
    try:
        page.evaluate(f"""
            const el = document.querySelector('{selector}');
            if (el) {{
                el.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                const orig = el.style.boxShadow;
                el.style.boxShadow = '0 0 0 4px #38bdf8';
                setTimeout(() => {{ el.style.boxShadow = orig; }}, {int(duration * 1000)});
            }}
        """)
    except Exception:
        pass


def smooth_click(page: Page, locator: Locator):
    try:
        box = locator.bounding_box()
        if box:
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=15)
        locator.click()
    except Exception:
        locator.click()


def countdown(seconds: int, message: str = "Next step in"):
    for s in range(seconds, 0, -1):
        sys.stdout.write(f"\r{DIM}⏳ {message}: {s}s...{RESET}   ")
        sys.stdout.flush()
        time.sleep(1)
    sys.stdout.write("\r" + " " * 40 + "\r")
    sys.stdout.flush()


def run_demo():
    print(f"\n{BOLD}{MAGENTA}========================================================================{RESET}")
    print(f"{BOLD}{MAGENTA}   Zamp Take-Home Case Study — 5-Minute Clearline Demo Driver          {RESET}")
    print(f"{BOLD}{MAGENTA}========================================================================{RESET}")
    print(f"{CYAN}Resetting database & seeding clean state...{RESET}")
    reset_api()

    print(f"\n{YELLOW}Instructions:{RESET}")
    print("1. A browser window will open at 1550x950.")
    print("2. Position your Loom / screen recorder to capture that browser window.")
    print("3. Start your recording during the 5-second countdown.")
    print("4. Read the text displayed in {BOLD}{GREEN}GREEN{RESET} out loud into your mic.")
    print("5. Total duration is exactly 4 minutes 40 seconds (safely under the 5-minute cap).")

    countdown(5, "Launching browser in")

    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path.home() / "Library/Caches/ms-playwright")

    total_target = 280  # 4m 40s
    start_time = time.time()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--window-size=1550,960",
                "--window-position=50,50",
                "--disable-blink-features=AutomationControlled",
            ]
        )
        context = browser.new_context(
            viewport={"width": 1500, "height": 900},
            device_scale_factor=2,
            record_video_dir="var/videos"
        )
        page = context.new_page()

        # -------------------------------------------------------------
        # SCENE 1: Introduction & How It Works (0:00 - 0:25)
        # -------------------------------------------------------------
        print_banner(
            "SCENE 1: Introduction & The Self-Learning Hook",
            start_time,
            total_target,
            "Most AP automation breaks the moment an invoice shifts. Clearline is an intelligent "
            "decision engine that learns on its own: repeat vendors automatically train zero-cost "
            "templates, while every invoice strictly isolates two questions: do we owe this, "
            "and is it safe to pay where they ask?"
        )
        page.goto(f"{WEB}/how")
        inject_custom_cursor(page)
        time.sleep(3)
        smooth_scroll(page, 450, 4.0)
        time.sleep(3)
        smooth_scroll(page, -450, 3.0)
        countdown(8, "Transition to Happy Path")

        # -------------------------------------------------------------
        # SCENE 2: Happy Path H1 (0:25 - 1:15)
        # -------------------------------------------------------------
        print_banner(
            "SCENE 2: Happy Path H1 (Clean Invoice)",
            start_time,
            total_target,
            "Here is H1, our happy path. Notice the live stage rail: Clearline reads the invoice "
            "twice, independently, using different models and OCR techniques. Green tags mean Verified: "
            "extracted from the document and confirmed another way. The 3-way match against the purchase "
            "order and goods receipt succeeds, and the bill is posted automatically with zero manual touch."
        )
        page.goto(f"{WEB}/")
        inject_custom_cursor(page)
        time.sleep(2)
        h1_btn = page.locator("button:has-text('Clean invoice, fully received')")
        smooth_click(page, h1_btn)

        page.wait_for_url("**/runs/**", timeout=20000)
        inject_custom_cursor(page)

        # Wait for streaming to show Decision card
        page.wait_for_selector("text=Decision", timeout=40000)
        time.sleep(4)
        smooth_scroll(page, 300, 3.0)
        time.sleep(5)
        smooth_scroll(page, -300, 2.0)
        countdown(12, "Transition to Fraud Edge Case")

        # -------------------------------------------------------------
        # SCENE 3: Edge Case E1 — Fraud & RBAC (1:15 - 2:30)
        # -------------------------------------------------------------
        print_banner(
            "SCENE 3: Edge Case E1 (Payment Fraud & Segregation of Duties)",
            start_time,
            total_target,
            "Now for an edge case: payment fraud in E1. This invoice matches PO-4410 perfectly. "
            "A naive 3-way match would pay it immediately. But Clearline screens the context: "
            "the email comes from a lookalike domain, acme-supp1y.com with a 1, reply-to on Gmail, "
            "and an unverified bank account. Clearline answers the two questions separately: Owed is Yes, "
            "but Safe is No. It isolates exactly one specific question for the Controller. If an AP clerk "
            "attempts to approve, the system refuses due to strict segregation of duties."
        )
        page.goto(f"{WEB}/")
        inject_custom_cursor(page)
        time.sleep(2)
        e1_btn = page.locator("button:has-text('Valid invoice, unsafe payment')")
        smooth_click(page, e1_btn)

        page.wait_for_url("**/runs/**", timeout=20000)
        inject_custom_cursor(page)
        page.wait_for_selector("text=The one question", timeout=45000)
        time.sleep(3)

        # Smoothly scroll to Screen & Judge stages
        smooth_scroll(page, 350, 3.0)
        time.sleep(4)

        # Highlight "The one question"
        spotlight(page, "div.border-l-4.border-slate-900", duration=2.5)
        time.sleep(4)

        # Demonstrate RBAC Segregation of Duties refusal
        # Click "Call-back: vendor confirms" while role is AP clerk
        try:
            cb_btn = page.locator("button:has-text('Call-back: vendor confirms')")
            if cb_btn.is_visible():
                smooth_click(page, cb_btn)
                time.sleep(4)  # Let the red rejection notice stay visible
        except Exception as e:
            print(f"(RBAC click note: {e})")

        countdown(10, "Transition to Partial Delivery E2")

        # -------------------------------------------------------------
        # SCENE 4: Edge Case E2 — Partial Delivery & Async Auto-Release (2:30 - 3:35)
        # -------------------------------------------------------------
        print_banner(
            "SCENE 4: Edge Case E2 (Partial Delivery & Asynchronous Auto-Release)",
            start_time,
            total_target,
            "In E2, Northwind billed all 100 units, but receiving has only logged 60. "
            "Instead of blocking the invoice or paying blindly, Clearline calculates a partial 3-way match: "
            "$6,000 is payable today, and $4,000 is held. As AP clerk, I authorize the short-pay of $6,000. "
            "Later, when the warehouse logs the remaining 40 items via GRN-883, Clearline automatically "
            "triggers a child run, reconciles the balance, and posts the remaining $4,000 touchlessly."
        )
        page.goto(f"{WEB}/")
        inject_custom_cursor(page)
        time.sleep(2)
        e2_btn = page.locator("button:has-text('Partial delivery')")
        smooth_click(page, e2_btn)

        page.wait_for_url("**/runs/**", timeout=20000)
        inject_custom_cursor(page)
        page.wait_for_selector("text=The one question", timeout=45000)
        time.sleep(3)

        # Scroll to PO/Receipts shortfall and Decision
        smooth_scroll(page, 300, 2.5)
        time.sleep(4)

        # As AP clerk, click Short-pay
        try:
            sp_btn = page.locator("button:has-text('Short-pay')")
            if sp_btn.is_visible():
                smooth_click(page, sp_btn)
                time.sleep(3)
        except Exception as e:
            print(f"(Short-pay click note: {e})")

        # Navigate to Queue
        page.goto(f"{WEB}/queue")
        inject_custom_cursor(page)
        time.sleep(3)

        # Click Simulate: receiving posts GRN-883
        try:
            grn_btn = page.locator("button:has-text('Simulate: receiving posts GRN-883')")
            if grn_btn.is_visible():
                smooth_click(page, grn_btn)
                time.sleep(4)
        except Exception as e:
            print(f"(GRN button note: {e})")

        countdown(8, "Transition to Continuous Learning")

        # -------------------------------------------------------------
        # SCENE 5: Continuous Learning & Template Drift (3:35 - 4:15)
        # -------------------------------------------------------------
        print_banner(
            "SCENE 5: Continuous Learning & Template Drift",
            start_time,
            total_target,
            "Next is continuous learning. For repeat vendors, Clearline learns visual layout templates. "
            "Across a month of 14 invoices, extraction cost falls from $0.006 per invoice to $0.0015, "
            "down to zero dollars on pure template extraction, while still verifying arithmetic and bank pixels. "
            "And when Acme changes its template in L2, Clearline instantly detects the mismatch and falls back "
            "safely to a full model read."
        )
        page.goto(f"{WEB}/learning")
        inject_custom_cursor(page)
        time.sleep(4)
        smooth_scroll(page, 350, 3.0)
        time.sleep(3)

        # Back to Home for the month simulation
        page.goto(f"{WEB}/")
        inject_custom_cursor(page)
        time.sleep(2)
        smooth_scroll(page, 600, 2.0)
        time.sleep(2)

        # Click Simulate the month
        try:
            sim_btn = page.locator("button:has-text('Simulate the month (14 invoices)')")
            if sim_btn.is_visible():
                smooth_click(page, sim_btn)
                time.sleep(10)  # Watch the table fill with $0.0000 costs
        except Exception as e:
            print(f"(Simulate month note: {e})")

        # Click L2 template change
        try:
            l2_btn = page.locator("button:has-text('L2 · Acme changes template')")
            if l2_btn.is_visible():
                smooth_click(page, l2_btn)
                time.sleep(5)
        except Exception as e:
            print(f"(L2 note: {e})")

        countdown(6, "Transition to Dashboard & Conclusion")

        # -------------------------------------------------------------
        # SCENE 6: Dashboard & Conclusion (4:15 - 4:40)
        # -------------------------------------------------------------
        print_banner(
            "SCENE 6: Dashboard & Executive Metrics",
            start_time,
            total_target,
            "Finally, the executive dashboard tracks our touchless straight-through processing rate, "
            "the exact fraud dollars stopped, and our rigorous evaluation: all 11 core scenarios "
            "passing with zero false positives. Thank you."
        )
        page.goto(f"{WEB}/dashboard")
        inject_custom_cursor(page)
        time.sleep(3)
        smooth_scroll(page, 300, 3.0)
        time.sleep(6)

        total_elapsed = int(time.time() - start_time)
        print("\n" + "=" * 76)
        print(f"{BOLD}{GREEN}🎉 DEMO COMPLETE! Total Run Time: {total_elapsed // 60}m {total_elapsed % 60}s{RESET}")
        print(f"{CYAN}You can now stop your Loom or screen recording.{RESET}")
        print("=" * 76)

        # Clean close
        time.sleep(3)
        context.close()
        browser.close()


if __name__ == "__main__":
    run_demo()
