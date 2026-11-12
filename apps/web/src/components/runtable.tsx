"use client";
import Link from "next/link";
import { ago, money } from "@/lib/api";
import { Badge, OutcomeBadge } from "./ui";

const OWNER: Record<string, string> = { ap_clerk: "AP clerk", ap_manager: "AP manager", controller: "Controller", receiving: "Receiving", procurement: "Procurement", budget_owner: "Budget owner" };

export function RunTable({ rows, showQuestion = false }: { rows: any[]; showQuestion?: boolean }) {
  if (!rows.length) return <div className="rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center text-sm text-slate-500">Nothing here yet.</div>;
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <table className="w-full text-[13px]">
        <thead className="bg-slate-50 text-left text-[11px] uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-3 py-2">Vendor / invoice</th><th>Outcome</th><th>{showQuestion ? "Question · owner" : "Owed · safe"}</th>
            <th className="text-right">Total</th><th className="text-right">Held</th><th className="px-3">Read</th><th className="px-3">When</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-slate-100 hover:bg-slate-50">
              <td className="px-3 py-2">
                <Link href={`/runs/${r.id}`} className="font-medium hover:underline">{r.vendor ?? r.filename}</Link>
                <div className="text-xs text-slate-500">
                  {r.invoice_number ?? "no number"} · <span className="font-mono">{r.scenario ?? r.filename}</span>
                  {r.trigger !== "ingest" && <Badge tone="violet" className="ml-1">{r.trigger.replace("_", " ")}</Badge>}
                  {r.superseded_by && <Badge className="ml-1">superseded</Badge>}
                </div>
              </td>
              <td><OutcomeBadge outcome={r.outcome} sub={r.sub_status} /></td>
              <td className="max-w-md pr-3 text-xs text-slate-600">
                {showQuestion ? (<>{r.question}<div className="text-slate-400">{OWNER[r.owner_role] ?? r.owner_role}{r.sla_due_at && <> · due {new Date(r.sla_due_at).toLocaleString()}</>}</div></>) :
                  r.owed ? <>Owed: {r.owed} · Safe: {r.safe_to_pay}</> : r.status}
              </td>
              <td className="text-right tabular-nums">{money(r.total, r.currency ?? "USD")}</td>
              <td className="text-right tabular-nums">{Number(r.amount_held) > 0 ? money(r.amount_held, r.currency ?? "USD") : "—"}</td>
              <td className="px-3"><Badge tone={r.read_tier === "T1" ? "green" : r.read_tier === "T2" ? "blue" : "slate"}>{r.read_tier ?? "—"}</Badge></td>
              <td className="px-3 text-xs text-slate-500">{ago(r.started_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
