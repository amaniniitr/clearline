"use client";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { OUTCOME } from "@/lib/api";

export function cn(...c: ClassValue[]) {
  return twMerge(clsx(c));
}

export function Card({ className, children, title, action }: { className?: string; children: React.ReactNode; title?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <section className={cn("rounded-xl border border-slate-200 bg-white shadow-sm", className)}>
      {(title || action) && (
        <header className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
          <h3 className="text-[13px] font-semibold text-slate-800">{title}</h3>
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Badge({ className, children, tone = "slate" }: { className?: string; children: React.ReactNode; tone?: string }) {
  const tones: Record<string, string> = {
    slate: "bg-slate-100 text-slate-700 ring-slate-200",
    green: "bg-emerald-50 text-emerald-800 ring-emerald-200",
    amber: "bg-amber-50 text-amber-800 ring-amber-200",
    red: "bg-rose-50 text-rose-800 ring-rose-200",
    blue: "bg-sky-50 text-sky-800 ring-sky-200",
    violet: "bg-violet-50 text-violet-800 ring-violet-200",
  };
  return <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset", tones[tone], className)}>{children}</span>;
}

export function OutcomeBadge({ outcome, sub, className }: { outcome?: string | null; sub?: string | null; className?: string }) {
  if (!outcome) return <Badge>running…</Badge>;
  const o = OUTCOME[outcome] ?? { label: outcome, cls: "bg-slate-100 text-slate-700 ring-slate-200", dot: "bg-slate-400" };
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset", o.cls, className)}>
      <span className={cn("h-1.5 w-1.5 rounded-full", o.dot)} />
      {o.label}
      {sub ? <span className="font-normal opacity-80">· {sub.replace("_", " ")}</span> : null}
    </span>
  );
}

export function Button({ className, variant = "default", size = "md", ...p }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "default" | "outline" | "ghost" | "danger" | "success"; size?: "sm" | "md" }) {
  const v = {
    default: "bg-slate-900 text-white hover:bg-slate-800",
    outline: "border border-slate-300 bg-white text-slate-800 hover:bg-slate-50",
    ghost: "text-slate-700 hover:bg-slate-100",
    danger: "bg-rose-600 text-white hover:bg-rose-500",
    success: "bg-emerald-600 text-white hover:bg-emerald-500",
  }[variant];
  const s = size === "sm" ? "h-7 px-2.5 text-xs" : "h-9 px-3.5 text-sm";
  return <button className={cn("inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition disabled:cursor-not-allowed disabled:opacity-50", v, s, className)} {...p} />;
}

export function Stat({ label, value, hint, tone }: { label: string; value: React.ReactNode; hint?: React.ReactNode; tone?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className={cn("mt-1 text-2xl font-semibold tabular-nums text-slate-900", tone)}>{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

export function Evidence({ level }: { level: string }) {
  const m: Record<string, [string, string]> = {
    verified: ["Verified", "green"],
    read: ["Read", "blue"],
    uncertain: ["Uncertain", "amber"],
    absent: ["Not printed", "slate"],
  };
  const [l, t] = m[level] ?? [level, "slate"];
  return <Badge tone={t}>{l}</Badge>;
}

export function StatusDot({ status }: { status?: string | null }) {
  const c: Record<string, string> = {
    pass: "bg-emerald-500", warn: "bg-amber-500", block: "bg-rose-500", running: "bg-sky-500 animate-pulse",
    degraded: "bg-violet-500", skipped: "bg-slate-300", error: "bg-rose-700",
  };
  return <span className={cn("inline-block h-2 w-2 shrink-0 rounded-full", c[status ?? ""] ?? "bg-slate-300")} />;
}
