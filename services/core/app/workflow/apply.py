"""apply_decision (§3.4): validate action, role and segregation of duties → commit (locks, re-checks, ledger)
→ post bill to the mock ERP → read back → learn. Each decision is consumed exactly once."""
from __future__ import annotations

from decimal import Decimal

from dbos import DBOS

from .. import db
from ..domain.money import fmt_money, q2
from ..domain.normalise import iban_norm
from ..learning import memory as LM
from .events import emit


class DecisionError(Exception):
    pass


ROLE_RANK = {"ap_clerk": 1, "ap_manager": 2, "controller": 3, "system": 9}
OWNER_MIN = {"ap_clerk": "ap_clerk", "receiving": "ap_clerk", "procurement": "ap_clerk", "budget_owner": "ap_manager",
             "ap_manager": "ap_manager", "controller": "controller"}
ACTIONS = {"approve", "short_pay", "reject", "request_info", "verify_bank", "correct_field", "link_po", "file", "apply_credit"}


def validate(p: dict, actor: str, role: str, action: str, reason: str, amount, data: dict) -> None:
    if action not in ACTIONS:
        raise DecisionError(f"unknown action {action}")
    if p["superseded_by"]:
        raise DecisionError("this proposal was superseded by a newer run; act on the latest one")
    if not reason or len(reason.strip()) < 3:
        raise DecisionError("a reason is required")
    done = db.q("SELECT action FROM decisions WHERE proposal_id=%s", (p["id"],))
    if any(d["action"] in ("approve", "short_pay", "reject", "file", "apply_credit") for d in done):
        raise DecisionError("this proposal has already been decided")
    if role == "system":
        if p["outcome"] != "AUTO_APPROVE" or action != "approve":
            raise DecisionError("the system may only post auto-approved invoices")
        return
    need = OWNER_MIN.get(p["owner_role"] or "ap_clerk", "ap_clerk")
    fails = {f["rule_id"] for f in p["findings"]["findings"] if f["status"] == "fail"}
    if action == "verify_bank" and role != "controller":
        raise DecisionError("only a controller can verify bank details")
    if action in ("approve", "short_pay"):
        if p["safe_to_pay"] == "no":
            raise DecisionError("not safe to pay: a controller must verify the bank account by call-back first")
        if "APR-01" in fails:
            need = "controller"
        if ROLE_RANK.get(role, 0) < ROLE_RANK[need]:
            raise DecisionError(f"needs {need.replace('_', ' ')} or above")
        # Segregation of duties on the same invoice.
        sod = db.q("""SELECT d.actor, d.action FROM decisions d JOIN proposals p ON p.id=d.proposal_id
                      WHERE p.invoice_id=%s AND d.action IN ('verify_bank','correct_field','link_po')""", (p["invoice_id"],))
        clash = [s for s in sod if s["actor"] == actor]
        if clash:
            raise DecisionError(f"segregation of duties: {actor} already did '{clash[0]['action'].replace('_', ' ')}' on this invoice")
    if action == "short_pay":
        if amount is None or Decimal(str(amount)) <= 0:
            raise DecisionError("short-pay needs an amount")
    if action == "correct_field" and not (data.get("field") and data.get("value")):
        raise DecisionError("correct_field needs field and value")
    if action == "link_po" and not data.get("po_id"):
        raise DecisionError("link_po needs po_id")


@DBOS.workflow()
def apply_decision(proposal_id: int, actor: str, role: str, action: str, reason: str, amount, data: dict) -> dict:
    p = _load(proposal_id)
    validate(p, actor, role, action, reason, amount, data)
    _record(proposal_id, actor, role, action, reason, amount, data)
    run_id = p["run_id"]
    if action in ("approve", "short_pay"):
        pay = Decimal(str(amount)) if action == "short_pay" else Decimal(str(p["amount_payable"]))
        res = commit_and_post(proposal_id, pay, actor)
        if res.get("posted"):
            learn_step(run_id, actor, role)
        return res
    if action == "reject":
        _set(p, "rejected", "completed", f"Rejected by {actor}: {reason}")
        return {"status": "rejected"}
    if action in ("file", "apply_credit"):
        _set(p, "filed" if action == "file" else "credit_applied", "completed", f"{action.replace('_', ' ').title()} by {actor}")
        return {"status": action}
    if action == "request_info":
        db.q("UPDATE proposals SET sub_status='awaiting_vendor' WHERE id=%s", (proposal_id,))
        emit(run_id, "decide", "human", "awaiting_vendor", f"{actor} sent the vendor email: {reason}")
        return {"status": "awaiting_vendor"}
    if action == "verify_bank":
        res = verify_bank_step(p, actor, data.get("result", "confirmed"), reason)
        if res["status"] == "rerun":
            res["run_id"] = start_child(p, "correction", {})
        return res
    if action in ("correct_field", "link_po"):
        pinned = {data["field"]: data["value"]} if action == "correct_field" else {"po_number": data["po_id"]}
        child = start_child(p, "correction", pinned)
        return {"status": "rerun", "run_id": child}
    return {"status": "noop"}


@DBOS.step()
def _record(proposal_id, actor, role, action, reason, amount, data):
    db.q("""INSERT INTO decisions (proposal_id, actor, role, action, reason, amount, data) VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (proposal_id, action) DO NOTHING""", (proposal_id, actor, role, action, reason, amount, db.J(data or {})))
    db.q("INSERT INTO audit_log (actor, action, entity, after) VALUES (%s,%s,%s,%s)",
         (actor, action, f"proposal:{proposal_id}", db.J({"role": role, "reason": reason, "amount": amount, "data": data})))


def _load(pid: int) -> dict:
    p = db.q1("SELECT * FROM proposals WHERE id=%s", (pid,))
    if not p:
        raise DecisionError("no such proposal")
    return p


@DBOS.step()
def _set(p: dict, inv_state: str, run_status: str, msg: str) -> None:
    db.q("UPDATE invoices SET state=%s WHERE id=%s", (inv_state, p["invoice_id"]))
    db.q("UPDATE runs SET status=%s, finished_at=now() WHERE id=%s", (run_status, p["run_id"]))
    emit(p["run_id"], "commit", "run.finished", inv_state, msg)


@DBOS.step()
def commit_and_post(proposal_id: int, pay: Decimal, actor: str) -> dict:
    p = _load(proposal_id)
    inv = db.q1("SELECT * FROM invoices WHERE id=%s", (p["invoice_id"],))
    f = db.q1("SELECT extraction, facts FROM facts WHERE run_id=%s", (p["run_id"],))
    lines = f["facts"]["lines"]
    run_id = p["run_id"]
    emit(run_id, "commit", "stage.started", "running")
    try:
        with db.tx() as c:
            # Locks: the PO and the vendor+number pair (covers non-PO invoices and cross-file duplicates).
            if inv["po_id"]:
                c.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (inv["po_id"],))
            c.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{inv['vendor_id']}|{inv['invoice_number_norm']}",))
            dup = c.execute("""SELECT invoice_id FROM billed_ledger WHERE vendor_id=%s AND invoice_number_norm=%s
                               AND state<>'reversed' AND invoice_id<>%s LIMIT 1""",
                            (inv["vendor_id"], inv["invoice_number_norm"], inv["id"])).fetchone()
            if dup:
                raise DecisionError("re-check at commit: a duplicate was posted in the meantime")
            tranche = c.execute("SELECT coalesce(max(tranche),0)+1 t FROM billed_ledger WHERE invoice_id=%s", (inv["id"],)).fetchone()["t"]
            goods = [l for l in lines if l.get("paired") and l["line_kind"] in ("goods", "service")]
            goods_total = sum((Decimal(str(l["amount"])) for l in goods), Decimal("0"))
            goods_at_po = sum((Decimal(str(l["extended_po"])) for l in goods), Decimal("0"))
            ratio = (pay / Decimal(str(inv["total"]))) if inv["total"] else Decimal("1")
            if inv["po_id"]:
                po = c.execute("SELECT total FROM purchase_orders WHERE id=%s", (inv["po_id"],)).fetchone()
                billed = c.execute("SELECT coalesce(sum(amount),0) s FROM billed_ledger WHERE po_id=%s AND state<>'reversed'",
                                   (inv["po_id"],)).fetchone()["s"]
                if Decimal(str(billed)) + q2(goods_at_po * ratio) > Decimal(str(po["total"])) * Decimal("1.01") + Decimal("0.01"):
                    raise DecisionError("re-check at commit: PO balance changed; back to review")
            posted_lines = []
            if goods:
                for l in goods:
                    amt = q2(Decimal(str(l["extended_po"])) * ratio)  # PO value consumed, at PO (net) prices
                    qty = q2(Decimal(str(l["qty_billed"])) * ratio)
                    c.execute("""INSERT INTO billed_ledger (po_line_id, po_id, invoice_id, vendor_id, invoice_number_norm, qty, amount, tranche)
                                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                              (l["po_line_id"], inv["po_id"], inv["id"], inv["vendor_id"], inv["invoice_number_norm"], qty, amt, tranche))
                    posted_lines.append({"po_line_id": l["po_line_id"], "qty": float(qty), "amount": float(amt)})
            else:
                c.execute("""INSERT INTO billed_ledger (po_id, invoice_id, vendor_id, invoice_number_norm, amount, tranche)
                             VALUES (%s,%s,%s,%s,%s,%s)""", (inv["po_id"], inv["id"], inv["vendor_id"], inv["invoice_number_norm"], pay, tranche))
            bill_id = f"B-{1000 + inv['id']}" + (f"-{tranche}" if tranche > 1 else "")
            terms = (p["findings"] or {}).get("terms") or {}
            c.execute("""INSERT INTO erp_bills (id, invoice_id, vendor_id, amount, currency, due_date, lines) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                      (bill_id, inv["id"], inv["vendor_id"], pay, inv["currency"], terms.get("due_date"), db.J(posted_lines)))
            held = q2(Decimal(str(inv["total"])) - pay -
                      Decimal(str(c.execute("SELECT coalesce(sum(amount),0) s FROM erp_bills WHERE invoice_id=%s AND id<>%s",
                                            (inv["id"], bill_id)).fetchone()["s"])))
            c.execute("UPDATE invoices SET state=%s WHERE id=%s", ("approved" if held <= 0 else "partially_paid", inv["id"]))
    except DecisionError as e:
        emit(run_id, "commit", "stage.finished", "block", str(e))
        db.q("UPDATE runs SET status='awaiting_human' WHERE id=%s", (run_id,))
        return {"posted": False, "error": str(e)}
    # Read back from the "ERP" and compare.
    rb = db.q1("SELECT amount, lines FROM erp_bills WHERE id=%s", (bill_id,))
    ok = rb and Decimal(str(rb["amount"])) == pay and len(rb["lines"]) == len(posted_lines)
    po_left = ""
    if inv["po_id"]:
        r = db.q1("""SELECT p.total - coalesce((SELECT sum(amount) FROM billed_ledger b WHERE b.po_id=p.id AND state<>'reversed'),0) left
                     FROM purchase_orders p WHERE id=%s""", (inv["po_id"],))
        po_left = f" · {inv['po_id']} remaining {fmt_money(Decimal(str(r['left'])), inv['currency'])}"
    emit(run_id, "commit", "stage.finished", "pass" if ok else "block",
         f"Bill {bill_id} posted for {fmt_money(pay, inv['currency'])} · read-back {'OK' if ok else 'MISMATCH'}{po_left}",
         {"bill_id": bill_id, "amount": str(pay), "read_back_ok": bool(ok), "approved_by": actor})
    db.q("UPDATE runs SET status='completed', finished_at=now() WHERE id=%s", (run_id,))
    return {"posted": True, "bill_id": bill_id, "amount": str(pay), "read_back_ok": bool(ok)}


@DBOS.step()
def learn_step(run_id: str, actor: str, role: str) -> None:
    """Learn (§16) from a posted run — only if it was clean."""
    emit(run_id, "learn", "stage.started", "running")
    row = db.q1("SELECT f.extraction, f.facts, r.read_tier, r.layout_id, d.line_map FROM facts f JOIN runs r ON r.id=f.run_id "
                "JOIN documents d ON d.id=r.document_id WHERE f.run_id=%s", (run_id,))
    E, facts = row["extraction"], row["facts"]
    vid = facts.get("vendor_id")
    notes: list[str] = []
    clean = not any(facts.get(k) for k in ("sender_domain_distance", "reply_to_mismatch")) and facts.get("bank_matches_verified") is not False \
        and (facts.get("jev_instructs_automation") or 0) < 0.5
    corrected = bool(db.q1("SELECT 1 FROM runs WHERE id=%s AND pinned_fields <> '{}'::jsonb", (run_id,)))
    if vid and row["line_map"] and row["line_map"].get("text_layer"):
        from ..domain.models import Extraction

        notes += LM.learn_from_posted(run_id, vid, row["line_map"], E["fields"], Extraction(**E["extraction"]), E.get("shadow"),
                                      row["layout_id"], row["read_tier"] or "T3", clean, corrected)
    if vid and clean and E["vendor"]["method"] in ("fuzzy", "jev") and role != "system":
        n = LM.learn_alias(run_id, vid, E["fields"]["vendor_name"]["value"])
        n and notes.append(n)
    if vid and clean:
        pairs = [{"description": l["description"], "method": l.get("pair_method"), "po_sku": l.get("po_sku")}
                 for l in facts["lines"] if l.get("paired") and not l.get("sku")]
        notes += LM.learn_items(run_id, vid, pairs)
    emit(run_id, "learn", "stage.finished", "pass", " · ".join(notes) or "Nothing new to learn", {"notes": notes})


@DBOS.step()
def verify_bank_step(p: dict, actor: str, result: str, reason: str) -> dict:
    f = db.q1("SELECT facts, extraction FROM facts WHERE run_id=%s", (p["run_id"],))
    inv = db.q1("SELECT * FROM invoices WHERE id=%s", (p["invoice_id"],))
    iban = iban_norm(f["extraction"]["fields"]["remit_iban"]["value"])
    if result == "denied":
        db.q("UPDATE invoices SET state='rejected' WHERE id=%s", (inv["id"],))
        db.q("UPDATE runs SET status='completed', finished_at=now() WHERE id=%s", (p["run_id"],))
        emit(p["run_id"], "decide", "run.finished", "rejected",
             f"Call-back by {actor}: vendor did NOT change bank details. Invoice rejected as attempted payment fraud.")
        return {"status": "rejected_fraud"}
    phone = db.q1("SELECT phone FROM vendors WHERE id=%s", (inv["vendor_id"],))["phone"]
    db.q("UPDATE vendor_bank_accounts SET active=false WHERE vendor_id=%s", (inv["vendor_id"],))
    db.q("""INSERT INTO vendor_bank_accounts (vendor_id, iban, bank_name, verified_at, verified_by, callback_phone)
            VALUES (%s,%s,'Verified by call-back',now(),%s,%s)""", (inv["vendor_id"], iban, f"callback:{actor}", phone))
    emit(p["run_id"], "decide", "human", "pass", f"Call-back by {actor} on {phone}: new account confirmed. Re-checking.")
    return {"status": "rerun"}


def start_child(p: dict, trigger: str, pinned: dict) -> str:
    from .process import new_run

    run = db.q1("SELECT document_id, pinned_fields FROM runs WHERE id=%s", (p["run_id"],))
    return new_run(run["document_id"], trigger, p["invoice_id"], p["run_id"], {**(run["pinned_fields"] or {}), **pinned},
                   start=True)


def on_grn_posted(po_id: str) -> list[str]:
    """Event-driven re-match (§3.4): awaiting_receipt cases on this PO start a child run automatically."""
    rows = db.q("""SELECT p.* FROM proposals p JOIN invoices i ON i.id=p.invoice_id
                   WHERE i.po_id=%s AND p.superseded_by IS NULL AND (p.sub_status='awaiting_receipt' OR i.state='partially_paid')""",
                (po_id,))
    out = []
    for p in rows:
        emit(p["run_id"], "event", "info", "running", f"Goods receipt posted on {po_id}: re-matching automatically")
        out.append(start_child(p, "grn_posted", {}))
    return out
