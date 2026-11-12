"use client";
import { Fragment, use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowRight, Bot, CheckCircle2, ChevronDown, ChevronRight, CircleDollarSign, Clock, FileSearch, GitBranch, Mail, ShieldAlert, ShieldCheck, XCircle } from "lucide-react";
import { API, FIELD_LABEL, STAGES, get, money, post } from "@/lib/api";
import { Badge, Button, Card, Evidence, OutcomeBadge, StatusDot, cn } from "@/components/ui";
import { useRole } from "@/components/shell";

type Ev = { seq: number; stage: string; type: string; status: string | null; summary: string | null; data: any; ts: string; duration_ms: number | null };

export default function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [d, setD] = useState<any>(null);
  const [events, setEvents] = useState<Ev[]>([]);
  const [hover, setHover] = useState<string | null>(null);
  const lastSeq = useRef(0);

  const load = useCallback(() => get(`/api/runs/${id}`).then((x) => setD(x)), [id]);

  useEffect(() => {
    load();
    lastSeq.current = 0;
    setEvents([]);
    const es = new EventSource(`${API}/api/runs/${id}/events`);
    es.onmessage = (m) => {
      const e: Ev = JSON.parse(m.data);
      if (e.seq <= lastSeq.current) return;
      lastSeq.current = e.seq;
      setEvents((prev) => [...prev, e]);
      if (["proposal", "run.finished"].includes(e.type) || (e.type === "stage.finished" && ["commit", "learn", "verify", "rules"].includes(e.stage))) load();
    };
    return () => es.close();
  }, [id, load]);

  const stageState = useMemo(() => {
    const m: Record<string, { status: string; ms?: number | null; summary?: string | null }> = {};
    for (const e of events) {
      if (e.type === "stage.started") m[e.stage] = { status: "running" };
      if (e.type === "stage.finished") m[e.stage] = { status: e.status ?? "pass", ms: (m[e.stage]?.ms ?? 0) + (e.duration_ms ?? 0), summary: e.summary };
    }
    return m;
  }, [events]);

  if (!d) return <div className="p-6 text-sm text-slate-500">Loading run…</div>;
  const run = d.run;
  const p = d.proposal;
  const fields = d.facts?.extraction?.fields ?? liveFields(events);
  const cur = d.facts?.extraction?.ctx?.currency ?? run.currency ?? "USD";
  const latest = d.lineage?.[d.lineage.length - 1];
  const superseded = p?.superseded_by && latest && latest.id !== run.id;
  const t0 = events[0] ? new Date(events[0].ts).getTime() : 0;
  const agentSteps = events.filter((e) => e.type === "agent.step");

  return (
    <div className="pb-10">
      {/* header */}
      <div className="border-b border-slate-200 bg-white px-6 py-4">
        <div className="flex flex-wrap items-center gap-3">
          <Link href="/" className="text-xs text-slate-500 hover:underline">← Runs</Link>
          <span className="font-mono text-xs text-slate-400">{run.id}</span>
          {run.trigger !== "ingest" && <Badge tone="violet"><GitBranch size={11} /> {run.trigger.replace("_", " ")} of {run.parent_run_id}</Badge>}
          {run.read_tier && <Badge tone={run.read_tier === "T1" ? "green" : run.read_tier === "T2" ? "blue" : "slate"}>Read tier {run.read_tier}</Badge>}
          <span className="flex-1" />
          <span className="text-xs text-slate-500">Model cost <b className="tabular-nums text-slate-700">${Number(run.cost_usd ?? 0).toFixed(4)}</b></span>
        </div>
        <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <h1 className="text-lg font-semibold">{run.vendor ?? fields?.vendor_name?.value ?? d.document?.filename}</h1>
          <span className="text-sm text-slate-600">Invoice {run.invoice_number ?? fields?.invoice_number?.value ?? "—"}</span>
          <span className="text-sm font-semibold tabular-nums">{money(run.total ?? fields?.total?.value, cur)}</span>
          <OutcomeBadge outcome={p?.outcome} sub={p?.sub_status} />
          {d.document?.email_from && <span className="flex items-center gap-1 text-xs text-slate-500"><Mail size={12} /> {d.document.email_from}</span>}
        </div>
        {superseded && (
          <div className="mt-3 flex items-center gap-2 rounded-lg bg-violet-50 px-3 py-2 text-sm text-violet-800">
            This proposal was superseded by a newer run. <Link className="font-medium underline" href={`/runs/${latest.id}`}>Open {latest.id} <ArrowRight className="inline" size={13} /></Link>
          </div>
        )}
        <StageRail state={stageState} />
      </div>

      <div className="grid grid-cols-1 gap-4 p-4 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)_minmax(340px,0.9fr)]">
        {/* document */}
        <div className="space-y-4">
          <Card title="Document" action={<span className="text-[11px] text-slate-500">{d.document?.text_layer ? "text layer" : "image · OCR"} · highlights show where each value was found</span>}>
            <DocView doc={d.document} fields={fields} hover={hover} />
          </Card>
          {d.document?.email_body && (
            <Card title={<span className="flex items-center gap-2"><Mail size={14} /> Email</span>}>
              <div className="text-xs text-slate-500">From {d.document.email_from}{d.document.email_reply_to && <> · Reply-To <b className="text-rose-700">{d.document.email_reply_to}</b></>}</div>
              <div className="mt-1 text-sm font-medium">{d.document.email_subject}</div>
              <pre className="mt-2 whitespace-pre-wrap font-sans text-[13px] leading-snug text-slate-700">{d.document.email_body}</pre>
            </Card>
          )}
        </div>

        {/* process */}
        <div className="space-y-4">
          <Card title="What happened, step by step" action={<span className="text-[11px] text-slate-500">{events.length} events · live</span>}>
            <ol className="space-y-1.5">
              {events.filter((e) => e.type !== "stage.started" && e.type !== "run.started" || e.stage === "run").map((e) => (
                <li key={e.seq} className="slide-in flex gap-2.5 text-[13px]">
                  <span className="mt-1.5"><StatusDot status={e.type === "agent.step" ? "running" : e.status} /></span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-2">
                      <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{e.type === "agent.step" ? "agent" : STAGES.find((s) => s.id === e.stage)?.label ?? e.stage}</span>
                      <span className="text-[10px] tabular-nums text-slate-400">+{((new Date(e.ts).getTime() - t0) / 1000).toFixed(1)}s{e.duration_ms ? ` · ${e.duration_ms} ms` : ""}</span>
                    </div>
                    <div className={cn("leading-snug", e.status === "block" ? "text-rose-800" : e.status === "warn" ? "text-amber-800" : "text-slate-700")}>{e.summary}</div>
                  </div>
                </li>
              ))}
            </ol>
          </Card>
          {(agentSteps.length > 0 || d.investigation) && (
            <Card title={<span className="flex items-center gap-2"><Bot size={14} /> Investigation agent</span>} action={<span className="text-[11px] text-slate-500">{d.investigation?.backend} · read-only tools · ≤ 6 calls</span>}>
              <ol className="space-y-1 text-[13px]">
                {(d.investigation?.steps ?? agentSteps.map((e) => e.data)).map((s: any, k: number) => (
                  <li key={k} className="flex gap-2"><span className="font-mono text-[11px] text-slate-400">{k + 1}</span><span className="font-mono text-xs text-sky-700">{s.tool}</span><span className="text-slate-600">{s.summary}</span></li>
                ))}
              </ol>
              {d.investigation?.hypotheses?.length > 0 && (
                <ul className="mt-3 list-disc space-y-1 pl-5 text-[13px] text-slate-700">{d.investigation.hypotheses.map((h: string, k: number) => <li key={k}>{h}</li>)}</ul>
              )}
              {d.investigation?.recheck_result && (
                <div className="mt-2 text-xs text-slate-500">Deterministic re-check of the agent&apos;s proposal: <b>{d.investigation.recheck_result.po_id} {d.investigation.recheck_result.ok ? "confirmed" : "rejected"}</b> (agent results are capped at Review)</div>
              )}
            </Card>
          )}
          {p && <Checklist findings={p.findings?.findings ?? []} />}
          <Fields fields={fields} onHover={setHover} cur={cur} />
          {d.po && <PoTable po={d.po} lines={d.facts?.facts?.lines ?? []} cur={cur} />}
        </div>

        {/* decision */}
        <div className="space-y-4">
          {p ? <DecisionCard p={p} run={run} cur={cur} superseded={!!superseded} onDone={load} decisions={d.decisions} /> : (
            <Card title="Decision"><div className="flex items-center gap-2 text-sm text-slate-500"><Clock size={14} className="animate-spin" /> Working…</div></Card>
          )}
          {d.lineage?.length > 1 && (
            <Card title={<span className="flex items-center gap-2"><GitBranch size={14} /> Run lineage</span>}>
              <ol className="space-y-1 text-xs">
                {d.lineage.map((l: any) => (
                  <li key={l.id} className={cn("flex items-center gap-2", l.id === run.id && "font-semibold")}>
                    <Link href={`/runs/${l.id}`} className="font-mono hover:underline">{l.id}</Link>
                    <span className="text-slate-500">{l.trigger.replace("_", " ")}</span>
                    <OutcomeBadge outcome={l.outcome} />
                    {l.amount_payable && <span className="tabular-nums text-slate-600">{money(l.amount_payable, cur)}</span>}
                  </li>
                ))}
              </ol>
            </Card>
          )}
          {d.facts?.extraction?.judge && <JudgeCard judge={d.facts.extraction.judge} />}
        </div>
      </div>
    </div>
  );
}

function liveFields(events: Ev[]) {
  const v = [...events].reverse().find((e) => e.stage === "verify" && e.type === "stage.finished");
  return v?.data?.fields ?? null;
}

function StageRail({ state }: { state: Record<string, any> }) {
  return (
    <div className="mt-4 flex flex-wrap gap-1">
      {STAGES.map((s) => {
        const st = state[s.id];
        const tone = !st ? "border-slate-200 bg-white text-slate-400" : {
          running: "border-sky-300 bg-sky-50 text-sky-800 pulse-ring", pass: "border-emerald-200 bg-emerald-50 text-emerald-800",
          warn: "border-amber-200 bg-amber-50 text-amber-800", block: "border-rose-200 bg-rose-50 text-rose-800",
          degraded: "border-violet-200 bg-violet-50 text-violet-800", skipped: "border-slate-200 bg-slate-50 text-slate-500", error: "border-rose-400 bg-rose-100 text-rose-900",
        }[st.status as string] ?? "border-slate-200 bg-slate-50";
        return (
          <div key={s.id} title={st?.summary ?? ""} className={cn("flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] font-medium", tone)}>
            <StatusDot status={st?.status} />
            {s.label}
            {st?.ms ? <span className="tabular-nums opacity-60">{st.ms < 1000 ? `${st.ms}ms` : `${(st.ms / 1000).toFixed(1)}s`}</span> : null}
          </div>
        );
      })}
    </div>
  );
}

function DocView({ doc, fields, hover }: { doc: any; fields: any; hover: string | null }) {
  if (!doc) return null;
  const meta = doc.pages_meta?.[0];
  const src = `${API}/api/documents/${doc.id}/page/1.png?scale=1.6`;
  const colors: Record<string, string> = { verified: "border-emerald-500 bg-emerald-400/15", read: "border-sky-500 bg-sky-400/15", uncertain: "border-amber-500 bg-amber-400/25" };
  return (
    <div className="relative overflow-hidden rounded-lg border border-slate-200 bg-slate-100">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt="document page" className="block w-full" />
      {meta && fields && Object.entries(fields).map(([k, f]: [string, any]) => {
        if (!f?.bbox || (f.page ?? 1) !== 1) return null;
        const [x0, top, x1, bottom] = f.bbox;
        const pad = 1.5;
        return (
          <div key={k} title={`${FIELD_LABEL[k] ?? k}: ${f.value} (${f.evidence})`}
            className={cn("absolute rounded-sm border-2 transition", colors[f.evidence] ?? "border-slate-400", hover === k && "ring-4 ring-slate-900/30 scale-[1.03]")}
            style={{ left: `${((x0 - pad) / meta.width) * 100}%`, top: `${((top - pad) / meta.height) * 100}%`,
                     width: `${((x1 - x0 + 2 * pad) / meta.width) * 100}%`, height: `${((bottom - top + 2 * pad) / meta.height) * 100}%` }} />
        );
      })}
      <div className="absolute bottom-2 right-2 flex gap-1 rounded-md bg-white/90 px-2 py-1 text-[10px] shadow">
        <span className="text-emerald-700">■ Verified</span><span className="text-sky-700">■ Read</span><span className="text-amber-700">■ Uncertain</span>
      </div>
    </div>
  );
}

function Fields({ fields, onHover, cur }: { fields: any; onHover: (k: string | null) => void; cur: string }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!fields) return null;
  const order = ["vendor_name", "invoice_number", "invoice_date", "due_date", "po_number", "currency", "subtotal", "tax", "freight", "total", "payment_terms", "remit_bank_name", "remit_iban", "remit_account_number", "remit_routing_number", "vendor_tax_id"];
  return (
    <Card title={<span className="flex items-center gap-2"><FileSearch size={14} /> Extracted fields</span>} action={<span className="text-[11px] text-slate-500">two independent readings · hover to locate</span>}>
      <table className="w-full text-[13px]">
        <tbody>
          {order.filter((k) => fields[k] && (fields[k].value || fields[k].evidence !== "absent")).map((k) => {
            const f = fields[k];
            return (
              <Fragment key={k}>
                <tr className="cursor-pointer border-t border-slate-100 hover:bg-slate-50" onMouseEnter={() => onHover(k)} onMouseLeave={() => onHover(null)} onClick={() => setOpen(open === k ? null : k)}>
                  <td className="py-1.5 pr-2 text-slate-500">{open === k ? <ChevronDown className="inline" size={12} /> : <ChevronRight className="inline" size={12} />} {FIELD_LABEL[k] ?? k}</td>
                  <td className="py-1.5 font-medium">{f.value ?? <span className="text-slate-400">—</span>}</td>
                  <td className="py-1.5 text-right"><Evidence level={f.evidence} /></td>
                </tr>
                {open === k && (
                  <tr className="bg-slate-50 text-xs text-slate-600">
                    <td colSpan={3} className="px-3 py-2">
                      <div>Reading A (vision + schema): <b>{f.readings?.a ?? "—"}</b> · Reading B (text lines + citation): <b>{f.readings?.b ?? "—"}</b></div>
                      <ul className="mt-1 list-disc pl-4">{f.reasons?.map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}

function Checklist({ findings }: { findings: any[] }) {
  const [showPass, setShowPass] = useState(false);
  const fails = findings.filter((f) => f.status === "fail");
  const passes = findings.filter((f) => f.status === "pass");
  return (
    <Card title="Checks" action={<span className="text-[11px] text-slate-500">{passes.length} passed · {fails.length} need attention</span>}>
      <ul className="space-y-1.5">
        {fails.map((f) => (
          <li key={f.rule_id} className="flex gap-2 text-[13px]">
            {["HOLD", "REJECT"].includes(f.outcome) ? <XCircle size={15} className="mt-0.5 shrink-0 text-rose-600" /> : <AlertTriangle size={15} className="mt-0.5 shrink-0 text-amber-600" />}
            <div><span className="font-medium">{f.title}</span> {f.locked && <Badge className="ml-1">locked</Badge>}<div className="text-slate-600">{f.message}</div></div>
          </li>
        ))}
      </ul>
      <button className="mt-3 text-xs text-slate-500 hover:underline" onClick={() => setShowPass(!showPass)}>{showPass ? "Hide" : "Show"} {passes.length} passed checks</button>
      {showPass && (
        <ul className="mt-2 grid grid-cols-1 gap-1 sm:grid-cols-2">
          {passes.map((f) => <li key={f.rule_id} className="flex items-center gap-1.5 text-xs text-slate-600"><CheckCircle2 size={13} className="text-emerald-600" /> {f.title}</li>)}
        </ul>
      )}
    </Card>
  );
}

function PoTable({ po, lines, cur }: { po: any; lines: any[]; cur: string }) {
  const byLine: Record<string, any> = {};
  lines.forEach((l) => l.po_line_id && (byLine[l.po_line_id] = l));
  return (
    <Card title={`Invoice vs ${po.id} vs goods received`} action={<span className="text-[11px] text-slate-500">PO total {money(po.row?.total, po.row?.currency ?? cur)} · {po.grns.length} receipt(s)</span>}>
      <table className="w-full text-xs">
        <thead className="text-left text-slate-500"><tr><th className="py-1">Item</th><th className="text-right">Ordered</th><th className="text-right">Received</th><th className="text-right">Billed before</th><th className="text-right">This invoice</th><th className="text-right">PO price</th><th className="text-right">Invoice price</th></tr></thead>
        <tbody>
          {po.lines.map((l: any) => {
            const inv = byLine[l.id];
            const short = inv && inv.qty_billed > inv.qty_received_net;
            const over = inv && inv.price_delta_abs > 0.0001;
            return (
              <tr key={l.id} className="border-t border-slate-100">
                <td className="py-1.5"><span className="font-mono">{l.sku}</span> <span className="text-slate-500">{l.description}</span></td>
                <td className="text-right tabular-nums">{Number(l.qty)}</td>
                <td className={cn("text-right tabular-nums", short && "font-semibold text-rose-700")}>{Number(l.received)}</td>
                <td className="text-right tabular-nums">{inv ? inv.qty_billed_before : "—"}</td>
                <td className={cn("text-right tabular-nums", short && "font-semibold text-rose-700")}>{inv ? inv.qty_billed : "—"}</td>
                <td className="text-right tabular-nums">{money(l.unit_price, cur)}</td>
                <td className={cn("text-right tabular-nums", over && "font-semibold text-amber-700")}>{inv ? money(inv.unit_price, cur) : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {po.grns.length > 0 && <div className="mt-2 text-[11px] text-slate-500">Receipts: {po.grns.map((g: any) => `${g.id} (${g.received_on})`).join(", ")}</div>}
    </Card>
  );
}

function JudgeCard({ judge }: { judge: any }) {
  const rows: [string, string][] = [];
  if (judge.requests_bank_change) rows.push(["Asks to change bank details", pword(judge.requests_bank_change.p)]);
  if (judge.pressure) rows.push(["Pressure / urgency", judge.pressure.label]);
  if (judge.instructs_automation) rows.push(["Text aimed at automated systems", pword(judge.instructs_automation.p)]);
  return (
    <Card title="Language checks" action={<span className="text-[11px] text-slate-500">typed judgments on redacted text</span>}>
      <table className="w-full text-[13px]"><tbody>{rows.map(([a, b]) => <tr key={a} className="border-t border-slate-100"><td className="py-1.5 text-slate-600">{a}</td><td className={cn("text-right font-medium", /very likely|likely|strong/.test(b) && "text-rose-700")}>{b}</td></tr>)}</tbody></table>
    </Card>
  );
}

function pword(p: number) {
  return p >= 0.9 ? "very likely" : p >= 0.6 ? "likely" : p >= 0.4 ? "can't tell" : p >= 0.1 ? "unlikely" : "no";
}

const OWNER: Record<string, string> = { ap_clerk: "AP clerk", ap_manager: "AP manager", controller: "Controller", receiving: "Receiving", procurement: "Procurement", budget_owner: "Budget owner" };

function DecisionCard({ p, run, cur, superseded, onDone, decisions }: { p: any; run: any; cur: string; superseded: boolean; onDone: () => void; decisions: any[] }) {
  const { role } = useRole();
  const [reason, setReason] = useState("");
  const [amount, setAmount] = useState<string>(String(p.amount_payable ?? ""));
  const [field, setField] = useState("invoice_number");
  const [value, setValue] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const decided = decisions?.some((x) => ["approve", "short_pay", "reject", "file", "apply_credit"].includes(x.action)) || run.status === "completed";

  async function act(action: string, extra: any = {}) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await post(`/api/proposals/${p.id}/decisions`, { actor: role.actor, role: role.id, action, reason: reason || extra.defaultReason, ...extra.body });
      setMsg({ ok: true, text: r.bill_id ? `Posted bill ${r.bill_id} for ${money(r.amount, cur)} · read-back ${r.read_back_ok ? "OK" : "mismatch"}` : r.run_id ? `Re-run started: ${r.run_id}` : `Done: ${r.status?.replace("_", " ")}` });
      if (r.run_id) setTimeout(() => (location.href = `/runs/${r.run_id}`), 900);
      onDone();
    } catch (e: any) {
      setMsg({ ok: false, text: e.message });
    }
    setBusy(false);
  }

  const terms = p.findings?.terms ?? {};
  const safe = p.safe_to_pay;
  return (
    <Card className={cn(p.outcome === "AUTO_APPROVE" && "ring-1 ring-emerald-200", ["HOLD", "REJECT"].includes(p.outcome) && "ring-1 ring-rose-200")} title="Decision">
      <div className="flex items-center gap-2"><OutcomeBadge outcome={p.outcome} sub={p.sub_status} className="text-sm" /></div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <div className="rounded-lg border border-slate-200 p-2.5">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Do we owe this?</div>
          <div className={cn("mt-0.5 flex items-center gap-1.5 text-sm font-semibold", p.owed === "yes" ? "text-emerald-700" : p.owed === "partly" ? "text-amber-700" : "text-slate-700")}>
            <CircleDollarSign size={15} /> {p.owed === "n/a" ? "n/a" : p.owed[0].toUpperCase() + p.owed.slice(1)}
          </div>
        </div>
        <div className="rounded-lg border border-slate-200 p-2.5">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Safe to pay where asked?</div>
          <div className={cn("mt-0.5 flex items-center gap-1.5 text-sm font-semibold", safe === "yes" ? "text-emerald-700" : safe === "no" ? "text-rose-700" : "text-slate-600")}>
            {safe === "no" ? <ShieldAlert size={15} /> : <ShieldCheck size={15} />} {safe === "n/a" ? "n/a" : safe === "yes" ? "Yes" : "No"}
          </div>
        </div>
      </div>
      {(Number(p.amount_payable) > 0 || Number(p.amount_held) > 0) && (
        <div className="mt-2 grid grid-cols-2 gap-2 text-sm">
          <div className={cn("rounded-lg p-2.5", safe === "no" ? "bg-slate-50" : "bg-emerald-50")}>
            <div className={cn("text-[10px] uppercase", safe === "no" ? "text-slate-500" : "text-emerald-700")}>{safe === "no" ? "Payable once verified" : "Payable now"}</div>
            <div className="font-semibold tabular-nums">{money(p.amount_payable, cur)}</div>
          </div>
          <div className="rounded-lg bg-orange-50 p-2.5"><div className="text-[10px] uppercase text-orange-700">{safe === "no" ? "On hold" : "Held"}</div><div className="font-semibold tabular-nums">{money(safe === "no" ? Number(p.amount_payable) + Number(p.amount_held) : p.amount_held, cur)}</div></div>
        </div>
      )}
      {p.narrative && <p className="mt-3 text-[13px] leading-relaxed text-slate-700">{p.narrative}</p>}
      {p.question && (
        <div className="mt-3 rounded-lg border-l-4 border-slate-900 bg-slate-50 p-3">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">The one question · for {OWNER[p.owner_role] ?? p.owner_role}</div>
          <div className="mt-0.5 text-sm font-medium text-slate-900">{p.question}</div>
          <div className="mt-1 text-xs text-slate-500">Recommended: {p.recommended_action}</div>
        </div>
      )}
      {p.attempted?.length > 0 && (
        <div className="mt-3">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">What I already tried</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-slate-600">{p.attempted.map((a: string, i: number) => <li key={i}>{a}</li>)}</ul>
        </div>
      )}
      {(terms.due_date || terms.discount_deadline) && (
        <div className="mt-3 text-xs text-slate-500">Due {terms.due_date ?? "—"}{terms.discount_deadline && <> · {terms.discount_pct}% early-pay discount until <b>{terms.discount_deadline}</b></>}</div>
      )}
      {p.gl_coding?.length > 0 && (
        <div className="mt-2 text-xs text-slate-500">GL: {p.gl_coding.map((g: any) => `${g.gl_account}${g.cost_center ? "/" + g.cost_center : ""} ${money(g.amount, cur)}`).join(" · ")}</div>
      )}

      {!decided && !superseded && p.outcome !== "AUTO_APPROVE" && (
        <div className="mt-4 space-y-2 border-t border-slate-100 pt-3">
          <div className="text-[11px] text-slate-500">Acting as <b>{role.label}</b> ({role.actor}). A reason is required.</div>
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (kept in the audit log)" className="h-16 w-full rounded-lg border border-slate-300 p-2 text-sm" />
          <div className="flex flex-wrap gap-2">
            {p.outcome === "NOT_AN_INVOICE" && <Button onClick={() => act("file", { defaultReason: "Statement filed for reconciliation" })} disabled={busy}>File statement</Button>}
            {p.outcome === "APPLY_CREDIT" && <Button onClick={() => act("apply_credit", { defaultReason: "Credit applied" })} disabled={busy}>Apply credit</Button>}
            {safe === "no" && (
              <>
                <Button variant="success" onClick={() => act("verify_bank", { body: { data: { result: "confirmed" } }, defaultReason: "Call-back on master-data number confirmed the new account" })} disabled={busy}>Call-back: vendor confirms</Button>
                <Button variant="danger" onClick={() => act("verify_bank", { body: { data: { result: "denied" } }, defaultReason: "Call-back: vendor did not change bank details" })} disabled={busy}>Call-back: vendor denies</Button>
              </>
            )}
            {["REVIEW", "HOLD"].includes(p.outcome) && safe !== "no" && (
              <>
                {p.outcome === "REVIEW" && Number(p.amount_held) <= 0 && <Button variant="success" onClick={() => act("approve", { defaultReason: "Confirmed" })} disabled={busy}>Approve {money(p.amount_payable, cur)}</Button>}
                {Number(p.amount_payable) > 0 && (Number(p.amount_held) > 0 || p.outcome === "HOLD") && (
                  <span className="flex items-center gap-1">
                    <input value={amount} onChange={(e) => setAmount(e.target.value)} className="h-9 w-28 rounded-lg border border-slate-300 px-2 text-sm tabular-nums" />
                    <Button variant="success" onClick={() => act("short_pay", { body: { amount: Number(amount) }, defaultReason: "Pay the confirmed portion" })} disabled={busy}>Short-pay</Button>
                  </span>
                )}
              </>
            )}
            {p.vendor_email && <Button variant="outline" onClick={() => act("request_info", { defaultReason: "Sent the drafted vendor email" })} disabled={busy}><Mail size={13} /> Send vendor email</Button>}
            {p.outcome !== "NOT_AN_INVOICE" && <Button variant="outline" onClick={() => act("reject", { defaultReason: "Rejected" })} disabled={busy}>Reject</Button>}
          </div>
          {["REVIEW", "HOLD"].includes(p.outcome) && (
            <details className="text-xs text-slate-600">
              <summary className="cursor-pointer">Correct a field or link a PO (starts a new run)</summary>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <select value={field} onChange={(e) => setField(e.target.value)} className="h-8 rounded-md border border-slate-300 px-1">
                  {["invoice_number", "invoice_date", "total", "po_number", "vendor_name", "currency"].map((f) => <option key={f} value={f}>{FIELD_LABEL[f]}</option>)}
                </select>
                <input value={value} onChange={(e) => setValue(e.target.value)} placeholder="correct value" className="h-8 w-36 rounded-md border border-slate-300 px-2" />
                <Button size="sm" variant="outline" onClick={() => act(field === "po_number" ? "link_po" : "correct_field", { body: { data: field === "po_number" ? { po_id: value } : { field, value } }, defaultReason: "Corrected from the document" })} disabled={busy || !value}>Re-run with this</Button>
              </div>
            </details>
          )}
        </div>
      )}
      {p.vendor_email && (
        <details className="mt-3 text-xs">
          <summary className="cursor-pointer text-slate-600">Drafted vendor email (a person sends it)</summary>
          <pre className="mt-2 whitespace-pre-wrap rounded-lg bg-slate-50 p-2 font-sans text-slate-700">{p.vendor_email}</pre>
        </details>
      )}
      {decisions?.length > 0 && (
        <div className="mt-3 border-t border-slate-100 pt-2 text-xs text-slate-600">
          {decisions.map((x: any) => <div key={x.id}><b>{x.actor}</b> ({x.role.replace("_", " ")}) · {x.action.replace("_", " ")}{x.amount ? ` ${money(x.amount, cur)}` : ""} — {x.reason}</div>)}
        </div>
      )}
      {msg && <div className={cn("mt-3 rounded-lg p-2 text-sm", msg.ok ? "bg-emerald-50 text-emerald-800" : "bg-rose-50 text-rose-800")}>{msg.text}</div>}
    </Card>
  );
}
