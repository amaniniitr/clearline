"""Clearline API: FastAPI + DBOS in one process. SSE for the live run view."""
from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from dbos import DBOS, SetWorkflowID
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import db
from .config import settings
from .ingest import intake, preprocess
from .learning import memory as LM
from .scenarios.catalog import BY_ID, SCENARIOS

s = settings()
DBOS(config={"name": "clearline", "system_database_url": s.dbos_database_url, "application_version": s.app_version,
             "log_level": "WARNING"})

from .workflow import apply as A  # noqa: E402  (workflows register after DBOS())
from .workflow import process as WP  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.migrate()
    if not db.q1("SELECT 1 FROM vendors LIMIT 1"):
        from .seed.master import seed

        seed()
    DBOS.launch()
    threading.Thread(target=preprocess._ocr, daemon=True).start()  # warm OCR so the first render check is fast
    yield
    DBOS.destroy()


app = FastAPI(title="Clearline", version=s.app_version, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def jsonable(o):
    return json.loads(db.dumps(o))


def J(o, status: int = 200):
    return JSONResponse(jsonable(o), status_code=status)


@app.get("/api/health")
def health():
    return {"ok": True, "provider": s.model_provider, "llm": s.llm_enabled, "jev": s.jev_enabled, "mode": s.ai_mode,
            "models": {"read_a": s.gemini_read_a, "read_b": s.gemini_read_b, "reread": s.gemini_reread}}


# ------------------------------------------------------------------ scenarios & intake

@app.get("/api/scenarios")
def scenarios():
    out_dir = s.scenarios_dir
    truth = json.loads((out_dir / "ground_truth.json").read_text()) if (out_dir / "ground_truth.json").exists() else {}
    extra = json.loads((out_dir / "series.json").read_text()) if (out_dir / "series.json").exists() else []
    items = [{k: v for k, v in sc.items() if k in ("id", "title", "tag", "story")} | {"file": truth.get(sc["id"], {}).get("file"),
              "expected": truth.get(sc["id"], {}).get("outcomes")} for sc in SCENARIOS]
    return J({"scenarios": items, "series": extra})


def _start(doc_id: int, redelivery: bool, pace_ms: int) -> dict:
    if redelivery:
        r = db.q1("SELECT id FROM runs WHERE document_id=%s AND parent_run_id IS NULL ORDER BY started_at LIMIT 1", (doc_id,))
        if r:
            return {"run_id": r["id"], "redelivery": True}
    return {"run_id": WP.new_run(doc_id, pace_ms=pace_ms), "redelivery": False}


@app.post("/api/scenarios/{sid}/run")
def run_scenario(sid: str, pace_ms: int = Query(350, ge=0, le=3000)):
    truth = json.loads((s.scenarios_dir / "ground_truth.json").read_text())
    series = {x["id"]: x for x in json.loads((s.scenarios_dir / "series.json").read_text())} if (s.scenarios_dir / "series.json").exists() else {}
    meta = truth.get(sid) or series.get(sid)
    if not meta:
        raise HTTPException(404, "unknown scenario")
    doc_id, again = intake.ingest_path(s.scenarios_dir / meta["file"], sid)
    return J(_start(doc_id, again, pace_ms))


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), pace_ms: int = Query(0, ge=0, le=3000)):
    data = await file.read()
    try:
        doc_id, again = intake.ingest_bytes(data, file.filename or "upload", "upload")
    except intake.IntakeError as e:
        raise HTTPException(400, str(e))
    return J(_start(doc_id, again, pace_ms))


# ------------------------------------------------------------------ runs

RUN_LIST_SQL = """
SELECT r.id, r.status, r.trigger, r.parent_run_id, r.started_at, r.finished_at, r.cost_usd, r.read_tier, r.policy_version,
       r.decision_backend, d.filename, d.scenario, d.channel, i.id invoice_id, i.invoice_number, i.total, i.currency, i.state invoice_state,
       v.legal_name vendor, p.id proposal_id, p.outcome, p.sub_status, p.owed, p.safe_to_pay, p.owner_role, p.question,
       p.amount_payable, p.amount_held, p.superseded_by, p.created_at proposed_at, r.sla_due_at,
       (SELECT max(ts) FROM run_events e WHERE e.run_id=r.id) last_event_at
FROM runs r JOIN documents d ON d.id=r.document_id LEFT JOIN invoices i ON i.id=r.invoice_id
LEFT JOIN vendors v ON v.id=i.vendor_id LEFT JOIN proposals p ON p.run_id=r.id
"""


@app.get("/api/runs")
def runs(limit: int = 200):
    return J(db.q(RUN_LIST_SQL + " ORDER BY r.started_at DESC LIMIT %s", (limit,)))


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    r = db.q1(RUN_LIST_SQL + " WHERE r.id=%s", (run_id,))
    if not r:
        raise HTTPException(404)
    run = db.q1("SELECT * FROM runs WHERE id=%s", (run_id,))
    doc = db.q1("SELECT id, filename, mime, pages, text_layer, channel, email_from, email_reply_to, email_subject, email_body, line_map "
                "FROM documents WHERE id=%s", (run["document_id"],))
    f = db.q1("SELECT extraction, facts FROM facts WHERE run_id=%s", (run_id,))
    prop = db.q1("SELECT * FROM proposals WHERE run_id=%s", (run_id,))
    inv = db.q1("SELECT * FROM investigations WHERE run_id=%s", (run_id,))
    events = db.q("SELECT * FROM run_events WHERE run_id=%s ORDER BY seq", (run_id,))
    decisions = db.q("SELECT * FROM decisions WHERE proposal_id=%s ORDER BY decided_at", (prop["id"],)) if prop else []
    lineage = []
    if r["invoice_id"]:
        lineage = db.q("""SELECT r.id, r.trigger, r.status, r.started_at, p.outcome, p.amount_payable, p.superseded_by
                          FROM runs r LEFT JOIN proposals p ON p.run_id=r.id WHERE r.invoice_id=%s ORDER BY r.started_at""",
                       (r["invoice_id"],))
    po = None
    if f and f["extraction"]["po"]["po_id"]:
        pid = f["extraction"]["po"]["po_id"]
        po = {"id": pid, "row": db.q1("SELECT * FROM purchase_orders WHERE id=%s", (pid,)),
              "lines": db.q("""SELECT l.*, coalesce((SELECT sum(qty) FROM grn_lines g WHERE g.po_line_id=l.id),0) received,
                               coalesce((SELECT sum(qty) FROM billed_ledger b WHERE b.po_line_id=l.id AND state<>'reversed'),0) billed
                               FROM po_lines l WHERE po_id=%s ORDER BY line_no""", (pid,)),
              "grns": db.q("SELECT id, received_on, posted_at FROM goods_receipts WHERE po_id=%s ORDER BY posted_at", (pid,))}
    if doc and doc["line_map"]:
        doc["pages_meta"] = doc["line_map"]["pages"]
        doc.pop("line_map")
    return J({"run": {**run, **r}, "document": doc, "facts": f, "proposal": prop, "investigation": inv, "events": events,
              "decisions": decisions, "lineage": lineage, "po": po})


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, request: Request):
    last = int(request.headers.get("last-event-id") or 0)

    async def gen():
        nonlocal last
        idle = 0
        while True:
            if await request.is_disconnected():
                return
            rows = await asyncio.to_thread(db.q, "SELECT * FROM run_events WHERE run_id=%s AND seq>%s ORDER BY seq", (run_id, last))
            for e in rows:
                last = e["seq"]
                yield {"id": str(e["seq"]), "event": "message", "data": db.dumps(e)}
            idle = 0 if rows else idle + 1
            if idle and idle % 60 == 0:
                yield {"event": "ping", "data": "{}"}
            await asyncio.sleep(0.25)

    return EventSourceResponse(gen(), headers={"X-Accel-Buffering": "no"})


@app.get("/api/documents/{doc_id}/file")
def doc_file(doc_id: int):
    d = db.q1("SELECT storage_path, mime, filename FROM documents WHERE id=%s", (doc_id,))
    if not d:
        raise HTTPException(404)
    return FileResponse(d["storage_path"], media_type=d["mime"])


@app.get("/api/documents/{doc_id}/page/{n}.png")
def doc_page(doc_id: int, n: int, scale: float = 1.5):
    d = db.q1("SELECT storage_path, mime FROM documents WHERE id=%s", (doc_id,))
    if not d:
        raise HTTPException(404)
    if d["mime"] != "application/pdf":
        return FileResponse(d["storage_path"], media_type=d["mime"])
    return Response(preprocess.render_page_png(Path(d["storage_path"]), n, scale), media_type="image/png",
                    headers={"Cache-Control": "max-age=3600"})


# ------------------------------------------------------------------ decisions & events

class DecisionIn(BaseModel):
    actor: str
    role: str
    action: str
    reason: str
    amount: float | None = None
    data: dict = {}


@app.post("/api/proposals/{pid}/decisions")
def decide(pid: int, d: DecisionIn):
    p = db.q1("SELECT * FROM proposals WHERE id=%s", (pid,))
    if not p:
        raise HTTPException(404)
    try:
        A.validate(p, d.actor, d.role, d.action, d.reason, d.amount, d.data)
    except A.DecisionError as e:
        raise HTTPException(409, str(e))
    with SetWorkflowID(f"decision-{pid}-{d.action}"):
        h = DBOS.start_workflow(A.apply_decision, pid, d.actor, d.role, d.action, d.reason, d.amount, d.data)
    try:
        res = h.get_result()
    except A.DecisionError as e:
        raise HTTPException(409, str(e))
    return J(res)


class GrnIn(BaseModel):
    po_id: str
    lines: list[dict]   # [{po_line_id, qty}]
    grn_id: str | None = None


@app.post("/api/grns")
def post_grn(g: GrnIn):
    gid = g.grn_id or f"GRN-{900 + (db.q1('SELECT count(*) n FROM goods_receipts')['n'])}"
    with db.tx() as c:
        c.execute("INSERT INTO goods_receipts (id, po_id, received_on) VALUES (%s,%s,%s)", (gid, g.po_id, date.today()))
        for l in g.lines:
            c.execute("INSERT INTO grn_lines VALUES (%s,%s,%s)", (gid, l["po_line_id"], l["qty"]))
    children = A.on_grn_posted(g.po_id)
    return J({"grn_id": gid, "rematched_runs": children})


@app.post("/api/demo/grn-883")
def demo_grn():
    if db.q1("SELECT 1 FROM goods_receipts WHERE id='GRN-883'"):
        raise HTTPException(409, "GRN-883 already posted")
    return post_grn(GrnIn(po_id="PO-4420", lines=[{"po_line_id": "PO-4420-1", "qty": 40}], grn_id="GRN-883"))


@app.post("/api/admin/reset")
def reset(keep_learning: bool = False):
    from .seed.master import seed

    seed()
    for f in s.storage_dir.glob("*"):
        f.unlink()
    return {"ok": True}


# ------------------------------------------------------------------ queue, dashboard, learning, master data, policy

@app.get("/api/queue")
def queue():
    rows = db.q(RUN_LIST_SQL + """ WHERE r.status='awaiting_human' AND p.superseded_by IS NULL ORDER BY r.sla_due_at NULLS LAST,
                p.amount_held DESC NULLS LAST""")
    return J(rows)


@app.get("/api/dashboard")
def dashboard():
    latest = db.q("""SELECT p.outcome, p.sub_status, p.amount_payable, p.amount_held, p.findings, p.owed, p.safe_to_pay, i.total, i.currency,
                            r.cost_usd, r.read_tier, r.started_at, r.finished_at, r.trigger,
                            (SELECT min(ts) FROM run_events e WHERE e.run_id=r.id) t0,
                            (SELECT max(ts) FROM run_events e WHERE e.run_id=r.id AND e.stage IN ('decide','commit')) t1
                     FROM proposals p JOIN runs r ON r.id=p.run_id JOIN invoices i ON i.id=p.invoice_id
                     WHERE p.superseded_by IS NULL""")
    first_runs = db.q("""SELECT p.outcome FROM proposals p JOIN runs r ON r.id=p.run_id WHERE r.parent_run_id IS NULL""")
    n = len(latest)
    by = {}
    for r in latest:
        by[r["outcome"]] = by.get(r["outcome"], 0) + 1
    touchless = sum(1 for r in first_runs if r["outcome"] == "AUTO_APPROVE")
    caught = {"payment_safety": Decimal(0), "duplicate": Decimal(0), "overbilling": Decimal(0)}
    rules = {}
    for r in latest:
        fails = [f for f in r["findings"]["findings"] if f["status"] == "fail"]
        for f in fails:
            rules[f["rule_id"]] = rules.get(f["rule_id"], 0) + 1
        ids = {f["rule_id"] for f in fails}
        from .domain.money import to_usd

        tot = to_usd(Decimal(str(r["total"] or 0)), r["currency"])
        if r["safe_to_pay"] == "no":
            caught["payment_safety"] += tot
        elif "DUP-01" in ids:
            caught["duplicate"] += tot
        elif ids & {"MAT-01", "MAT-02", "PO-03"}:
            caught["overbilling"] += to_usd(Decimal(str(r["amount_held"] or 0)), r["currency"])
    machine_ms = sorted(((r["t1"] - r["t0"]).total_seconds() * 1000 for r in latest if r["t0"] and r["t1"]))
    costs = [float(r["cost_usd"] or 0) for r in latest]
    weekly = db.q("""SELECT date_trunc('day', r.started_at) d, count(*) n, avg(r.cost_usd) cost,
                            sum(CASE WHEN p.outcome='AUTO_APPROVE' THEN 1 ELSE 0 END) auto,
                            sum(CASE WHEN r.read_tier='T1' THEN 1 ELSE 0 END) t1, sum(CASE WHEN r.read_tier='T2' THEN 1 ELSE 0 END) t2
                     FROM runs r LEFT JOIN proposals p ON p.run_id=r.id WHERE r.trigger='ingest' GROUP BY 1 ORDER BY 1""")
    return J({"invoices": n, "by_outcome": by, "touchless_rate": (touchless / len(first_runs)) if first_runs else 0,
              "caught": caught, "top_rules": sorted(rules.items(), key=lambda x: -x[1])[:8],
              "median_machine_ms": machine_ms[len(machine_ms) // 2] if machine_ms else None,
              "avg_cost_usd": sum(costs) / len(costs) if costs else 0, "total_cost_usd": sum(costs),
              "awaiting": db.q1("SELECT count(*) n FROM runs WHERE status='awaiting_human'")["n"], "daily": weekly,
              "eval": _latest_eval()})


def _latest_eval():
    from .config import ROOT

    p = ROOT / "eval" / "latest.json"
    return json.loads(p.read_text()) if p.exists() else None


@app.get("/api/learning")
def learning():
    layouts = db.q("""SELECT l.id, l.vendor_id, v.legal_name vendor, l.status, l.agree_streak, l.uses, l.demotions, l.version,
                             array_length(l.learned_from,1) learned_runs, l.history, l.updated_at,
                             (SELECT count(*) FROM jsonb_object_keys(l.template->'fields')) nfields
                      FROM layouts l JOIN vendors v ON v.id=l.vendor_id ORDER BY l.vendor_id, l.id""")
    curve = db.q("""SELECT r.id, r.started_at, r.read_tier, r.cost_usd, v.legal_name vendor, p.outcome,
                           (SELECT max(ts)-min(ts) FROM run_events e WHERE e.run_id=r.id AND e.stage='read') read_time
                    FROM runs r JOIN proposals p ON p.run_id=r.id LEFT JOIN invoices i ON i.id=r.invoice_id
                    LEFT JOIN vendors v ON v.id=i.vendor_id WHERE r.trigger='ingest' ORDER BY r.started_at""")
    for c in curve:
        c["read_ms"] = int(c.pop("read_time").total_seconds() * 1000) if c.get("read_time") else None
    return J({"layouts": layouts, "items": db.q("SELECT * FROM vendor_item_map ORDER BY created_at DESC"),
              "aliases": db.q("SELECT * FROM vendor_aliases WHERE source='learned'"),
              "cache": db.q1("SELECT count(*) entries, coalesce(sum(hits),0) hits FROM judgment_cache"),
              "events": db.q("SELECT * FROM learning_events ORDER BY at DESC LIMIT 50"),
              "suggestions": LM.policy_suggestions(), "curve": curve})


@app.get("/api/vendors")
def vendors():
    vs = db.q("SELECT * FROM vendors ORDER BY id")
    for v in vs:
        v["banks"] = db.q("""SELECT id, right(coalesce(iban,account_number),4) last4, bank_name, verified_at, verified_by,
                             callback_phone, added_at, active FROM vendor_bank_accounts WHERE vendor_id=%s ORDER BY added_at""", (v["id"],))
        v["aliases"] = db.q("SELECT alias, source FROM vendor_aliases WHERE vendor_id=%s", (v["id"],))
        v["pos"] = db.q("""SELECT p.id, p.total, p.status, p.issued_on,
                           coalesce((SELECT sum(amount) FROM billed_ledger b WHERE b.po_id=p.id AND state<>'reversed'),0) billed
                           FROM purchase_orders p WHERE vendor_id=%s ORDER BY id""", (v["id"],))
    return J(vs)


@app.get("/api/policy")
def policy():
    from .policy import catalog

    pol = db.q("SELECT version, text_md, status, drafted_by, approved_by, approved_at, created_at FROM policies ORDER BY created_at DESC")
    active = db.q1("SELECT jdm FROM policies WHERE status='active' ORDER BY created_at DESC LIMIT 1")
    rules = [{"id": r["id"], "title": r["title"], "locked": r["id"] in catalog.LOCKED, "stage": r["stage"],
              "fails": [{"when": c, "outcome": o, "owner": w, "message": m} for c, o, w, a, m in r["fail"]]} for r in catalog.RULES]
    return J({"versions": pol, "rules": rules, "tolerances": active["jdm"]["meta"], "jdm": active["jdm"]})
