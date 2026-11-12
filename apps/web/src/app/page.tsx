"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { FileText, Mail, Camera, ScanLine, Upload, RotateCcw, Sparkles } from "lucide-react";
import { get, post, API } from "@/lib/api";
import { Badge, Button, Card, cn } from "@/components/ui";
import { PageHeader } from "@/components/shell";

type Sc = { id: string; title: string; tag: string; story: string; file: string; expected?: string[] };

function kindIcon(file: string) {
  if (file?.endsWith(".eml")) return <Mail size={14} />;
  if (file?.endsWith(".jpg")) return <Camera size={14} />;
  return <FileText size={14} />;
}

export default function Home() {
  const router = useRouter();
  const [data, setData] = useState<{ scenarios: Sc[]; series: Sc[] } | null>(null);
  const [pace, setPace] = useState(350);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    get("/api/scenarios").then(setData).catch((e) => setErr(String(e)));
  }, []);

  async function run(id: string) {
    setBusy(id);
    setErr(null);
    try {
      const r = await post(`/api/scenarios/${id}/run?pace_ms=${pace}`);
      router.push(`/runs/${r.run_id}`);
    } catch (e: any) {
      setErr(e.message);
      setBusy(null);
    }
  }

  async function upload(f: File) {
    setBusy("upload");
    const fd = new FormData();
    fd.append("file", f);
    const r = await fetch(`${API}/api/upload?pace_ms=${pace}`, { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok) {
      setErr(j.detail);
      setBusy(null);
      return;
    }
    router.push(`/runs/${j.run_id}`);
  }

  async function reset() {
    if (!confirm("Reset all demo data (runs, decisions, learned layouts) back to the seed?")) return;
    setBusy("reset");
    await post("/api/admin/reset");
    setBusy(null);
    location.reload();
  }

  const groups = data
    ? [
        { name: "Edge cases", items: data.scenarios.filter((s) => s.id.startsWith("E")) },
        { name: "Happy path & locales", items: data.scenarios.filter((s) => s.id === "H1" || s.id.startsWith("G")) },
        { name: "More checks", items: data.scenarios.filter((s) => s.id.startsWith("B")) },
      ]
    : [];

  return (
    <div>
      <PageHeader
        title="Run an invoice"
        sub="Pick a prepared vendor document or drop your own PDF, photo or .eml. Every step runs for real and streams live."
        right={
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-xs text-slate-500">
              Stage pacing
              <input type="range" min={0} max={1200} step={50} value={pace} onChange={(e) => setPace(+e.target.value)} />
              <span className="w-12 tabular-nums">{pace} ms</span>
            </label>
            <Button variant="outline" size="sm" onClick={reset} disabled={!!busy}><RotateCcw size={13} /> Reset demo</Button>
          </div>
        }
      />
      <div className="space-y-6 p-6">
        {err && <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">{err}</div>}
        <div
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            const f = e.dataTransfer.files[0];
            if (f) upload(f);
          }}
          onClick={() => fileRef.current?.click()}
          className="flex cursor-pointer items-center gap-4 rounded-xl border-2 border-dashed border-slate-300 bg-white p-5 hover:border-slate-400"
        >
          <div className="grid h-11 w-11 place-items-center rounded-lg bg-slate-100"><Upload size={18} /></div>
          <div className="flex-1">
            <div className="text-sm font-medium">Upload a vendor document</div>
            <div className="text-xs text-slate-500">PDF (text or scanned), JPEG/PNG photo, or a forwarded .eml with the invoice attached · max 15 MB</div>
          </div>
          {busy === "upload" && <span className="text-xs text-slate-500">uploading…</span>}
          <input ref={fileRef} type="file" accept=".pdf,.jpg,.jpeg,.png,.eml" className="hidden" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        </div>

        {groups.map((g) => (
          <div key={g.name}>
            <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">{g.name}</h2>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
              {g.items.map((s) => (
                <button key={s.id} onClick={() => run(s.id)} disabled={!!busy}
                  className={cn("group rounded-xl border border-slate-200 bg-white p-4 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-slate-300 hover:shadow", busy === s.id && "ring-2 ring-sky-400")}>
                  <div className="flex items-center gap-2">
                    <span className="rounded-md bg-slate-900 px-1.5 py-0.5 font-mono text-[11px] text-white">{s.id}</span>
                    <span className="text-sm font-semibold">{s.title}</span>
                  </div>
                  <p className="mt-2 text-[13px] leading-snug text-slate-600">{s.story}</p>
                  <div className="mt-3 flex items-center gap-2 text-xs text-slate-500">
                    {kindIcon(s.file)} <span className="font-mono">{s.file}</span>
                    <span className="flex-1" />
                    <Badge>{s.tag}</Badge>
                  </div>
                </button>
              ))}
            </div>
          </div>
        ))}

        {data && <SeriesCard series={data.series} pace={pace} onOpen={(id) => router.push(`/runs/${id}`)} />}
      </div>
    </div>
  );
}

function SeriesCard({ series, onOpen }: { series: Sc[]; pace: number; onOpen: (id: string) => void }) {
  const [log, setLog] = useState<{ id: string; tier?: string; cost?: number; outcome?: string; note?: string; run?: string }[]>([]);
  const [running, setRunning] = useState(false);
  const month = series.filter((s) => s.id.startsWith("S"));
  const l2 = series.find((s) => s.id === "L2");

  async function waitRun(runId: string) {
    for (let i = 0; i < 240; i++) {
      const d = await get(`/api/runs/${runId}`);
      const done = d.events.some((e: any) => e.stage === "learn" && e.type === "stage.finished") || (d.run.status !== "running" && d.run.status !== "completed");
      if (done) return d;
      await new Promise((r) => setTimeout(r, 500));
    }
  }

  async function runAll(items: Sc[]) {
    setRunning(true);
    for (const s of items) {
      setLog((l) => [...l, { id: s.id, note: "running…" }]);
      const r = await post(`/api/scenarios/${s.id}/run?pace_ms=0`);
      const d: any = await waitRun(r.run_id);
      const learn = d?.events.find((e: any) => e.stage === "learn" && e.type === "stage.finished")?.summary;
      setLog((l) => l.map((x) => (x.id === s.id ? { id: s.id, run: r.run_id, tier: d?.run.read_tier, cost: Number(d?.run.cost_usd ?? 0), outcome: d?.proposal?.outcome, note: learn } : x)));
    }
    setRunning(false);
  }

  return (
    <Card title={<span className="flex items-center gap-2"><Sparkles size={14} /> Learning loop: a month of Acme invoices</span>}>
      <p className="text-[13px] text-slate-600">
        Acme bills routine consumables against blanket PO-4499. Run 14 invoices in date order and watch the reading tier move from a
        full model read (T3) to a learned template with a cheap cross-check (T2) to the template alone (T1), with cost falling to $0.
        Then Acme switches templates (L2) and Clearline notices.
      </p>
      <div className="mt-3 flex gap-2">
        <Button onClick={() => runAll(month)} disabled={running}><ScanLine size={14} /> Simulate the month (14 invoices)</Button>
        {l2 && <Button variant="outline" onClick={() => runAll([l2])} disabled={running}>L2 · Acme changes template</Button>}
      </div>
      {log.length > 0 && (
        <table className="mt-4 w-full text-xs">
          <thead className="text-left text-slate-500"><tr><th className="py-1">Invoice</th><th>Outcome</th><th>Read tier</th><th className="text-right">Model cost</th><th className="pl-4">What it learned</th></tr></thead>
          <tbody>
            {log.map((r) => (
              <tr key={r.id} className="border-t border-slate-100">
                <td className="py-1.5 font-mono"><button className="underline-offset-2 hover:underline" onClick={() => r.run && onOpen(r.run)}>{r.id}</button></td>
                <td>{r.outcome?.replace("_", " ") ?? "…"}</td>
                <td><Badge tone={r.tier === "T1" ? "green" : r.tier === "T2" ? "blue" : "slate"}>{r.tier ?? "…"}</Badge></td>
                <td className="text-right tabular-nums">{r.cost !== undefined ? `$${r.cost.toFixed(4)}` : ""}</td>
                <td className="pl-4 text-slate-600">{r.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
