"use client";
import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { OUTCOME, get, money } from "@/lib/api";
import { PageHeader } from "@/components/shell";
import { Badge, Card, Stat } from "@/components/ui";

const COLORS: Record<string, string> = { AUTO_APPROVE: "#10b981", REVIEW: "#f59e0b", HOLD: "#f97316", REJECT: "#e11d48", NOT_AN_INVOICE: "#94a3b8", APPLY_CREDIT: "#0ea5e9" };
const RULE: Record<string, string> = {
  "PAY-01": "Bank not on file", "PAY-02": "Lookalike sender", "PAY-03": "Bank-change request / pressure", "PAY-04": "New payee / recent bank change",
  "DOC-02": "Tampered document", "DUP-01": "Duplicate", "DUP-02": "Near-duplicate", "MAT-01": "Price above PO", "MAT-02": "Not yet received",
  "MAT-03": "Freight over allowance", "PO-01": "PO missing / inferred", "PO-03": "PO overrun", "EXT-01": "Missing field", "EXT-02": "Uncertain field",
  "EXT-03": "Doesn't add up", "SEC-01": "Text aimed at automation", "APR-01": "Above approval limit", "DOC-01": "Not an invoice", "VEN-01": "Vendor unknown",
};

export default function Dashboard() {
  const [d, setD] = useState<any>(null);
  useEffect(() => {
    const f = () => get("/api/dashboard").then(setD).catch(() => {});
    f();
    const t = setInterval(f, 5000);
    return () => clearInterval(t);
  }, []);
  if (!d) return <div className="p-6 text-sm text-slate-500">Loading…</div>;
  const outcomes = Object.entries(d.by_outcome).map(([k, v]) => ({ k, label: OUTCOME[k]?.label ?? k, v }));
  const caughtTotal = Object.values(d.caught).reduce((a: number, b: any) => a + Number(b), 0);
  const ev = d.eval;
  return (
    <div>
      <PageHeader title="Dashboard" sub="Latest decision per invoice. Money is shown in USD (other currencies converted at fixed demo rates)." />
      <div className="space-y-4 p-6">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Stat label="Invoices" value={d.invoices} hint={`${d.awaiting} waiting for a person`} />
          <Stat label="Touchless rate" value={`${Math.round(d.touchless_rate * 100)}%`} hint="first runs auto-approved and posted" />
          <Stat label="Stopped before paying" value={money(caughtTotal)} hint="payment safety + duplicates + over-billing held" tone="text-rose-700" />
          <Stat label="Median machine time" value={d.median_machine_ms ? `${(d.median_machine_ms / 1000).toFixed(1)} s` : "—"} hint="intake → decision" />
          <Stat label="Model cost / invoice" value={`$${Number(d.avg_cost_usd).toFixed(4)}`} hint={<>machine only · industry avg $9.40 fully loaded (Ardent Partners)</>} />
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Card title="Outcomes">
            <div className="h-56"><ResponsiveContainer><BarChart data={outcomes}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="label" fontSize={11} /><YAxis allowDecimals={false} fontSize={11} /><Tooltip /><Bar dataKey="v" radius={[4, 4, 0, 0]}>{outcomes.map((o) => <Cell key={o.k} fill={COLORS[o.k] ?? "#64748b"} />)}</Bar></BarChart></ResponsiveContainer></div>
          </Card>
          <Card title="What it caught">
            <table className="w-full text-sm"><tbody>
              <tr className="border-b border-slate-100"><td className="py-2">Unsafe payment (fraud / tampering)</td><td className="text-right font-semibold tabular-nums text-rose-700">{money(d.caught.payment_safety)}</td></tr>
              <tr className="border-b border-slate-100"><td className="py-2">Duplicates</td><td className="text-right font-semibold tabular-nums">{money(d.caught.duplicate)}</td></tr>
              <tr><td className="py-2">Over-billing held (not received / over PO / price)</td><td className="text-right font-semibold tabular-nums">{money(d.caught.overbilling)}</td></tr>
            </tbody></table>
            <p className="mt-3 text-xs text-slate-500">Counts only amounts on invoices that were stopped — not every flag.</p>
          </Card>
          <Card title="Top reasons a person was needed">
            <ul className="space-y-1.5 text-sm">{d.top_rules.map(([r, n]: [string, number]) => <li key={r} className="flex items-center gap-2"><span className="flex-1">{RULE[r] ?? r}</span><span className="h-1.5 rounded bg-slate-300" style={{ width: `${n * 18}px` }} /><span className="w-5 text-right tabular-nums text-slate-500">{n}</span></li>)}</ul>
          </Card>
        </div>
        {ev && (
          <Card title="Evaluation: scenarios with ground truth" action={<span className="text-xs text-slate-500">last run {new Date(ev.at).toLocaleString()} · <b>{ev.passed}/{ev.n} pass</b> · decision accuracy {Math.round(ev.decision_accuracy * 100)}% · Verified-but-wrong fields: <b>{ev.verified_but_wrong}</b></span>}>
            <table className="w-full text-xs">
              <thead className="text-left text-slate-500"><tr><th className="py-1">Scenario</th><th>Expected</th><th>Got</th><th>Fields</th><th className="text-right">Cost</th><th className="text-right">Time</th><th className="pl-4">Question asked</th></tr></thead>
              <tbody>{ev.results.map((r: any) => (
                <tr key={r.id} className="border-t border-slate-100">
                  <td className="py-1.5 font-mono">{r.id}</td><td>{r.expected.join("/")}</td>
                  <td><Badge tone={r.pass ? "green" : "red"}>{r.outcome}</Badge></td>
                  <td>{Object.values(r.fields).filter(Boolean).length}/{Object.keys(r.fields).length}</td>
                  <td className="text-right tabular-nums">${r.cost_usd.toFixed(4)}</td><td className="text-right tabular-nums">{(r.ms / 1000).toFixed(1)}s</td>
                  <td className="max-w-lg truncate pl-4 text-slate-600">{r.question}</td>
                </tr>
              ))}</tbody>
            </table>
          </Card>
        )}
      </div>
    </div>
  );
}
