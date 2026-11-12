"""Investigate (§6): one bounded agent for open-ended exceptions.

Read-only tools scoped to the resolved vendor. Sees facts only, never raw invoice/email text. ≤ 6 tool calls, ≤ 30 s.
Its proposals are re-checked by deterministic code in the workflow and can never raise the outcome above REVIEW.
Live: Gemini function calling. Fallback: a deterministic playbook that calls the same tools in a fixed order.
"""
from __future__ import annotations

import json
import time
from decimal import Decimal

from .. import db
from ..config import settings
from ..domain.money import fmt_money

INVESTIGABLE = {"PO-01", "MAT-02", "PO-03", "DUP-02", "PAY-01", "PAY-04", "VEN-01", "MAT-01"}
MAX_CALLS, MAX_SECONDS = 6, 30


# ---------------------------------------------------------------- tools (read-only)

def search_open_pos(vendor_id: str) -> list[dict]:
    pos = db.q("""SELECT p.id, p.total, p.issued_on,
                         coalesce((SELECT sum(amount) FROM billed_ledger b WHERE b.po_id=p.id AND state<>'reversed'),0) billed
                  FROM purchase_orders p WHERE vendor_id=%s AND status='open' ORDER BY id""", (vendor_id,))
    for p in pos:
        p["lines"] = db.q("SELECT sku, description, qty, unit_price FROM po_lines WHERE po_id=%s ORDER BY line_no", (p["id"],))
        p["remaining"] = Decimal(str(p["total"])) - Decimal(str(p["billed"]))
    return pos


def get_grns(po_id: str) -> list[dict]:
    return db.q("""SELECT g.id, g.received_on, g.posted_at, l.po_line_id, l.qty FROM goods_receipts g
                   JOIN grn_lines l ON l.grn_id=g.id WHERE g.po_id=%s ORDER BY g.posted_at""", (po_id,))


def vendor_invoice_history(vendor_id: str, n: int = 10) -> list[dict]:
    return db.q("""SELECT invoice_number, total, invoice_date, state, po_id FROM invoices WHERE vendor_id=%s
                   ORDER BY created_at DESC LIMIT %s""", (vendor_id, n))


def bank_change_history(vendor_id: str) -> list[dict]:
    return db.q("""SELECT right(coalesce(iban, account_number), 4) last4, added_at, verified_at, verified_by, active
                   FROM vendor_bank_accounts WHERE vendor_id=%s ORDER BY added_at""", (vendor_id,))


def find_similar_invoices(vendor_id: str, amount: float) -> list[dict]:
    return db.q("""SELECT invoice_number, total, invoice_date, state FROM invoices WHERE vendor_id=%s
                   AND abs(total - %s) < 0.01 ORDER BY created_at DESC LIMIT 5""", (vendor_id, amount))


def get_vendor_contact(vendor_id: str) -> dict | None:
    return db.q1("SELECT legal_name, phone, domains FROM vendors WHERE id=%s", (vendor_id,))


TOOLS = {
    "search_open_pos": (search_open_pos, "List this vendor's open purchase orders with lines and remaining balance.", {}),
    "get_grns": (get_grns, "List goods receipts posted against a PO, including ones posted after the invoice.",
                 {"po_id": {"type": "string"}}),
    "vendor_invoice_history": (vendor_invoice_history, "Recent invoices from this vendor.", {}),
    "bank_change_history": (bank_change_history, "When this vendor's bank details were added, verified or changed.", {}),
    "find_similar_invoices": (find_similar_invoices, "Invoices from this vendor with the same amount.", {"amount": {"type": "number"}}),
    "get_vendor_contact": (get_vendor_contact, "The vendor's master-data contact (for call-backs).", {}),
}


def _call(name: str, args: dict, vendor_id: str):
    fn = TOOLS[name][0]
    if name in ("search_open_pos", "vendor_invoice_history", "bank_change_history", "get_vendor_contact"):
        return fn(vendor_id)
    if name == "find_similar_invoices":
        return fn(vendor_id, float(args.get("amount", 0)))
    if name == "get_grns":
        po = str(args.get("po_id", ""))
        own = {p["id"] for p in db.q("SELECT id FROM purchase_orders WHERE vendor_id=%s", (vendor_id,))}
        return fn(po) if po in own else {"error": "PO not in this vendor's scope"}
    raise KeyError(name)


def _brief(facts: dict, fails: list[dict]) -> dict:
    keep = ["vendor_id", "vendor_legal_name", "invoice_number", "po_status", "po_id", "amount_total", "invoice_goods_billed",
            "currency", "po_total", "po_billed_before", "po_cumulative_after", "bank_matches_verified", "days_since_bank_change",
            "first_payment", "sender_domain_distance", "invoice_date"]
    lines = [{k: l.get(k) for k in ("description", "sku", "qty_billed", "qty_received_net", "qty_ordered_net", "unit_price",
                                    "po_unit_price", "paired")} for l in facts.get("lines", [])]
    return {"facts": {k: facts.get(k) for k in keep}, "lines": lines,
            "failing_checks": [{"rule": f["rule_id"], "message": f["message"]} for f in fails]}


# ---------------------------------------------------------------- deterministic playbook (offline / fallback)

def playbook(facts: dict, fails: list[dict]) -> dict:
    vid = facts["vendor_id"]
    ids = {f["rule_id"] for f in fails}
    steps, hyp, resolution, question = [], [], None, None

    def step(name, args, result, summary):
        steps.append({"tool": name, "args": args, "summary": summary, "rows": len(result) if isinstance(result, list) else 1})

    if "PO-01" in ids and facts.get("po_status") in ("none", "not_found"):
        pos = search_open_pos(vid)
        inv_skus = {l.get("sku") for l in facts.get("lines", []) if l.get("sku")}
        scored = []
        for p in pos:
            hits = len(inv_skus & {l["sku"] for l in p["lines"]})
            scored.append((hits, p))
        scored.sort(key=lambda x: -x[0])
        step("search_open_pos", {}, pos, f"{len(pos)} open POs checked")
        if scored and scored[0][0] == len(inv_skus) and inv_skus and (len(scored) == 1 or scored[1][0] < scored[0][0]):
            po = scored[0][1]["id"]
            resolution = {"type": "link_po", "po_id": po}
            hyp.append(f"{po} matches {len(inv_skus)} of {len(inv_skus)} invoice lines")
            question = f"Confirm {po}? It matches every line on the invoice."
        else:
            hyp.append("No single open PO explains all the lines")
    if ids & {"MAT-02"} and facts.get("po_id"):
        g = get_grns(facts["po_id"])
        step("get_grns", {"po_id": facts["po_id"]}, g, f"{len({r['id'] for r in g})} goods receipts on {facts['po_id']}")
        short = [l for l in facts.get("lines", []) if l.get("paired") and l["qty_billed"] > l["qty_received_net"]]
        if short:
            l = short[0]
            hyp.append(f"Billed {l['qty_billed']:g}, received {l['qty_received_net']:g}: nothing newer has been posted, "
                       "so the rest is probably still in transit")
    if ids & {"PO-03"} and facts.get("po_id"):
        h = vendor_invoice_history(vid)
        on_po = [x for x in h if x["po_id"] == facts["po_id"]]
        step("vendor_invoice_history", {}, h, f"{len(on_po)} earlier invoices on {facts['po_id']}")
        hyp.append(f"{len(on_po)} earlier invoices already billed {fmt_money(Decimal(str(facts.get('po_billed_before', 0))), facts.get('currency'))} "
                   f"of {fmt_money(Decimal(str(facts.get('po_total', 0))), facts.get('currency'))}; receiving accepted more than ordered")
    if ids & {"PAY-01", "PAY-04"}:
        b = bank_change_history(vid)
        step("bank_change_history", {}, b, f"{len(b)} bank account(s) on file")
        c = get_vendor_contact(vid)
        step("get_vendor_contact", {}, [c], "Master-data phone retrieved for call-back")
        hyp.append("The account on the invoice has never been on file for this vendor" if facts.get("bank_matches_verified") is False
                   else "Bank details changed recently")
    if ids & {"DUP-02"}:
        s = find_similar_invoices(vid, float(facts.get("amount_total") or 0))
        step("find_similar_invoices", {"amount": facts.get("amount_total")}, s, f"{len(s)} invoices with the same amount")
    return {"backend": "playbook", "steps": steps[:MAX_CALLS], "hypotheses": hyp, "proposed_resolution": resolution,
            "question": question}


# ---------------------------------------------------------------- live agent (Gemini function calling)

SYSTEM = """You investigate an exception on a vendor invoice for an accounts-payable team. You get facts, not the document.
Use the read-only tools to test explanations (e.g. find the PO an invoice belongs to, check for receipts posted later,
check bank-change history). Never invent a PO or a receipt. At most 6 tool calls.
Facts you can rely on: PO totals and PO prices are BEFORE tax; compare them with invoice_goods_billed (the invoice's
goods amount before tax), never with the invoice total. Only explain the failing checks you are given; do not restate
checks that passed. Keep each hypothesis to one short sentence a finance clerk understands.
Then answer as JSON:
{"hypotheses": [..short sentences..], "proposed_resolution": {"type": "link_po"|"none", "po_id": str|null},
 "question": "one specific question for the reviewer, or null"}"""


def investigate(facts: dict, fails: list[dict], on_step=None) -> dict:
    s = settings()
    if not s.gemini_enabled or s.ai_mode == "offline":
        res = playbook(facts, fails)
        for st in res["steps"]:
            on_step and on_step(st)
        return res
    try:
        return _live(facts, fails, on_step)
    except Exception as e:  # noqa: BLE001 - degrade to the playbook
        res = playbook(facts, fails)
        res["backend"] = f"playbook (agent unavailable: {type(e).__name__})"
        for st in res["steps"]:
            on_step and on_step(st)
        return res


def _live(facts: dict, fails: list[dict], on_step) -> dict:
    from google.genai import types

    from ..ai import gateway

    s = settings()
    decls = [types.FunctionDeclaration(name=n, description=d,
                                       parameters_json_schema={"type": "object", "properties": p, "required": list(p)})
             for n, (_, d, p) in TOOLS.items()]
    contents = [types.Content(role="user", parts=[types.Part(text=json.dumps(_brief(facts, fails), default=str))])]
    cfg = types.GenerateContentConfig(system_instruction=SYSTEM, temperature=0,
                                      tools=[types.Tool(function_declarations=decls)],
                                      automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                                      thinking_config=types.ThinkingConfig(thinking_level="low"))
    steps, t0, calls, cost = [], time.monotonic(), 0, 0.0
    vid = facts["vendor_id"]
    while True:
        r = gateway.genai_client().models.generate_content(model=s.gemini_agent, contents=contents, config=cfg)
        u = r.usage_metadata
        cost += gateway.cost_of(s.gemini_agent, {"input_tokens": u.prompt_token_count or 0,
                                                  "output_tokens": (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)})
        fcs = r.function_calls or []
        if not fcs or calls >= MAX_CALLS or time.monotonic() - t0 > MAX_SECONDS:
            break
        contents.append(r.candidates[0].content)
        parts = []
        for fc in fcs[: MAX_CALLS - calls]:
            calls += 1
            try:
                out = _call(fc.name, dict(fc.args or {}), vid)
            except Exception as e:  # noqa: BLE001
                out = {"error": str(e)}
            st = {"tool": fc.name, "args": dict(fc.args or {}), "rows": len(out) if isinstance(out, list) else 1,
                  "summary": f"{fc.name}: {len(out) if isinstance(out, list) else 1} result(s)"}
            steps.append(st)
            on_step and on_step(st)
            parts.append(types.Part.from_function_response(name=fc.name, response={"result": json.loads(json.dumps(out, default=str))}))
        contents.append(types.Content(role="user", parts=parts))
    text = r.text or "{}"
    try:
        out = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except ValueError:
        out = {"hypotheses": [text[:300]], "proposed_resolution": None, "question": None}
    gateway._add_spend(cost)
    res = playbook(facts, fails) if not steps else None  # agent made no calls: use playbook evidence
    return {"backend": f"agent ({s.gemini_agent})", "steps": steps or (res or {}).get("steps", []),
            "hypotheses": out.get("hypotheses") or (res or {}).get("hypotheses", []),
            "proposed_resolution": out.get("proposed_resolution"), "question": out.get("question"), "cost_usd": round(cost, 6)}
