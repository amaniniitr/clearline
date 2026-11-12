"""Compile the policy catalog to JDM, store versions, and evaluate with GoRules ZEN."""
from __future__ import annotations

import copy
import json
from functools import lru_cache

import zen

from ..domain.models import Finding
from . import catalog


def _node(nid: str, ntype: str, name: str, content: dict | None = None, x: int = 0, y: int = 0) -> dict:
    n = {"id": nid, "type": ntype, "name": name, "position": {"x": x, "y": y}}
    if content is not None:
        n["content"] = content
    return n


def _lit(v) -> str:
    return json.dumps(v)


def tolerances_table(defaults: dict, vendor_rows: dict) -> dict:
    keys = list(defaults)
    outputs = [{"id": f"o_{k}", "name": k, "field": f"tol.{k}"} for k in keys]
    rules = []
    for vid, over in vendor_rows.items():
        row = {"_id": f"v_{vid}", "i_vendor": _lit(vid)}
        for k in keys:
            row[f"o_{k}"] = _lit(over.get(k, defaults[k]))
        rules.append(row)
    rules.append({"_id": "default", "i_vendor": "", **{f"o_{k}": _lit(defaults[k]) for k in keys}})
    return {"hitPolicy": "first", "passThrough": True, "inputField": None, "outputPath": None, "executionMode": "single",
            "inputs": [{"id": "i_vendor", "name": "Vendor", "field": "vendor_id"}], "outputs": outputs, "rules": rules}


def rule_table(rule: dict) -> dict:
    rid = rule["id"]
    outs = ["status", "outcome", "owner", "action", "message"]
    outputs = [{"id": f"o_{o}", "name": o, "field": o} for o in outs]
    rows = []
    for k, (cond, outcome, owner, action, msg) in enumerate(rule["fail"]):
        rows.append({"_id": f"{rid}-f{k}", "_description": f"fail variant {k}", "i_cond": cond,
                     "o_status": '"fail"', "o_outcome": _lit(outcome), "o_owner": _lit(owner),
                     "o_action": _lit(action), "o_message": _lit(msg)})
    rows.append({"_id": f"{rid}-pass", "i_cond": "", "o_status": '"pass"', "o_outcome": '"AUTO_APPROVE"',
                 "o_owner": "null", "o_action": "null", "o_message": _lit(rule["title"])})
    return {"hitPolicy": "first", "passThrough": False, "inputField": None, "outputPath": f"rules.{rid.replace('-', '_')}",
            "executionMode": "single", "inputs": [{"id": "i_cond", "name": "Condition", "field": ""}],
            "outputs": outputs, "rules": rows}


def build_jdm(defaults: dict | None = None, vendor_rows: dict | None = None, rules: list[dict] | None = None) -> dict:
    defaults = defaults or catalog.DEFAULT_TOLERANCES
    vendor_rows = vendor_rows if vendor_rows is not None else catalog.VENDOR_TOLERANCES
    rules = rules or catalog.RULES
    nodes = [_node("in", "inputNode", "Facts"),
             _node("tol", "decisionTableNode", "Tolerances", tolerances_table(defaults, vendor_rows), 250, 0),
             _node("out", "outputNode", "Findings", None, 800, 0)]
    # Per-line checks: tolerances applied to raw line measurements, inside the policy (not in Python).
    goods = "#.line_kind == 'goods' and #.paired == true"
    price_tol = "max([tol.price_pct * #.extended_po, tol.price_abs])"
    price_fail = f"{goods} and #.price_delta_abs > {price_tol}"
    unreceived = f"{goods} and #.qty_billed > #.qty_received_net"
    over_ordered = f"{goods} and #.qty_billed > #.qty_ordered_net"
    line_exprs = [
        ("line_checks", f"map(lines, {{idx: #.idx, price_tol: {goods} ? {price_tol} : null, "
                        f"price_fail: {price_fail}, unreceived: {unreceived}, over_ordered: {over_ordered}}})"),
        ("price_fail_lines", f"len(filter(lines, {price_fail}))"),
        ("qty_unreceived_lines", f"len(filter(lines, {unreceived}))"),
        ("qty_over_ordered_lines", f"len(filter(lines, {over_ordered}))"),
    ]
    nodes.append(_node("lchk", "expressionNode", "Line checks",
                       {"expressions": [{"id": f"lx{k}", "key": key, "value": v} for k, (key, v) in enumerate(line_exprs)],
                        "passThrough": True, "inputField": None, "outputPath": None, "executionMode": "single"}, 380, 0))
    edges = [{"id": "e_in_tol", "sourceId": "in", "targetId": "tol", "type": "edge"},
             {"id": "e_tol_lchk", "sourceId": "tol", "targetId": "lchk", "type": "edge"},
             {"id": "e_lchk_out", "sourceId": "lchk", "targetId": "out", "type": "edge"}]
    for k, r in enumerate(rules):
        nid = f"r_{r['id'].replace('-', '_')}"
        locked = " 🔒" if r["id"] in catalog.LOCKED else ""
        nodes.append(_node(nid, "decisionTableNode", f"{r['id']} {r['title']}{locked}", rule_table(r), 520, k * 80))
        edges.append({"id": f"e_lchk_{nid}", "sourceId": "lchk", "targetId": nid, "type": "edge"})
        edges.append({"id": f"e_{nid}_out", "sourceId": nid, "targetId": "out", "type": "edge"})
    # Expose the applied tolerances to decide().
    nodes.append(_node("tol_out", "expressionNode", "Applied tolerances",
                       {"expressions": [{"id": "x1", "key": "applied", "value": "tol"}], "passThrough": False,
                        "inputField": None, "outputPath": None, "executionMode": "single"}, 520, -80))
    edges.append({"id": "e_tol_tolout", "sourceId": "tol", "targetId": "tol_out", "type": "edge"})
    edges.append({"id": "e_tolout_out", "sourceId": "tol_out", "targetId": "out", "type": "edge"})
    return {"contentType": "application/vnd.gorules.decision", "nodes": nodes, "edges": edges,
            "meta": {"locked": sorted(catalog.LOCKED), "defaults": defaults, "vendor_rows": vendor_rows}}


@lru_cache(maxsize=16)
def _decision(jdm_json: str):
    return zen.ZenEngine().create_decision(jdm_json)


def evaluate(jdm: dict, facts: dict, trace: bool = True) -> tuple[list[Finding], dict, dict, list[dict]]:
    """Returns (findings for every rule, applied tolerances, trace, per-line check results)."""
    d = _decision(json.dumps(jdm, sort_keys=True))
    res = d.evaluate(copy.deepcopy(facts), {"trace": trace})
    result = res["result"]
    findings = []
    for key, row in (result.get("rules") or {}).items():
        rid = key.replace("_", "-")
        findings.append(Finding(rule_id=rid, status=row["status"], outcome=row["outcome"], owner=row.get("owner"),
                                action=row.get("action"), message=row.get("message") or "",
                                locked=rid in catalog.LOCKED, title=catalog.RULE_TITLES.get(rid, rid)))
    order = {r["id"]: k for k, r in enumerate(catalog.RULES)}
    findings.sort(key=lambda f: order.get(f.rule_id, 99))
    slim_trace = {}
    for nid, t in (res.get("trace") or {}).items():
        td = t.get("traceData")
        slim_trace[t.get("name", nid)] = {"row": td.get("rule", {}).get("_id") if isinstance(td, dict) else None,
                                          "ms": t.get("performance")}
    line_checks = [{k: (int(v) if k == "idx" else v) for k, v in lc.items()} for lc in (result.get("line_checks") or [])]
    return findings, result.get("applied") or result.get("tol") or {}, slim_trace, line_checks


def policy_text(defaults: dict, vendor_rows: dict) -> str:
    lines = ["# Clearline AP policy", "", "## Tolerances", ""]
    for k, v in defaults.items():
        lines.append(f"- `{k}`: {v}")
    if vendor_rows:
        lines += ["", "### Vendor-specific", ""]
        for vid, over in vendor_rows.items():
            lines.append(f"- {vid}: " + ", ".join(f"`{k}` = {v}" for k, v in over.items()))
    lines += ["", "## Checks", ""]
    for r in catalog.RULES:
        lock = " (locked)" if r["id"] in catalog.LOCKED else ""
        lines.append(f"- **{r['id']}** {r['title']}{lock}")
    return "\n".join(lines)
