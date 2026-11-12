"""process_invoice: the durable pipeline (§3). Every stage is a DBOS step and emits live events.

Intake → Pre-process → Read (tier T1/T2/T3) → Verify → Resolve vendor → Resolve PO → Pair → Match → Screen → Judge
→ Rules (ZEN) → [Investigate] → Decide → Explain → proposal. AUTO_APPROVE continues straight into apply_decision.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from dbos import DBOS

from .. import db, repo
from ..agent import investigator
from ..ai import decision_port as dp
from ..ai import gateway, narrator, offline_reader
from ..config import settings
from ..domain import decide as D
from ..domain import facts as F
from ..domain import match as M
from ..domain import resolve as R
from ..domain import screen as S
from ..domain import verify as V
from ..domain.models import Extraction, PAYMENT_CRITICAL
from ..domain.money import fmt_money, parse_amount, parse_date
from ..domain.normalise import invoice_number_norm, iban_norm
from ..ingest import preprocess as P
from ..learning import layouts as LL
from ..learning import memory as LM
from ..policy import engine as PE
from .events import Stage, emit

SLA_HOURS = {"AUTO_APPROVE": 0, "REVIEW": 24, "HOLD": 48, "REJECT": 24, "NOT_AN_INVOICE": 72, "APPLY_CREDIT": 48}


def _pace(C) -> int:
    return int(C.get("pace_ms") or settings().demo_pace_ms or 0)


def _add_cost(C: dict, rec) -> None:
    if rec is None:
        return
    C["cost_usd"] = round(C.get("cost_usd", 0) + (getattr(rec, "cost_usd", 0) or 0), 6)
    C.setdefault("calls", []).append({"kind": rec.kind, "model": rec.model, "source": rec.source, "ms": rec.ms,
                                      "cost_usd": rec.cost_usd, "usage": rec.usage})


# ------------------------------------------------------------------ stages

@DBOS.step()
def s_intake(run_id: str) -> dict:
    run = db.q1("SELECT * FROM runs WHERE id=%s", (run_id,))
    doc = db.q1("SELECT * FROM documents WHERE id=%s", (run["document_id"],))
    C = {"run_id": run_id, "document_id": doc["id"], "invoice_id": run["invoice_id"], "pinned": run["pinned_fields"] or {},
         "trigger": run["trigger"], "parent_run_id": run["parent_run_id"], "cost_usd": 0.0, "attempted": [],
         "pace_ms": (run["model_versions"] or {}).get("pace_ms", 0),
         "doc": {k: doc[k] for k in ("filename", "mime", "storage_path", "channel", "email_from", "email_reply_to",
                                     "email_subject", "email_body", "scenario", "sha256")}}
    with Stage(run_id, "intake", _pace(C)) as st:
        kind = {"application/pdf": "PDF", "image/jpeg": "Photo (JPEG)", "image/png": "Image (PNG)"}.get(doc["mime"], doc["mime"])
        bits = [f"{kind}, {doc['filename']}"]
        if doc["email_from"]:
            bits.append(f"emailed from {doc['email_from']}")
        if C["trigger"] != "ingest":
            bits.append(f"re-run ({C['trigger'].replace('_', ' ')})")
        st.done("pass", " · ".join(bits), channel=doc["channel"], sha256=doc["sha256"][:12])
    return C


@DBOS.step()
def s_preprocess(C: dict) -> dict:
    with Stage(C["run_id"], "preprocess", _pace(C)) as st:
        path = Path(C["doc"]["storage_path"])
        doc = db.q1("SELECT line_map FROM documents WHERE id=%s", (C["document_id"],))
        lm = doc["line_map"]
        if not lm:
            lm = P.build_pdf_line_map(path) if C["doc"]["mime"] == "application/pdf" else P.build_image_line_map(path)
            db.q("UPDATE documents SET line_map=%s, pages=%s, text_layer=%s WHERE id=%s",
                 (db.J(lm), len(lm["pages"]), lm["text_layer"], C["document_id"]))
        C["text_layer"] = lm["text_layer"]
        src = "Text layer" if lm["text_layer"] else "No text layer: OCR"
        st.done("pass", f"{src} · {len(lm['pages'])} page(s) · {len(lm['lines'])} text lines located",
                source=lm["source"], lines=len(lm["lines"]))
    C["line_map"] = lm
    return C


def _full_read(C: dict) -> tuple[Extraction, dict | None, list[str]]:
    """Read A ∥ Read B, degrading to the offline parser (P9)."""
    path, mime = Path(C["doc"]["storage_path"]), C["doc"]["mime"]
    notes = []
    from ..ai.read_a import read_a
    from ..ai.read_b import read_b

    with cf.ThreadPoolExecutor(2) as ex:
        fa = ex.submit(read_a, path, mime)
        fb = ex.submit(read_b, C["line_map"]) if C["line_map"]["lines"] else None
        try:
            a, ra = fa.result()
            _add_cost(C, ra)
            notes.append(f"Read A: {ra.model} ({'replayed' if ra.source == 'fixture' else f'{ra.ms / 1000:.1f} s'})")
        except gateway.NoBackend as e:
            a = offline_reader.parse(C["line_map"])
            notes.append(f"Read A: offline text parser ({e})")
            C["degraded_read"] = True
        except Exception as e:  # noqa: BLE001
            a = offline_reader.parse(C["line_map"])
            notes.append(f"Read A failed ({type(e).__name__}); offline text parser")
            C["degraded_read"] = True
        b = None
        if fb:
            try:
                b, rb = fb.result()
                _add_cost(C, rb)
                notes.append(f"Read B: {rb.model}, cited to lines")
            except Exception as e:  # noqa: BLE001
                notes.append(f"Read B unavailable ({type(e).__name__})")
    return a, b, notes


@DBOS.step()
def s_read(C: dict) -> dict:
    with Stage(C["run_id"], "read", _pace(C)) as st:
        lm = C["line_map"]
        layout, score = (LL.best_layout(lm, LM.all_layouts()) if lm["text_layer"] else (None, 0.0))
        tier = LM.tier_for(layout)
        if not lm["text_layer"] and layout:
            tier = "T3"  # scans and photos never read by template alone
        audit = tier == "T1" and int(hashlib.sha256(C["run_id"].encode()).hexdigest(), 16) % LL.AUDIT_EVERY == 0
        C["layout_id"] = layout["id"] if layout else None
        C["layout_vendor"] = layout["vendor_id"] if layout else None
        C["tier"] = tier
        a = b = None
        if tier in ("T1", "T2"):
            a, misses = LL.apply_template(lm, layout["template"])
            if a is None or misses:
                C["escalate"] = f"template could not find {', '.join(misses) or 'fields'}"
            else:
                C["template_read"] = True
                if tier == "T2" or audit:
                    from ..ai.read_b import read_b

                    try:
                        b, rb = read_b(lm)
                        _add_cost(C, rb)
                    except Exception:  # noqa: BLE001
                        b = None
                        C["escalate"] = "cross-check unavailable"
        if tier == "T3" or C.get("escalate"):
            a, b, notes = _full_read(C)
            if C.get("escalate"):
                C["attempted"].append(f"Known layout, but {C['escalate']}: full read instead")
                LM.demote(layout["id"], C["run_id"], C["escalate"])
                C["tier"] = "T3"
            C["read_notes"] = notes
            if layout and layout["status"] == "shadow":
                C["shadow_template"] = layout["template"]
        C["extraction"] = a.model_dump()
        C["read_b"] = b
        n_fields = sum(1 for k, v in C["extraction"].items() if v and k not in ("lines", "quotes", "document_type"))
        cost = fmt_money(Decimal(str(C["cost_usd"])), "USD")
        if C["tier"] in ("T1", "T2") and not C.get("escalate"):
            what = "template only" if C["tier"] == "T1" and not audit else "template + cross-check" + (" (audit sample)" if audit else "")
            st.done("pass", f"Known layout ({C['tier']}, v{layout['version']}) · {what} · {n_fields} fields, {len(a.lines)} lines · {cost}",
                    tier=C["tier"], layout_id=layout["id"], similarity=round(score, 2), cost_usd=C["cost_usd"])
        else:
            status = "degraded" if C.get("degraded_read") else "pass"
            st.done(status, f"Full read · {n_fields} fields, {len(a.lines)} lines · {cost} · " + " · ".join(C.get("read_notes", [])),
                    tier="T3", calls=C.get("calls", []), cost_usd=C["cost_usd"],
                    layout_hint=(f"shadow template v{layout['version']}" if layout else None))
    db.q("UPDATE runs SET read_tier=%s, layout_id=%s WHERE id=%s", (C["tier"], C.get("layout_id"), C["run_id"]))
    return C


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _alnum(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _render_ok(field: str, value: str, seen: str) -> bool:
    """Visible pixels (OCR) vs text-layer value. Tolerates OCR separator noise, never a changed digit."""
    from rapidfuzz.distance import Levenshtein

    if field in ("total", "subtotal"):
        v, o = _digits(value), _digits(seen)
        if v and v in o:
            return True
        # a thousands separator OCR'd as "1"/"7": one inserted character, all real digits in order
        return len(o) == len(v) + 1 and any(o[:i] + o[i + 1:] == v for i in range(len(o)))
    v, o = _alnum(value), _alnum(seen).replace("O", "0")
    v = v.replace("O", "0")
    return v in o or (len(v) >= 12 and Levenshtein.distance(v, o[-len(v):] if len(o) >= len(v) else o) <= 2)


def _render_check(C: dict, fields: dict) -> dict:
    """DOC-02: OCR the rendered page region of payment-critical fields and compare it with the text layer.
    If the two readings disagree on a payment field, check where the text-layer value sits: if the pixels there
    show the *other* reading, the visible document and its hidden text layer say different things."""
    render = {"matches": None, "checked": []}
    path = Path(C["doc"]["storage_path"])
    for f in PAYMENT_CRITICAL:
        stf = fields.get(f)
        if not stf or not stf.value:
            continue
        a, b = stf.a, stf.b
        target, loc = stf.value, (stf.page, stf.bbox) if stf.located else (None, None)
        if stf.agree is False and b:
            line, bb = V.locate(str(b), C["line_map"]["lines"])
            if line is not None:
                target, loc = b, (line["page"], bb)
        if not loc[1]:
            continue
        seen = P.ocr_near(path, loc[0] or 1, loc[1])
        ok = _render_ok(f, str(target), seen)
        shows_other = bool(stf.agree is False and a and _render_ok(f, str(a), seen))
        render["checked"].append({"field": f, "ok": ok and not shows_other, "text_layer": str(target)[:40], "seen": seen[:60]})
        if (not ok or shows_other) and f in ("remit_iban", "remit_account_number", "total"):
            render.update(matches=False, field=f.replace("remit_", "").replace("_", " "))
            if shows_other:
                stf.reasons.append(f"render check: the page shows '{a}' but the hidden text layer says '{target}'")
                stf.failed_validation = True
    if render["matches"] is None and render["checked"]:
        render["matches"] = True
    return render


@DBOS.step()
def s_verify(C: dict) -> dict:
    with Stage(C["run_id"], "verify", _pace(C)) as st:
        ex = Extraction(**C["extraction"])
        fields = V.compare_readings(ex, C.get("read_b"), C["line_map"], C["pinned"])
        rec = V.reconcile(fields, ex)
        render = {"matches": None, "checked": []}
        if C["doc"]["mime"] == "application/pdf" and C["text_layer"] and ex.document_type == "invoice":
            try:
                render = _render_check(C, fields)
            except Exception as e:  # noqa: BLE001
                render["error"] = type(e).__name__
        # Shadow comparison for learning: would the template have read the same thing?
        if C.get("shadow_template"):
            tex, misses = LL.apply_template(C["line_map"], C["shadow_template"])
            if tex is not None:
                ok, diffs = LL.agrees(tex, {k: v.to_dict() for k, v in fields.items()})
                C["shadow"] = {"agree": ok and not misses, "diffs": diffs + misses}
        # Template reads must still reconcile; otherwise escalate inside the same run (§16.2).
        if C.get("template_read") and (not rec.reconciles or any(not fields[f].located for f in ("invoice_number", "total") if fields[f].value)):
            C["needs_escalation"] = "template reading did not reconcile"
        C["fields"] = {k: v.to_dict() for k, v in fields.items()}
        C["reconciliation"] = {"reconciles": rec.reconciles, "delta": str(rec.delta), "mode": rec.tax_mode_used,
                               "lines_sum": str(rec.lines_sum), "chosen": rec.chosen}
        C["render"] = render
        counts = {"verified": 0, "read": 0, "uncertain": 0}
        for v in C["fields"].values():
            if v["evidence"] in counts:
                counts[v["evidence"]] += 1
        unc = [k for k, v in C["fields"].items() if v["evidence"] == "uncertain"]
        parts = [f"{counts['verified']} verified · {counts['read']} read · {counts['uncertain']} uncertain",
                 "adds up" + (f" ({rec.tax_mode_used} tax)" if rec.tax_mode_used else "") if rec.reconciles else f"does not add up (off by {rec.delta})"]
        if render["matches"] is False:
            parts.append(f"visible {render['field']} ≠ text layer")
        elif render["matches"]:
            parts.append(f"render check OK on {len(render['checked'])} payment fields")
        for k, v in C["fields"].items():
            for r in v["reasons"]:
                if r.startswith("attempted resolution"):
                    C["attempted"].append(f"{k.replace('_', ' ').title()}: {r.split(': ', 1)[1]}")
        status = "block" if render["matches"] is False else ("warn" if unc or not rec.reconciles else "pass")
        st.done(status, " · ".join(parts), fields=C["fields"], reconciliation=C["reconciliation"], render=render,
                uncertain=unc, shadow=C.get("shadow"))
    return C


@DBOS.step()
def s_escalate(C: dict) -> dict:
    """Template reading failed a check: full read now, within this run, and demote the layout."""
    with Stage(C["run_id"], "read", _pace(C)) as st:
        LM.demote(C["layout_id"], C["run_id"], C["needs_escalation"])
        C["attempted"].append(f"Known layout, but {C['needs_escalation']}: full read instead")
        a, b, notes = _full_read(C)
        C["extraction"], C["read_b"], C["tier"] = a.model_dump(), b, "T3"
        C.pop("template_read", None)
        C.pop("needs_escalation", None)
        st.done("warn", "Template check failed → full read · " + " · ".join(notes), tier="T3")
    db.q("UPDATE runs SET read_tier='T3' WHERE id=%s", (C["run_id"],))
    return C


@DBOS.step()
def s_resolve_vendor(C: dict) -> dict:
    with Stage(C["run_id"], "resolve_vendor", _pace(C)) as st:
        f = C["fields"]
        snap = repo.master_snapshot(None)
        res = R.resolve_vendor(f["vendor_name"]["value"], f["vendor_tax_id"]["value"], f["remit_iban"]["value"],
                               snap["vendors"], snap["banks"], snap["aliases"])
        jev = None
        if res.needs_judgment:
            printed, cand = res.needs_judgment
            ans = dp.ask({"a": printed, "b": cand}, dp.q_same_entity(), kind="same_entity", cacheable=True)
            p = ans.answers["same_entity"]["p"]
            jev = {"p": p, "backend": ans.backend}
            if p >= 0.9:
                res = R.VendorResolution(res.candidates[0]["vendor_id"], "jev", res.candidates, note=f"same company (p={p:.2f})")
        vrow = next((v for v in snap["vendors"] if v["id"] == res.vendor_id), None)
        C["vendor"] = {"vendor_id": res.vendor_id, "method": res.method, "note": res.note, "candidates": res.candidates,
                       "row": vrow, "jev": jev}
        if vrow:
            if res.method in ("tax_id", "bank", "alias"):
                vf = C["fields"]["vendor_name"]
                vf["reasons"].append(f"confirmed: vendor master ({res.method.replace('_', ' ')} → {vrow['legal_name']})")
                if vf["evidence"] != "verified" and vf.get("value"):
                    vf["evidence"] = "verified"
            st.done("pass" if res.method in ("tax_id", "bank", "alias") else "warn",
                    f"{vrow['legal_name']} ({vrow['id']}) · matched by {res.method.replace('_', ' ')}"
                    + (f" · status {vrow['status']}" if vrow["status"] != "approved" else ""), vendor=C["vendor"])
        else:
            st.done("block", f"'{f['vendor_name']['value']}' is not in the vendor master", vendor=C["vendor"])
    return C


@DBOS.step()
def s_resolve_po(C: dict) -> dict:
    with Stage(C["run_id"], "resolve_po", _pace(C)) as st:
        vid = C["vendor"]["vendor_id"]
        snap = repo.master_snapshot(vid)
        C["snapshot"] = snap
        ex = C["extraction"]
        printed = C["pinned"].get("po_number") or C["fields"]["po_number"]["value"]
        inv_lines = [{"description": l["description"], "sku": l.get("sku")} for l in ex["lines"]]
        all_pos = db.q("SELECT id, vendor_id, currency, status, total FROM purchase_orders")
        res = R.resolve_po(printed, vid, all_pos, snap.get("po_lines", []), inv_lines) if vid else R.PoResolution(None, "none", printed)
        if C["pinned"].get("po_number") and res.status == "explicit" and C["pinned"].get("po_source") == "agent":
            res.status = "inferred"
        open_count = sum(1 for p in snap.get("pos", []) if p["vendor_id"] == vid and p["status"] == "open")
        po_row = next((p for p in snap.get("pos", []) if p["id"] == res.po_id), None)
        C["po"] = {"po_id": res.po_id, "status": res.status, "printed": printed, "candidates": res.candidates, "row": po_row,
                   "open_po_count": open_count, "note": res.note}
        if res.status in ("explicit", "inferred") and po_row:
            cf = C["fields"]["currency"]
            if cf.get("value") and cf["value"] == po_row.get("currency") and cf["evidence"] != "verified":
                # §4: a value confirmed by master data is Verified (the PO is in this currency).
                cf["evidence"] = "verified"
                cf["reasons"].append(f"confirmed: matches the PO currency ({po_row['currency']})")
            elif not cf.get("value"):
                cf.update(value=po_row.get("currency"), evidence="verified",
                          reasons=cf["reasons"] + [f"not printed; taken from the PO ({po_row.get('currency')})"])
            if res.status == "explicit":
                C["fields"]["po_number"]["reasons"].append("confirmed: PO exists for this vendor")
                if C["fields"]["po_number"]["evidence"] == "read":
                    C["fields"]["po_number"]["evidence"] = "verified"
            st.done("pass" if res.status == "explicit" else "warn",
                    f"{res.po_id} · {res.note} · PO total {fmt_money(Decimal(str(po_row['total'])), po_row['currency'])}", po=C["po"])
        elif C["extraction"]["document_type"] != "invoice":
            st.done("skipped", f"Not an invoice ({C['extraction']['document_type'].replace('_', ' ')})", po=C["po"])
        else:
            st.done("warn" if res.status == "none" else "block",
                    {"none": f"No PO printed · {res.note}", "not_found": f"PO {printed} not found",
                     "other_vendor": f"{printed} belongs to another vendor", "closed": f"{res.po_id} is closed"}.get(res.status, res.status),
                    po=C["po"])
    return C


@DBOS.step()
def s_pair(C: dict) -> dict:
    with Stage(C["run_id"], "pair", _pace(C)) as st:
        ex = C["extraction"]
        vid = C["vendor"]["vendor_id"]
        inv_lines = []
        cached = 0
        for k, l in enumerate(ex["lines"]):
            ans = dp.ask(dp.redact(l["description"]), dp.q_line_kind(), kind="line_kind", cacheable=True)
            cached += ans.cached
            kind = ans.answers["line_kind"]["label"] or "goods"
            inv_lines.append({"idx": k, "description": l["description"], "sku": l.get("sku"), "line_kind": kind,
                              "qty": l.get("qty"), "unit_price": l.get("unit_price"), "amount": l.get("amount")})
        po_lines = [pl for pl in C["snapshot"].get("po_lines", []) if pl["po_id"] == C["po"]["po_id"]]
        known = LM.item_map(vid) if vid else {}
        by_sku = {pl["sku"]: pl for pl in po_lines}
        pre = {}
        for l in inv_lines:
            k = LM.item_key(l["description"])
            if not l.get("sku") and k in known and known[k] in by_sku:
                pre[l["idx"]] = {"po_line_id": by_sku[known[k]]["id"], "method": "item_map", "p": 1.0}
                LM.bump_item_use(vid, k)

        def same_item(a, b):
            return dp.ask({"a": dp.redact(a), "b": dp.redact(b)}, dp.q_same_item(), kind="same_item", cacheable=True).answers["same_item"]["p"]

        pairing = M.pair_lines([l for l in inv_lines if l["idx"] not in pre], po_lines, same_item) if po_lines else M.Pairing()
        pairing.pairs.update(pre)
        C["inv_lines"] = inv_lines
        C["pairing"] = {"pairs": {str(k): v for k, v in pairing.pairs.items()}, "vetoed": pairing.vetoed, "asked": pairing.asked}
        goods = [l for l in inv_lines if l["line_kind"] in ("goods", "service")]
        other = len(inv_lines) - len(goods)
        by = {}
        for v in pairing.pairs.values():
            by[v["method"]] = by.get(v["method"], 0) + 1
        how = ", ".join(f"{n} by {m.replace('_', ' ')}" for m, n in by.items()) or "none"
        unp = len(goods) - len([l for l in goods if l["idx"] in pairing.pairs])
        st.done("pass" if not unp else "warn",
                f"{len(pairing.pairs)} of {len(goods)} lines matched to {C['po']['po_id'] or 'a PO'} ({how})"
                + (f" · {other} non-goods line(s)" if other else "") + (f" · {len(pairing.vetoed)} spec veto(es)" if pairing.vetoed else "")
                + (f" · {cached} judgments from memory" if cached else ""),
                pairs=C["pairing"], lines=inv_lines)
    return C


@DBOS.step()
def s_match(C: dict) -> dict:
    with Stage(C["run_id"], "match", _pace(C)) as st:
        snap = C["snapshot"]
        per_line, per_po, own_paid = repo.billed_before(snap, C["invoice_id"])
        received = {k: Decimal(str(v)) for k, v in snap.get("received", {}).items()}
        po_lines = {pl["id"]: pl for pl in snap.get("po_lines", [])}
        pairing = M.Pairing(pairs={int(k): v for k, v in C["pairing"]["pairs"].items()})
        rate = parse_amount(C["extraction"].get("tax_rate"))
        incl = rate if C["reconciliation"].get("mode") == "inclusive" and rate else None
        lines = M.line_measures(C["inv_lines"], pairing, po_lines, received, per_line, incl)
        for l in lines:
            if l.get("po_line_id"):
                l["po_sku"] = po_lines[l["po_line_id"]]["sku"]
        C["lines"] = lines
        C["po_billed_before"] = str(per_po.get(C["po"]["po_id"], Decimal("0"))) if C["po"]["po_id"] else "0"
        C["own_paid"] = str(own_paid)
        bits = []
        for l in lines:
            if not l.get("paired"):
                continue
            pd = l["price_delta_pct"]
            bits.append(f"{(l.get('sku') or l['description'][:18])}: billed {l['qty_billed']:g}, received {l['qty_received_net']:g}"
                        + (f", price {pd:+.1%}" if pd else ""))
        st.done("pass", " · ".join(bits[:4]) or "No PO lines to match against", lines=lines,
                po_billed_before=C["po_billed_before"], own_paid=C["own_paid"])
    return C


@DBOS.step()
def s_screen(C: dict) -> dict:
    with Stage(C["run_id"], "screen", _pace(C)) as st:
        f, snap, vid = C["fields"], C["snapshot"], C["vendor"]["vendor_id"]
        num = C["pinned"].get("invoice_number") or f["invoice_number"]["value"]
        total = parse_amount(f["total"]["value"])
        inv_date, _ = parse_date(f["invoice_date"]["value"])
        dup = S.duplicates(vid, invoice_number_norm(num), total, inv_date, snap.get("related_vendor_ids", [vid]),
                           [{**h, "total": h["total"]} for h in snap.get("history", [])], C["invoice_id"],
                           bool((C["po"].get("row") or {}).get("blanket")), C["extraction"].get("service_period"))
        banks = [b for b in snap["banks"] if b["vendor_id"] == vid]
        pay = S.payment_safety(iban_norm(f["remit_iban"]["value"]), f["remit_account_number"]["value"], banks, C["vendor"]["row"],
                               C["doc"]["email_from"], C["doc"]["email_reply_to"], snap.get("paid_before", 0))
        # A bank account verified by call-back resets the bank-change clock for PAY-04.
        if pay["days_since_bank_change"] is not None:
            last = max(banks, key=lambda b: b["added_at"])
            if (last.get("verified_by") or "").startswith("callback:"):
                pay["days_since_bank_change"] = None
        C["screen"] = {**dup, **pay}
        bits = []
        bits.append(f"Duplicate of {dup['duplicate_of']}" if dup["hard_duplicate"] else "No duplicate")
        if pay["bank_present"]:
            bits.append("bank matches verified account" if pay["bank_matches_verified"] else
                        f"bank {pay['invoice_bank_masked']} NOT on file (verified: {pay['verified_bank_masked']})")
        if pay["sender_domain_distance"]:
            bits.append(f"sender {pay['sender_domain']} is {pay['sender_domain_distance']} edit(s) from {pay['vendor_domain']}")
        if pay["reply_to_mismatch"]:
            bits.append("Reply-To differs from sender")
        bad = dup["hard_duplicate"] or pay["bank_matches_verified"] is False or (pay["sender_domain_distance"] or 0) > 0
        st.done("block" if bad else "pass", " · ".join(bits), screen=C["screen"])
    return C


@DBOS.step()
def s_judge(C: dict) -> dict:
    with Stage(C["run_id"], "judge", _pace(C)) as st:
        d = C["doc"]
        text = "\n".join(x for x in [d.get("email_subject"), d.get("email_body"), C["extraction"].get("notes")] if x)
        out = {}
        backends = set()
        if text.strip():
            red = dp.redact(text)
            a = dp.ask(red, dp.Q_JUDGE, kind="judge")
            out.update(a.answers)
            backends.add(a.backend)
        doc_text = dp.redact(P.line_map_text(C["line_map"])[:6000])
        inj = dp.ask(doc_text + ("\n" + dp.redact(text) if text else ""), dp.Q_INJECTION, kind="injection")
        out.update(inj.answers)
        backends.add(inj.backend)
        C["judge"] = out
        C["judge_backend"] = sorted(backends)
        words = []
        if "requests_bank_change" in out:
            p = out["requests_bank_change"]["p"]
            words.append("Asks to change bank details: " + ("very likely" if p >= 0.9 else "likely" if p >= 0.5 else "no"))
        if "pressure" in out:
            words.append("Pressure: " + out["pressure"]["label"])
        p = out["instructs_automation"]["p"]
        words.append("Text aimed at automated systems: " + ("yes" if p >= 0.5 else "no"))
        degraded = any(b != "jev" for b in backends)
        flagged = p >= 0.5 or out.get("requests_bank_change", {}).get("p", 0) >= 0.5 or out.get("pressure", {}).get("level", 0) >= 2
        st.done("block" if flagged else ("degraded" if degraded and "heuristic" in backends else "pass"),
                " · ".join(words) + f" · via {'/'.join(sorted(backends))}", answers=out, backends=sorted(backends),
                redacted=bool(text))
    return C


@DBOS.step()
def s_rules(C: dict) -> dict:
    with Stage(C["run_id"], "rules", _pace(C)) as st:
        C["doc_type"] = C["extraction"]["document_type"]
        facts, ctx = F.build(C)
        pol = db.q1("SELECT version, jdm FROM policies WHERE status='active' ORDER BY created_at DESC LIMIT 1")
        findings, applied, trace, line_checks = PE.evaluate(pol["jdm"], facts)
        C["facts"], C["ctx"], C["policy_version"] = facts, ctx, pol["version"]
        C["findings"] = [f.model_dump() for f in findings]
        C["applied"], C["line_checks"], C["zen_trace"] = applied, line_checks, trace
        fails = [f for f in findings if f.status == "fail"]
        blocks = [f for f in fails if f.outcome in ("HOLD", "REJECT")]
        st.done("block" if blocks else ("warn" if fails else "pass"),
                f"{len(findings)} checks: {len(findings) - len(fails)} pass, {len(fails)} fail (policy {pol['version']})"
                + (" · " + ", ".join(f.rule_id for f in fails) if fails else ""),
                findings=[{**f.model_dump(), "message": D.render(f.message, ctx)} for f in findings], applied=applied)
    db.q("UPDATE runs SET policy_version=%s WHERE id=%s", (C["policy_version"], C["run_id"]))
    return C


@DBOS.step()
def s_investigate(C: dict) -> dict:
    from ..domain.models import Finding

    fails = [f for f in C["findings"] if f["status"] == "fail"]
    worst = D.decide([Finding(**f) for f in C["findings"]], C["facts"], C["lines"], C["line_checks"], [], C["ctx"], [])
    targets = [f for f in fails if f["rule_id"] in investigator.INVESTIGABLE]
    if worst["outcome"] == "AUTO_APPROVE" or not targets or not C["vendor"]["vendor_id"] or C["doc_type"] != "invoice":
        return C
    with Stage(C["run_id"], "investigate", _pace(C)) as st:
        msgs = [{**f, "message": D.render(f["message"], C["ctx"])} for f in targets]

        def on_step(s):
            emit(C["run_id"], "investigate", "agent.step", "running", s["summary"], s)

        facts = {**C["facts"], "vendor_legal_name": C["ctx"]["vendor_legal_name"], "currency": C["ctx"]["currency"],
                 "invoice_number": C["ctx"]["invoice_number"]}
        res = investigator.investigate(facts, msgs, on_step)
        recheck = None
        prop = res.get("proposed_resolution") or {}
        if prop.get("type") == "link_po" and prop.get("po_id") and C["po"]["status"] in ("none", "not_found"):
            po = db.q1("SELECT id, vendor_id, status FROM purchase_orders WHERE id=%s", (prop["po_id"],))
            ok = bool(po and po["vendor_id"] == C["vendor"]["vendor_id"] and po["status"] == "open")
            recheck = {"po_id": prop["po_id"], "ok": ok}
            if ok:
                C["agent_po"] = prop["po_id"]
        C["investigation"] = {**res, "recheck": recheck}
        for h in res.get("hypotheses", [])[:3]:
            C["attempted"].append(h)
        db.q("""INSERT INTO investigations (run_id, backend, steps, hypotheses, proposed_resolution, question, recheck_result)
                VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (run_id) DO NOTHING""",
             (C["run_id"], res["backend"], db.J(res["steps"]), db.J(res.get("hypotheses", [])),
              (prop or {}).get("po_id"), res.get("question"), db.J(recheck)))
        _add_cost(C, None)
        C["cost_usd"] = round(C["cost_usd"] + float(res.get("cost_usd") or 0), 6)
        st.done("pass", f"{len(res['steps'])} tool call(s) · " + ("; ".join(res.get("hypotheses", [])[:2]) or "nothing conclusive")
                + (f" · re-check: {prop['po_id']} {'confirmed' if recheck and recheck['ok'] else 'rejected'}" if recheck else ""),
                investigation=C["investigation"])
    return C


@DBOS.step()
def s_rematch_with_agent_po(C: dict) -> dict:
    """Deterministic re-check of the agent's PO: re-run resolve → pair → match → screen → rules with it pinned (capped)."""
    C["pinned"] = {**C["pinned"], "po_number": C.pop("agent_po"), "po_source": "agent"}
    return C


@DBOS.step()
def s_decide(C: dict) -> dict:
    from ..domain.models import Finding

    with Stage(C["run_id"], "decide", _pace(C)) as st:
        caps = []
        if C["vendor"]["method"] in ("fuzzy", "jev"):
            caps.append({"reason": "vendor matched by name only", "action": "confirm_vendor", "owner": "ap_clerk"})
        if C["po"]["status"] == "inferred":
            caps.append({"reason": "PO was inferred, not printed", "action": "confirm_po", "owner": "ap_clerk"})
        if C.get("investigation") and C["investigation"].get("recheck"):
            caps.append({"reason": "agent proposed the resolution", "action": "confirm_po", "owner": "ap_clerk"})
        if C.get("degraded_read") and settings().ai_mode != "offline":
            pass
        p = D.decide([Finding(**f) for f in C["findings"]], C["facts"], C["lines"], C["line_checks"], caps, C["ctx"], C["attempted"])
        inv_q = (C.get("investigation") or {}).get("question")
        # The agent's question is used only when no deterministic question exists (no PO could be confirmed).
        if inv_q and p["outcome"] == "REVIEW" and p["action_code"] == "link_po":
            p["question"] = inv_q
        if p["action_code"] == "confirm_po" and C["po"]["status"] == "inferred":
            n = sum(1 for l in C["lines"] if l.get("paired"))
            p["question"] = (f"Confirm {C['po']['po_id']}? No PO was printed; it is the only open PO that matches all {n} lines "
                             f"(prices and quantities checked{' net of VAT' if C['facts'].get('tax_inclusive') else ''}).")
        due = D.terms(parse_date(C["fields"]["invoice_date"]["value"])[0], C["fields"]["due_date"]["value"],
                      C["fields"]["payment_terms"]["value"])
        p["terms"] = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in due.items()}
        C["proposal"] = p
        badge = f"Owed: {p['owed']} · Safe to pay: {p['safe_to_pay']}"
        st.done({"AUTO_APPROVE": "pass", "REVIEW": "warn", "HOLD": "block", "REJECT": "block"}.get(p["outcome"], "warn"),
                f"{p['outcome'].replace('_', ' ')}" + (f" · {p['sub_status'].replace('_', ' ')}" if p["sub_status"] else "")
                + f" · {badge}" + (f" · owner: {p['owner_role'].replace('_', ' ')}" if p["owner_role"] else ""),
                proposal={k: v for k, v in p.items() if k != "findings"})
    return C


@DBOS.step()
def s_explain(C: dict) -> dict:
    with Stage(C["run_id"], "explain", _pace(C)) as st:
        p = C["proposal"]
        text, src = narrator.narrate(p, C["ctx"])
        p["narrative"] = text
        p["vendor_email"] = narrator.vendor_email(p, C["ctx"])
        st.done("pass", f"Summary written ({src})" + (" · vendor email drafted" if p["vendor_email"] else ""), narrative=text)
    return C


@DBOS.step()
def s_propose(C: dict) -> dict:
    p, f = C["proposal"], C["fields"]
    with db.tx() as c:
        num = C["pinned"].get("invoice_number") or f["invoice_number"]["value"]
        inv_date, _ = parse_date(f["invoice_date"]["value"])
        vals = (C["doc_type"], num, invoice_number_norm(num), C["vendor"]["vendor_id"], C["po"]["po_id"], C["ctx"]["currency"],
                inv_date, parse_amount(f["subtotal"]["value"]), parse_amount(f["tax"]["value"]), parse_amount(f["freight"]["value"]),
                parse_amount(f["total"]["value"]))
        state = {"AUTO_APPROVE": "approving", "REJECT": "in_review", "NOT_AN_INVOICE": "in_review"}.get(p["outcome"], "in_review")
        if C["invoice_id"]:
            c.execute("""UPDATE invoices SET document_type=%s, invoice_number=%s, invoice_number_norm=%s, vendor_id=%s, po_id=%s,
                         currency=%s, invoice_date=%s, subtotal=%s, tax=%s, freight=%s, total=%s, state=%s WHERE id=%s""",
                      (*vals, state, C["invoice_id"]))
        else:
            C["invoice_id"] = c.execute("""INSERT INTO invoices (document_type, invoice_number, invoice_number_norm, vendor_id, po_id,
                         currency, invoice_date, subtotal, tax, freight, total, state, document_id)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                                        (*vals, state, C["document_id"])).fetchone()["id"]
            c.execute("UPDATE runs SET invoice_id=%s WHERE id=%s", (C["invoice_id"], C["run_id"]))
        for k, v in f.items():
            c.execute("""INSERT INTO invoice_fields (run_id, field, value, evidence, reasons, readings, page, bbox, set_by)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                      (C["run_id"], k, v["value"], v["evidence"], v["reasons"], db.J(v["readings"]), v["page"], db.J(v["bbox"]), v["set_by"]))
        c.execute("INSERT INTO facts (run_id, extraction, facts, master_snapshot) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                  (C["run_id"], db.J({"extraction": C["extraction"], "fields": f, "inv_lines": C["inv_lines"], "pairing": C["pairing"],
                                      "screen": C["screen"], "judge": C["judge"], "vendor": C["vendor"], "po": C["po"],
                                      "reconciliation": C["reconciliation"], "render": C["render"], "ctx": C["ctx"],
                                      "line_checks": C["line_checks"], "applied": C["applied"], "tier": C["tier"],
                                      "shadow": C.get("shadow"), "layout_id": C.get("layout_id")}),
                   db.J(C["facts"]), db.J(C["snapshot"])))
        prev = c.execute("SELECT id FROM proposals WHERE invoice_id=%s AND superseded_by IS NULL", (C["invoice_id"],)).fetchall()
        pid = c.execute("""INSERT INTO proposals (run_id, invoice_id, outcome, sub_status, owed, safe_to_pay, recommended_action, owner_role,
                    question, attempted, amount_payable, amount_held, gl_coding, narrative, vendor_email, findings, zen_trace)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (C["run_id"], C["invoice_id"], p["outcome"], p["sub_status"], p["owed"], p["safe_to_pay"], p["recommended_action"],
                         p["owner_role"], p["question"], db.J(p["attempted"]), p["amount_payable"], p["amount_held"], db.J(p["gl_coding"]),
                         p["narrative"], p["vendor_email"], db.J({"findings": p["findings"], "caps": p["caps"], "terms": p["terms"],
                                                                     "action_code": p["action_code"], "primary_rule": p["primary_rule"]}),
                         db.J(C["zen_trace"]))).fetchone()["id"]
        for r in prev:
            c.execute("UPDATE proposals SET superseded_by=%s WHERE id=%s", (pid, r["id"]))
        status = "completed" if p["outcome"] == "AUTO_APPROVE" else "awaiting_human"
        sla = None if status == "completed" else datetime.now(timezone.utc) + timedelta(hours=SLA_HOURS.get(p["outcome"], 24))
        c.execute("""UPDATE runs SET status=%s, sla_due_at=%s, cost_usd=%s, decision_backend=%s,
                     model_versions = model_versions || %s WHERE id=%s""",
                  (status if status != "completed" else "running", sla, C["cost_usd"], ",".join(C.get("judge_backend", [])),
                   db.J({"calls": C.get("calls", []), "tier": C["tier"]}), C["run_id"]))
    C["proposal_id"] = pid
    emit(C["run_id"], "decide", "proposal", p["outcome"], p["question"] or p["recommended_action"],
         {"proposal_id": pid, "outcome": p["outcome"], "amount_payable": str(p["amount_payable"]), "amount_held": str(p["amount_held"])})
    return C


# ------------------------------------------------------------------ the workflow

@DBOS.workflow()
def process_invoice(run_id: str) -> dict:
    try:
        C = s_intake(run_id)
        C = s_preprocess(C)
        C = s_read(C)
        C = s_verify(C)
        if C.get("needs_escalation"):
            C = s_escalate(C)
            C = s_verify(C)
        C = s_resolve_vendor(C)
        C = s_resolve_po(C)
        C = s_pair(C)
        C = s_match(C)
        C = s_screen(C)
        C = s_judge(C)
        C = s_rules(C)
        C = s_investigate(C)
        if C.get("agent_po"):
            C = s_rematch_with_agent_po(C)
            C = s_resolve_po(C)
            C = s_pair(C)
            C = s_match(C)
            C = s_screen(C)
            C = s_rules(C)
        C = s_decide(C)
        C = s_explain(C)
        C = s_propose(C)
    except Exception as e:  # noqa: BLE001
        _fail(run_id, e)
        raise
    if C["proposal"]["outcome"] == "AUTO_APPROVE":
        from .apply import apply_decision

        apply_decision(C["proposal_id"], "clearline", "system", "approve", "All checks passed (auto-approve)", None, {})
    else:
        emit(run_id, "run", "run.finished", "awaiting_human", f"Waiting for {C['proposal']['owner_role'] or 'a reviewer'}")
    return {"run_id": run_id, "proposal_id": C["proposal_id"], "outcome": C["proposal"]["outcome"]}


@DBOS.step()
def _fail(run_id: str, e: Exception) -> None:
    db.q("UPDATE runs SET status='failed', error=%s, finished_at=now() WHERE id=%s", (f"{type(e).__name__}: {e}"[:500], run_id))
    emit(run_id, "run", "run.finished", "error", f"Run failed: {type(e).__name__}: {str(e)[:200]}")


# ------------------------------------------------------------------ starting runs

def new_run(document_id: int, trigger: str = "ingest", invoice_id: int | None = None, parent_run_id: str | None = None,
            pinned: dict | None = None, pace_ms: int = 0, start: bool = True) -> str:
    run_id = f"R-{datetime.now(timezone.utc):%y%m%d}-{uuid.uuid4().hex[:6]}"
    db.q("""INSERT INTO runs (id, invoice_id, document_id, parent_run_id, trigger, status, mode, pinned_fields, model_versions)
            VALUES (%s,%s,%s,%s,%s,'running',%s,%s,%s)""",
         (run_id, invoice_id, document_id, parent_run_id, trigger, settings().ai_mode, db.J(pinned or {}),
          db.J({"provider": settings().model_provider, "read_a": settings().gemini_read_a, "read_b": settings().gemini_read_b,
                "pace_ms": pace_ms})))
    emit(run_id, "run", "run.started", "running", f"Run started ({trigger.replace('_', ' ')})")
    if start:
        from dbos import SetWorkflowID

        with SetWorkflowID(run_id):
            DBOS.start_workflow(process_invoice, run_id)
    return run_id
