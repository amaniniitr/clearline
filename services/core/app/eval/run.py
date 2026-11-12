"""Eval (§12): run every scenario through the real workflow (via the API) and score against ground truth.

    uv run python -m app.eval.run [--reset] [--only H1,E2] [--pace 0]

Writes clearline/eval/latest.json (shown on the dashboard)."""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx

from ..config import ROOT, settings

API = "http://127.0.0.1:8000"
TERMINAL = {"completed", "awaiting_human", "failed"}


def wait(c: httpx.Client, run_id: str, timeout: float = 180) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = c.get(f"{API}/api/runs/{run_id}").json()
        if d["run"]["status"] in TERMINAL and (d["run"]["status"] != "completed" or any(e["stage"] in ("learn", "commit") and e["type"] == "stage.finished" or e["type"] == "run.finished" for e in d["events"])):
            return d
        time.sleep(0.5)
    raise TimeoutError(run_id)


def score(sid: str, truth: dict, d: dict) -> dict:
    p = d["proposal"] or {}
    fields = {f: (d["facts"]["extraction"]["fields"].get(f) or {}).get("value") if d["facts"] else None for f in truth.get("fields", {})}
    checks = {"outcome": p.get("outcome") in truth["outcomes"]}
    for k in ("owner", "safe_to_pay", "owed", "sub_status"):
        if k in truth:
            got = p.get("owner_role") if k == "owner" else p.get(k)
            checks[k] = got == truth[k]
    for k in ("amount_payable", "amount_held"):
        if k in truth:
            checks[k] = Decimal(str(p.get(k) or 0)) == Decimal(truth[k])
    field_ok = {}
    for f, exp in truth.get("fields", {}).items():
        got = fields.get(f)
        if f in ("total", "subtotal"):
            from ..domain.money import parse_amount

            field_ok[f] = got is not None and parse_amount(got) == Decimal(exp)
        else:
            field_ok[f] = (got or "").strip().upper() == exp.upper()
    verified_wrong = [f for f, ok in field_ok.items() if not ok and
                      ((d["facts"] or {}).get("extraction", {}).get("fields", {}).get(f) or {}).get("evidence") == "verified"]
    return {"id": sid, "outcome": p.get("outcome"), "expected": truth["outcomes"], "pass": all(checks.values()) and all(field_ok.values()),
            "checks": checks, "fields": field_ok, "verified_but_wrong": verified_wrong, "cost_usd": float(d["run"]["cost_usd"] or 0),
            "tier": d["run"].get("read_tier"), "question": p.get("question"),
            "amount_payable": p.get("amount_payable"), "amount_held": p.get("amount_held"),
            "ms": int((datetime.fromisoformat(d["events"][-1]["ts"]) - datetime.fromisoformat(d["events"][0]["ts"])).total_seconds() * 1000)}


def run_series(c: httpx.Client) -> list[dict]:
    """Repeat-vendor eval (§16.4): cost, tier and touchless status per invoice, in date order."""
    series = json.loads((settings().scenarios_dir / "series.json").read_text())
    rows = []
    print("\nLearning series (Acme, blanket PO-4499):")
    for s in series:
        r = c.post(f"{API}/api/scenarios/{s['id']}/run", params={"pace_ms": 0}).json()
        d = wait(c, r["run_id"])
        learn = next((e["summary"] for e in d["events"] if e["stage"] == "learn" and e["type"] == "stage.finished"), "")
        read = next((e for e in d["events"] if e["stage"] == "read" and e["type"] == "stage.finished"), {})
        row = {"id": s["id"], "outcome": (d["proposal"] or {}).get("outcome"), "tier": d["run"].get("read_tier"),
               "cost_usd": float(d["run"]["cost_usd"] or 0), "read_ms": read.get("duration_ms"), "learn": learn,
               "pass": (d["proposal"] or {}).get("outcome") in s["expected"]}
        rows.append(row)
        print(f"  {s['id']:4} {str(row['outcome']):13} {row['tier']}  ${row['cost_usd']:.4f}  read {row['read_ms']}ms  · {learn[:110]}")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--pace", type=int, default=0)
    ap.add_argument("--series", action="store_true", help="also run the learning-loop series")
    a = ap.parse_args()
    truth = json.loads((settings().scenarios_dir / "ground_truth.json").read_text())
    ids = [x for x in a.only.split(",") if x] or list(truth)
    results = []
    with httpx.Client(timeout=60) as c:
        if a.reset:
            c.post(f"{API}/api/admin/reset")
        for sid in ids:
            r = c.post(f"{API}/api/scenarios/{sid}/run", params={"pace_ms": a.pace}).json()
            d = wait(c, r["run_id"])
            s = score(sid, truth[sid], d)
            results.append(s)
            mark = "PASS" if s["pass"] else "FAIL"
            bad = [k for k, v in {**s["checks"], **s["fields"]}.items() if not v]
            print(f"{mark}  {sid:4} {str(s['outcome']):14} expected {'/'.join(s['expected']):14} tier={s['tier']} "
                  f"${s['cost_usd']:.4f} {s['ms']}ms" + (f"  ✗ {bad}" if bad else ""))
            if s["question"]:
                print(f"       Q: {s['question']}")
        series_rows = run_series(c) if a.series else None
    summary = {"at": datetime.now(timezone.utc).isoformat(), "n": len(results), "passed": sum(r["pass"] for r in results),
               "decision_accuracy": sum(r["checks"]["outcome"] for r in results) / max(1, len(results)),
               "verified_but_wrong": sum(len(r["verified_but_wrong"]) for r in results), "results": results,
               "series": series_rows}
    out = ROOT / "eval"
    out.mkdir(exist_ok=True)
    (out / "latest.json").write_text(json.dumps(summary, indent=2, default=str))
    print(f"\n{summary['passed']}/{summary['n']} scenarios pass · decision accuracy {summary['decision_accuracy']:.0%} · "
          f"verified-but-wrong fields: {summary['verified_but_wrong']}")


if __name__ == "__main__":
    main()
