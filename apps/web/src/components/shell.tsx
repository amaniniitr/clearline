"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense, createContext, useContext, useEffect, useState } from "react";
import { Activity, BookOpen, Brain, Building2, Inbox, LayoutDashboard, PlayCircle, Scale, History } from "lucide-react";
import { ROLES, get } from "@/lib/api";
import { cn } from "./ui";

type Role = (typeof ROLES)[number];
const RoleCtx = createContext<{ role: Role; setRole: (r: Role) => void }>({ role: ROLES[0], setRole: () => {} });
export const useRole = () => useContext(RoleCtx);

const NAV = [
  { href: "/", label: "Run an invoice", icon: PlayCircle },
  { href: "/queue", label: "Queue", icon: Inbox },
  { href: "/history", label: "History", icon: History },
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/learning", label: "Learning", icon: Brain },
  { href: "/vendors", label: "Vendors & POs", icon: Building2 },
  { href: "/policy", label: "Policy", icon: Scale },
  { href: "/how", label: "How it works", icon: BookOpen },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const [role, setRoleState] = useState<Role>(ROLES[0]);
  const [health, setHealth] = useState<any>(null);
  const [queue, setQueue] = useState<number | null>(null);
  useEffect(() => {
    const saved = typeof window !== "undefined" ? localStorage.getItem("role") : null;
    const r = ROLES.find((x) => x.id === saved);
    if (r) setRoleState(r);
    get("/api/health").then(setHealth).catch(() => setHealth({ ok: false }));
    const t = setInterval(() => get("/api/queue").then((q) => setQueue(q.length)).catch(() => {}), 4000);
    get("/api/queue").then((q) => setQueue(q.length)).catch(() => {});
    return () => clearInterval(t);
  }, []);
  const setRole = (r: Role) => {
    setRoleState(r);
    localStorage.setItem("role", r.id);
  };
  return (
    <RoleCtx.Provider value={{ role, setRole }}>
      <div className="flex min-h-screen bg-slate-50 text-slate-900">
        <aside className="sticky top-0 flex h-screen w-56 shrink-0 flex-col border-r border-slate-200 bg-white">
          <div className="flex items-center gap-2 px-4 py-4">
            <div className="grid h-8 w-8 place-items-center rounded-lg bg-slate-900 text-white"><Activity size={16} /></div>
            <div>
              <div className="text-sm font-semibold leading-tight">Clearline</div>
              <div className="text-[11px] text-slate-500">AP invoice processing</div>
            </div>
          </div>
          <Suspense fallback={<nav className="flex-1" />}>
            <Nav queue={queue} />
          </Suspense>
          <div className="space-y-2 border-t border-slate-100 p-3">
            <label className="block text-[10px] font-medium uppercase tracking-wide text-slate-500">Acting as (demo)</label>
            <select value={role.id} onChange={(e) => setRole(ROLES.find((r) => r.id === e.target.value)!)} className="w-full rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs">
              {ROLES.map((r) => (
                <option key={r.id} value={r.id}>{r.label} ({r.actor})</option>
              ))}
            </select>
            <div className="text-[10px] leading-snug text-slate-500">
              {health?.ok ? (
                <>Models: {health.models?.read_a} + {health.models?.read_b} · {health.mode} mode{health.jev ? " · Jev" : ""}</>
              ) : health ? <span className="text-rose-600">API offline</span> : "…"}
            </div>
          </div>
        </aside>
        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </RoleCtx.Provider>
  );
}


function Nav({ queue }: { queue: number | null }) {
  const path = usePathname();
  return (
    <nav className="flex-1 space-y-0.5 px-2">
      {NAV.map(({ href, label, icon: I }) => {
        const active = href === "/" ? path === "/" || path.startsWith("/runs") : path.startsWith(href);
        return (
          <Link key={href} href={href} className={cn("flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] font-medium", active ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100")}>
            <I size={15} />
            <span className="flex-1">{label}</span>
            {href === "/queue" && queue ? <span className={cn("rounded-full px-1.5 text-[10px]", active ? "bg-white/20" : "bg-orange-100 text-orange-700")}>{queue}</span> : null}
          </Link>
        );
      })}
    </nav>
  );
}

export function PageHeader({ title, sub, right }: { title: string; sub?: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="flex items-end justify-between gap-4 border-b border-slate-200 bg-white px-6 py-4">
      <div>
        <h1 className="text-lg font-semibold">{title}</h1>
        {sub && <p className="mt-0.5 text-sm text-slate-500">{sub}</p>}
      </div>
      {right}
    </div>
  );
}
