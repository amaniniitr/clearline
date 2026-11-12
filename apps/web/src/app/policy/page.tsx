"use client";
import { useEffect, useState } from "react";
import { Lock } from "lucide-react";
import { get } from "@/lib/api";
import { PageHeader } from "@/components/shell";
import { Badge, Card } from "@/components/ui";

const OUT_TONE: Record<string, string> = { HOLD: "amber", REVIEW: "blue", REJECT: "red", NOT_AN_INVOICE: "slate", APPLY_CREDIT: "slate" };
const TOL_LABEL: Record<string, string> = {
  price_pct: "Price variance allowed (share of PO amount)", price_abs: "…or this absolute amount, whichever is larger",
  freight_abs: "Freight allowance per invoice", po_overrun_pct: "PO total may be exceeded by", reconcile_abs: "Rounding allowed when adding up",
  tax_abs: "Rounding allowed on tax", jev_bank_change: "Bank-change request: probability threshold", jev_pressure_level: "Pressure level that holds (2 = strong)",
  jev_instructs: "Text-aimed-at-automation threshold", lookalike_max: "Lookalike sender: max edit distance", bank_change_days: "Recent bank change window (days)",
  apr_limit: "AP approval limit (USD)", pair_p_min: "Line pairing by description: minimum certainty",
};

export default function Policy() {
  const [d, setD] = useState<any>(null);
  const [adv, setAdv] = useState(false);
  useEffect(() => { get("/api/policy").then(setD).catch(() => {}); }, []);
  if (!d) return <div className="p-6 text-sm text-slate-500">Loading…</div>;
  const v = d.versions[0];
  return (
    <div>
      <PageHeader title="Policy" sub={<>The rules are data: a decision table a finance person can read. Active version <b>{v.version}</b>, approved by {v.approved_by}. Evaluated by the GoRules ZEN engine.</>} />
      <div className="grid grid-cols-1 gap-4 p-6 xl:grid-cols-[1fr_380px]">
        <Card title={`Checks (${d.rules.length})`} action={<button className="text-xs text-slate-500 hover:underline" onClick={() => setAdv(!adv)}>{adv ? "Hide" : "Show"} conditions</button>}>
          <ul className="divide-y divide-slate-100">
            {d.rules.map((r: any) => (
              <li key={r.id} className="py-2.5">
                <div className="flex items-center gap-2">
                  <span className="w-14 font-mono text-[11px] text-slate-400">{r.id}</span>
                  <span className="text-sm font-medium">{r.title}</span>
                  {r.locked && <Badge><Lock size={10} /> locked</Badge>}
                </div>
                <ul className="ml-16 mt-1 space-y-0.5">
                  {r.fails.map((f: any, i: number) => (
                    <li key={i} className="text-xs text-slate-600">
                      If not: <Badge tone={OUT_TONE[f.outcome] ?? "slate"}>{f.outcome.replace("_", " ")}</Badge> → {f.owner.replace("_", " ")} · <span className="italic">{f.message.replace(/\{[^}]+\}/g, "…")}</span>
                      {adv && <div className="font-mono text-[10px] text-slate-400">{f.when}</div>}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </Card>
        <div className="space-y-4">
          <Card title="Tolerances & thresholds">
            <table className="w-full text-xs"><tbody>
              {Object.entries(d.tolerances.defaults).map(([k, val]: [string, any]) => (
                <tr key={k} className="border-t border-slate-100"><td className="py-1.5 pr-2 text-slate-600">{TOL_LABEL[k] ?? k}</td><td className="text-right font-mono">{String(val)}</td></tr>
              ))}
            </tbody></table>
            {Object.keys(d.tolerances.vendor_rows).length > 0 && (
              <div className="mt-3 text-xs text-slate-600">Vendor-specific: {Object.entries(d.tolerances.vendor_rows).map(([vid, o]: [string, any]) => `${vid}: ${Object.entries(o).map(([k, x]) => `${k}=${x}`).join(", ")}`).join(" · ")}</div>
            )}
          </Card>
          <Card title="How the policy changes">
            <ol className="list-decimal space-y-1 pl-4 text-xs text-slate-700">
              <li>A reviewer writes guidance in plain English, or Clearline suggests a change from repeated decisions.</li>
              <li>It becomes a typed patch to one table (never raw rules).</li>
              <li>Gates: static checks, the golden scenario suite, and a replay over past invoices showing what would change.</li>
              <li>A different person approves. New runs pin the new version; old runs keep theirs.</li>
            </ol>
            <p className="mt-2 text-[11px] text-slate-500">Locked tables (bank checks, tampering, duplicates, automation-aimed text) can only change through an admin review.</p>
          </Card>
          <Card title="Versions">
            <ul className="text-xs">{d.versions.map((x: any) => <li key={x.version}><b>{x.version}</b> · {x.status} · approved by {x.approved_by} {x.approved_at?.slice(0, 10)}</li>)}</ul>
          </Card>
        </div>
      </div>
    </div>
  );
}
