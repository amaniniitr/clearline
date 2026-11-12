"use client";
import { useEffect, useState } from "react";
import { get } from "@/lib/api";
import { PageHeader } from "@/components/shell";
import { RunTable } from "@/components/runtable";

export default function History() {
  const [rows, setRows] = useState<any[]>([]);
  const [q, setQ] = useState("");
  useEffect(() => {
    const f = () => get("/api/runs").then(setRows).catch(() => {});
    f();
    const t = setInterval(f, 4000);
    return () => clearInterval(t);
  }, []);
  const shown = rows.filter((r) => !q || JSON.stringify([r.vendor, r.invoice_number, r.scenario, r.outcome, r.id]).toLowerCase().includes(q.toLowerCase()));
  return (
    <div>
      <PageHeader title="History" sub="Every run, including re-runs triggered by corrections, call-backs and goods receipts."
        right={<input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search vendor, invoice, outcome…" className="h-9 w-72 rounded-lg border border-slate-300 px-3 text-sm" />} />
      <div className="p-6"><RunTable rows={shown} /></div>
    </div>
  );
}
