"use client";
import { useEffect, useState } from "react";
import { get, post } from "@/lib/api";
import { PageHeader } from "@/components/shell";
import { Button, cn } from "@/components/ui";
import { RunTable } from "@/components/runtable";

const TABS = [
  { id: "all", label: "All open" },
  { id: "controller", label: "Payment safety" },
  { id: "awaiting_receipt", label: "Awaiting receipt" },
  { id: "awaiting_vendor", label: "Awaiting vendor" },
];

export default function Queue() {
  const [rows, setRows] = useState<any[]>([]);
  const [tab, setTab] = useState("all");
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    const f = () => get("/api/queue").then(setRows).catch(() => {});
    f();
    const t = setInterval(f, 3000);
    return () => clearInterval(t);
  }, []);
  const shown = rows.filter((r) => tab === "all" || (tab === "controller" ? r.owner_role === "controller" : r.sub_status === tab));
  const hasGrnCase = rows.some((r) => r.sub_status === "awaiting_receipt" && r.scenario === "E2");
  return (
    <div>
      <PageHeader title="Queue" sub="Cases waiting for a person, by due time then dollars held. Each one comes with a single question."
        right={hasGrnCase && (
          <Button variant="outline" onClick={async () => { try { const r = await post("/api/demo/grn-883"); setNote(`GRN-883 posted (+40 boards on PO-4420) → re-matching ${r.rematched_runs.join(", ")}`); } catch (e: any) { setNote(e.message); } }}>
            Simulate: receiving posts GRN-883
          </Button>
        )} />
      <div className="space-y-4 p-6">
        {note && <div className="rounded-lg bg-sky-50 p-3 text-sm text-sky-800">{note}</div>}
        <div className="flex gap-1">
          {TABS.map((t) => (
            <button key={t.id} onClick={() => setTab(t.id)} className={cn("rounded-lg px-3 py-1.5 text-xs font-medium", tab === t.id ? "bg-slate-900 text-white" : "bg-white text-slate-600 ring-1 ring-slate-200")}>
              {t.label} <span className="opacity-60">{rows.filter((r) => t.id === "all" || (t.id === "controller" ? r.owner_role === "controller" : r.sub_status === t.id)).length}</span>
            </button>
          ))}
        </div>
        <RunTable rows={shown} showQuestion />
      </div>
    </div>
  );
}
