"""Persistence for everything the system learns (§16). Learns only from clean, posted runs."""
from __future__ import annotations

import hashlib
from collections import defaultdict

from .. import db
from ..domain.normalise import name_norm
from . import layouts as L


def event(run_id: str | None, vendor_id: str | None, kind: str, **detail) -> None:
    db.q("INSERT INTO learning_events (run_id, vendor_id, kind, detail) VALUES (%s,%s,%s,%s)",
         (run_id, vendor_id, kind, db.J(detail)))


def all_layouts() -> list[dict]:
    return db.q("SELECT * FROM layouts WHERE status <> 'retired' ORDER BY id")


def tier_for(layout: dict | None) -> str:
    if not layout:
        return "T3"
    return {"t1": "T1", "t2": "T2"}.get(layout["status"], "T3")


def _hist(lay: dict, what: str, run_id: str) -> list:
    return (lay.get("history") or [])[-30:] + [{"what": what, "run": run_id}]


def demote(layout_id: int, run_id: str, reason: str) -> str:
    lay = db.q1("SELECT * FROM layouts WHERE id=%s", (layout_id,))
    new = {"t1": "t2", "t2": "shadow", "shadow": "shadow"}[lay["status"]]
    db.q("""UPDATE layouts SET status=%s, agree_streak=0, demotions=demotions+1, history=%s, updated_at=now() WHERE id=%s""",
         (new, db.J(_hist(lay, f"demoted to {new}: {reason}", run_id)), layout_id))
    event(run_id, lay["vendor_id"], "demoted", layout_id=layout_id, to=new, reason=reason)
    return new


def learn_from_posted(run_id: str, vendor_id: str, line_map: dict, fields: dict, extraction, shadow: dict | None,
                      layout_id: int | None, tier: str, clean: bool, corrected: bool) -> list[str]:
    """Called after a run is posted to the ERP. Returns plain-language notes for the live view."""
    notes: list[str] = []
    if not clean:
        return ["Not learned from: run had payment-safety or channel findings"]
    from ..domain.models import Extraction

    ex = extraction if isinstance(extraction, Extraction) else Extraction(**extraction)
    if layout_id is None:
        tpl = L.learn_template(line_map, fields, ex)
        if len(tpl["fields"]) >= 4 and tpl.get("table"):
            r = db.q1("""INSERT INTO layouts (vendor_id, fingerprint, template, learned_from) VALUES (%s,%s,%s,%s) RETURNING id""",
                      (vendor_id, db.J(L.fingerprint(line_map)), db.J(tpl), [run_id]))
            event(run_id, vendor_id, "layout_learned", layout_id=r["id"], fields=len(tpl["fields"]))
            notes.append(f"New layout learned for this vendor ({len(tpl['fields'])} fields + line table), in shadow mode")
        return notes
    lay = db.q1("SELECT * FROM layouts WHERE id=%s", (layout_id,))
    if corrected:
        demote(layout_id, run_id, "a person corrected a field")
        return ["Layout demoted: a person corrected a field"]
    agreed = bool(shadow and shadow.get("agree")) or tier in ("T1", "T2")
    if not agreed:
        # Template disagreed with the verified full read: relearn from this run.
        tpl = L.learn_template(line_map, fields, ex)
        db.q("UPDATE layouts SET template=%s, agree_streak=0, version=version+1, learned_from=learned_from || %s, updated_at=now() WHERE id=%s",
             (db.J(tpl), [run_id], layout_id))
        event(run_id, vendor_id, "relearned", layout_id=layout_id)
        return [f"Template disagreed on {', '.join((shadow or {}).get('diffs', []))}; relearned (v{lay['version'] + 1})"]
    streak = lay["agree_streak"] + 1
    status = lay["status"]
    if status == "shadow" and streak >= L.T2_AFTER:
        status, streak = "t2", 0
    elif status == "t2" and streak >= L.T1_AFTER:
        status, streak = "t1", 0
    if status != lay["status"]:
        event(run_id, vendor_id, "promoted", layout_id=layout_id, to=status)
        notes.append(f"Layout promoted to {status.upper()} — next invoices from this layout read "
                     + ("with a template plus a cheap cross-check" if status == "t2" else "by template only (1 in 10 audited)"))
    else:
        need = (L.T2_AFTER if status == "shadow" else L.T1_AFTER) - streak
        if status != "t1":
            notes.append(f"Template agreed with the verified reading ({streak} in a row; {need} more to promote)")
    db.q("""UPDATE layouts SET status=%s, agree_streak=%s, uses=uses+1, history=%s, updated_at=now() WHERE id=%s""",
         (status, streak, db.J(_hist(lay, f"agreed ({status}, streak {streak})", run_id)), layout_id))
    return notes


def learn_alias(run_id: str, vendor_id: str, printed: str) -> str | None:
    if not printed:
        return None
    exists = db.q1("SELECT 1 FROM vendor_aliases WHERE vendor_id=%s AND alias=%s", (vendor_id, printed))
    if exists:
        return None
    db.q("INSERT INTO vendor_aliases (vendor_id, alias, source, learned_from) VALUES (%s,%s,'learned',%s)", (vendor_id, printed, run_id))
    event(run_id, vendor_id, "alias_learned", alias=printed)
    return f"Learned vendor alias '{printed}'"


def item_key(desc: str) -> str:
    return name_norm(desc)[:200]


def item_map(vendor_id: str) -> dict[str, str]:
    return {r["invoice_text_norm"]: r["sku"] for r in db.q("SELECT invoice_text_norm, sku FROM vendor_item_map WHERE vendor_id=%s", (vendor_id,))}


def learn_items(run_id: str, vendor_id: str, pairs: list[dict]) -> list[str]:
    notes = []
    for p in pairs:
        if p["method"] not in ("description", "human") or not p.get("po_sku"):
            continue
        k = item_key(p["description"])
        r = db.q("""INSERT INTO vendor_item_map (vendor_id, invoice_text_norm, sku, source, learned_from) VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING RETURNING sku""", (vendor_id, k, p["po_sku"], p["method"], run_id))
        if r:
            event(run_id, vendor_id, "item_learned", description=p["description"], sku=p["po_sku"])
            notes.append(f"Learned item: '{p['description']}' = {p['po_sku']}")
    return notes


def bump_item_use(vendor_id: str, k: str) -> None:
    db.q("UPDATE vendor_item_map SET uses=uses+1 WHERE vendor_id=%s AND invoice_text_norm=%s", (vendor_id, k))


# ---- judgment cache (L4)

def cache_key(state, question: str, backend_hint: str) -> str:
    return hashlib.sha256(f"{question}|{backend_hint}|{state}".encode()).hexdigest()[:32]


def cache_get(key: str) -> dict | None:
    r = db.q1("UPDATE judgment_cache SET hits=hits+1 WHERE key=%s RETURNING answer, backend", (key,))
    return r


def cache_put(key: str, question: str, backend: str, answer: dict) -> None:
    db.q("INSERT INTO judgment_cache (key, question, backend, answer) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
         (key, question, backend, db.J(answer)))


# ---- decision patterns -> suggested policy changes (L6)

def policy_suggestions(min_support: int = 5, min_deciders: int = 2) -> list[dict]:
    rows = db.q("""SELECT p.id, p.findings, p.outcome, d.action, d.actor, i.vendor_id, v.legal_name,
                          (SELECT f->>'rule_id' FROM jsonb_array_elements(p.findings) f WHERE f->>'status'='fail' LIMIT 1) primary_rule
                   FROM decisions d JOIN proposals p ON p.id=d.proposal_id JOIN invoices i ON i.id=p.invoice_id
                   JOIN vendors v ON v.id=i.vendor_id
                   WHERE p.outcome='REVIEW' AND d.action IN ('approve','reject','short_pay')""")
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["vendor_id"], r["primary_rule"])].append(r)
    out = []
    from ..policy.catalog import LOCKED

    for (vid, rule), rs in groups.items():
        if not rule or rule in LOCKED:
            continue
        approvals = [r for r in rs if r["action"] == "approve"]
        contrary = [r for r in rs if r["action"] != "approve"]
        deciders = {r["actor"] for r in approvals}
        status = "ready" if len(approvals) >= min_support and not contrary and len(deciders) >= min_deciders else "watching"
        out.append({"vendor_id": vid, "vendor": rs[0]["legal_name"], "rule_id": rule, "approvals": len(approvals),
                    "contrary": len(contrary), "deciders": sorted(deciders), "status": status,
                    "suggestion": f"{rs[0]['legal_name']}: {len(approvals)} of {len(rs)} '{rule}' reviews were approved. "
                                  f"Suggest a vendor-specific tolerance (teach the agent)."})
    return sorted(out, key=lambda s: (s["status"] != "ready", -s["approvals"]))
