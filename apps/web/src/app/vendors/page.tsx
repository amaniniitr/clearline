"use client";
import { useEffect, useState } from "react";
import { get, money } from "@/lib/api";
import { PageHeader } from "@/components/shell";
import { Badge, Card } from "@/components/ui";

const CUR: Record<string, string> = { "V-1005": "INR", "V-1006": "EUR" };

export default function Vendors() {
  const [vs, setVs] = useState<any[]>([]);
  useEffect(() => { get("/api/vendors").then(setVs).catch(() => {}); }, []);
  return (
    <div>
      <PageHeader title="Vendors & POs" sub="The procurement side: approved vendors, verified bank accounts with call-back numbers, and how much of each PO is billed." />
      <div className="grid grid-cols-1 gap-4 p-6 xl:grid-cols-2">
        {vs.map((v) => (
          <Card key={v.id} title={<span>{v.legal_name} <span className="font-mono text-xs text-slate-400">{v.id}</span></span>} action={<Badge tone={v.status === "approved" ? "green" : "red"}>{v.status}</Badge>}>
            <div className="text-xs text-slate-600">Tax ID {v.tax_id} · {v.domains.join(", ")} · call-back {v.phone} · {v.category}</div>
            <div className="mt-3 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Bank accounts</div>
            <ul className="mt-1 space-y-0.5 text-xs">{v.banks.map((b: any) => (
              <li key={b.id} className="flex gap-2"><span className="font-mono">••••{b.last4}</span><span className="text-slate-500">verified {b.verified_at?.slice(0, 10)} by {b.verified_by}</span>{!b.active && <Badge>inactive</Badge>}</li>
            ))}</ul>
            <div className="mt-3 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Purchase orders</div>
            <ul className="mt-1 space-y-1.5">{v.pos.filter((p: any) => Number(p.total) > 0).map((p: any) => {
              const pct = Math.min(100, (Number(p.billed) / Number(p.total)) * 100);
              return (
                <li key={p.id} className="text-xs">
                  <div className="flex justify-between"><span className="font-mono">{p.id}</span><span className="tabular-nums text-slate-600">{money(p.billed, CUR[v.id])} of {money(p.total, CUR[v.id])} billed</span></div>
                  <div className="mt-0.5 h-1.5 rounded bg-slate-100"><div className="h-1.5 rounded bg-slate-800" style={{ width: `${pct}%` }} /></div>
                </li>
              );
            })}</ul>
            {v.aliases?.length > 0 && <div className="mt-3 text-[11px] text-slate-500">Also known as: {v.aliases.map((a: any) => a.alias + (a.source === "learned" ? " (learned)" : "")).join(" · ")}</div>}
          </Card>
        ))}
      </div>
    </div>
  );
}
