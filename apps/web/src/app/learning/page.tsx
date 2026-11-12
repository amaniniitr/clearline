"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { get } from "@/lib/api";
import { PageHeader } from "@/components/shell";
import { Badge, Card, Stat } from "@/components/ui";

const TIER: Record<string, { label: string; tone: string; what: string }> = {
  shadow: { label: "Shadow (T3)", tone: "slate", what: "Learned; still full read while the template proves itself" },
  t2: { label: "T2", tone: "blue", what: "Template reads, one cheap model cross-checks" },
  t1: { label: "T1", tone: "green", what: "Template only, with deterministic checks; 1 in 10 audited" },
  retired: { label: "Retired", tone: "slate", what: "" },
};

export default function Learning() {
  const [d, setD] = useState<any>(null);
  useEffect(() => {
    const f = () => get("/api/learning").then(setD).catch(() => {});
    f();
    const t = setInterval(f, 4000);
    return () => clearInterval(t);
  }, []);
  if (!d) return <div className="p-6 text-sm text-slate-500">Loading…</div>;
  const curve = d.curve.map((c: any, i: number) => ({ n: i + 1, cost: Number(c.cost_usd) * 1000, read: c.read_ms, tier: c.read_tier, vendor: c.vendor, id: c.id }));
  const t1 = d.curve.filter((c: any) => c.read_tier === "T1").length;
  const t2 = d.curve.filter((c: any) => c.read_tier === "T2").length;
  const first = d.curve.filter((c: any) => c.read_tier === "T3").map((c: any) => Number(c.cost_usd));
  const later = d.curve.filter((c: any) => c.read_tier !== "T3").map((c: any) => Number(c.cost_usd));
  const avg = (a: number[]) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0);
  return (
    <div>
      <PageHeader title="Learning" sub="Clearline learns to read cheaper and to ask fewer questions. It never learns to pay more loosely on its own." />
      <div className="space-y-4 p-6">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Stat label="Known layouts" value={d.layouts.length} hint={`${d.layouts.filter((l: any) => l.status === "t1").length} at T1 · ${d.layouts.filter((l: any) => l.status === "t2").length} at T2`} />
          <Stat label="Invoices read by template" value={t1 + t2} hint={`${t1} template-only · ${t2} with cross-check`} />
          <Stat label="Avg cost: full read" value={`$${avg(first).toFixed(4)}`} hint="new vendor or layout" />
          <Stat label="Avg cost: known layout" value={`$${avg(later).toFixed(4)}`} hint="T2 / T1" tone="text-emerald-700" />
          <Stat label="Judgment cache" value={d.cache?.hits ?? 0} hint={`hits over ${d.cache?.entries ?? 0} remembered answers`} />
        </div>
        <Card title="Learning curve" action={<span className="text-[11px] text-slate-500">model cost per invoice, in order · ■ full read (T3) · ■ template + cross-check (T2) · ■ template only (T1)</span>}>
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart data={curve}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="n" fontSize={11} />
                <YAxis fontSize={11} tickFormatter={(v) => `$${(v / 1000).toFixed(3)}`} width={56} />
                <Tooltip formatter={(v: any) => [`$${(Number(v) / 1000).toFixed(4)}`, "model cost"]} labelFormatter={(n: any) => { const c = curve[Number(n) - 1]; return c ? `#${n} ${c.vendor ?? ""} · read tier ${c.tier}` : String(n); }} />
                <Bar dataKey="cost" radius={[3, 3, 0, 0]}>
                  {curve.map((c: any) => <Cell key={c.id} fill={c.tier === "T1" ? "#10b981" : c.tier === "T2" ? "#0ea5e9" : "#0f172a"} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card title="Layouts (per vendor)">
            <table className="w-full text-xs">
              <thead className="text-left text-slate-500"><tr><th className="py-1">Vendor</th><th>Tier</th><th className="text-right">Agreements</th><th className="text-right">Uses</th><th className="text-right">Demotions</th><th className="text-right">Version</th></tr></thead>
              <tbody>{d.layouts.map((l: any) => (
                <tr key={l.id} className="border-t border-slate-100" title={TIER[l.status]?.what}>
                  <td className="py-1.5">{l.vendor} <span className="text-slate-400">#{l.id}</span></td>
                  <td><Badge tone={TIER[l.status]?.tone}>{TIER[l.status]?.label ?? l.status}</Badge></td>
                  <td className="text-right tabular-nums">{l.agree_streak}</td><td className="text-right tabular-nums">{l.uses}</td>
                  <td className="text-right tabular-nums">{l.demotions}</td><td className="text-right tabular-nums">v{l.version}</td>
                </tr>
              ))}</tbody>
            </table>
            <p className="mt-3 text-[11px] text-slate-500">Promotion: 3 agreements in shadow → T2; 10 more with no corrections → T1. Any failed check, layout change or correction demotes immediately, and that invoice gets a full read in the same run.</p>
          </Card>
          <Card title="Suggested policy changes (from repeated human decisions)">
            {d.suggestions.length === 0 ? <p className="text-sm text-slate-500">None yet. A suggestion needs ≥ 5 consistent decisions from ≥ 2 people with none contrary, and never touches locked safety rules.</p> : (
              <ul className="space-y-2 text-sm">{d.suggestions.map((s: any, i: number) => (
                <li key={i} className="flex items-start gap-2"><Badge tone={s.status === "ready" ? "green" : "slate"}>{s.status}</Badge><span>{s.suggestion} <span className="text-xs text-slate-500">({s.deciders.join(", ")})</span></span></li>
              ))}</ul>
            )}
          </Card>
          <Card title="Vendor item map (learned)">
            {d.items.length === 0 ? <p className="text-sm text-slate-500">Lines paired by description and confirmed are remembered here, so the next invoice pairs them deterministically.</p> : (
              <table className="w-full text-xs"><tbody>{d.items.map((x: any) => <tr key={x.vendor_id + x.invoice_text_norm} className="border-t border-slate-100"><td className="py-1">{x.vendor_id}</td><td>{x.invoice_text_norm}</td><td className="font-mono">{x.sku}</td><td className="text-right">{x.uses} uses</td></tr>)}</tbody></table>
            )}
            {d.aliases.length > 0 && <div className="mt-3 text-xs text-slate-600">Learned aliases: {d.aliases.map((a: any) => `${a.alias} → ${a.vendor_id}`).join(", ")}</div>}
          </Card>
          <Card title="Learning events">
            <ul className="max-h-64 space-y-1 overflow-auto text-xs">{d.events.map((e: any) => (
              <li key={e.id} className="flex gap-2"><Badge>{e.kind.replace("_", " ")}</Badge><span className="text-slate-600">{e.vendor_id} {e.detail?.to ? `→ ${e.detail.to}` : ""} {e.detail?.reason ?? ""}</span>{e.run_id && <Link className="ml-auto font-mono text-slate-400 hover:underline" href={`/runs/${e.run_id}`}>{e.run_id}</Link>}</li>
            ))}</ul>
          </Card>
        </div>
        <Card title="What is never learned">
          <ul className="grid grid-cols-1 gap-1 text-sm text-slate-700 md:grid-cols-2">
            <li>• Bank details: always checked against the verified vendor master</li>
            <li>• The render check on payment fields runs at every tier</li>
            <li>• Duplicate checks and the locked safety rules (PAY-*, DOC-02, DUP-01, SEC-01)</li>
            <li>• Nothing is learned from runs with lookalike senders, bank mismatches or injection attempts</li>
          </ul>
        </Card>
      </div>
    </div>
  );
}
