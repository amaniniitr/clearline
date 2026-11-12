export const API = process.env.NEXT_PUBLIC_API ?? "http://localhost:8000";

export async function get<T = any>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

export async function post<T = any>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data?.detail ?? `${r.status}`);
  return data;
}

export function money(v: unknown, currency = "USD"): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  const locale = currency === "INR" ? "en-IN" : currency === "EUR" ? "de-DE" : "en-US";
  try {
    return new Intl.NumberFormat(locale, { style: "currency", currency }).format(n);
  } catch {
    return `${n.toFixed(2)} ${currency}`;
  }
}

export function ago(ts?: string): string {
  if (!ts) return "";
  const s = (Date.now() - new Date(ts).getTime()) / 1000;
  if (s < 60) return `${Math.max(1, Math.round(s))}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export const OUTCOME: Record<string, { label: string; cls: string; dot: string }> = {
  AUTO_APPROVE: { label: "Auto-approved", cls: "bg-emerald-50 text-emerald-800 ring-emerald-200", dot: "bg-emerald-500" },
  REVIEW: { label: "Review", cls: "bg-amber-50 text-amber-800 ring-amber-200", dot: "bg-amber-500" },
  HOLD: { label: "Hold", cls: "bg-orange-50 text-orange-800 ring-orange-200", dot: "bg-orange-500" },
  REJECT: { label: "Reject", cls: "bg-rose-50 text-rose-800 ring-rose-200", dot: "bg-rose-500" },
  NOT_AN_INVOICE: { label: "Not an invoice", cls: "bg-slate-100 text-slate-700 ring-slate-200", dot: "bg-slate-400" },
  APPLY_CREDIT: { label: "Apply credit", cls: "bg-sky-50 text-sky-800 ring-sky-200", dot: "bg-sky-500" },
};

export const ROLES = [
  { id: "ap_clerk", label: "AP clerk", actor: "alex" },
  { id: "ap_manager", label: "AP manager", actor: "sam" },
  { id: "controller", label: "Controller", actor: "priya" },
] as const;

export const STAGES: { id: string; label: string }[] = [
  { id: "intake", label: "Intake" },
  { id: "preprocess", label: "Pre-process" },
  { id: "read", label: "Read" },
  { id: "verify", label: "Verify" },
  { id: "resolve_vendor", label: "Vendor" },
  { id: "resolve_po", label: "PO" },
  { id: "pair", label: "Pair lines" },
  { id: "match", label: "3-way match" },
  { id: "screen", label: "Screen" },
  { id: "judge", label: "Judge" },
  { id: "rules", label: "Rules" },
  { id: "investigate", label: "Investigate" },
  { id: "decide", label: "Decide" },
  { id: "explain", label: "Explain" },
  { id: "commit", label: "Post" },
  { id: "learn", label: "Learn" },
];

export const FIELD_LABEL: Record<string, string> = {
  invoice_number: "Invoice number", invoice_date: "Invoice date", due_date: "Due date", vendor_name: "Vendor",
  vendor_tax_id: "Tax ID", po_number: "PO number", currency: "Currency", subtotal: "Subtotal", tax: "Tax",
  freight: "Freight", total: "Total", payment_terms: "Terms", remit_iban: "IBAN", remit_account_number: "Account no.",
  remit_routing_number: "Routing / IFSC", remit_bank_name: "Bank",
};
