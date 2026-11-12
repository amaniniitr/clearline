import { PageHeader } from "@/components/shell";

const STEPS = [
  { t: "1 · It arrives", d: "By email or upload: a PDF, a scan, or a phone photo. Clearline keeps the original, notes who sent it, and spots re-deliveries of the same file." },
  { t: "2 · It reads the invoice twice", d: "Two independent readings: one looks at the page like a person, the other reads the text line by line and points to where each value is printed. If they disagree, the arithmetic decides, and the field is marked uncertain if it can't." },
  { t: "3 · It checks the page itself", d: "For bank details and the total, Clearline compares what is visibly printed with the file's hidden text, which catches doctored PDFs." },
  { t: "4 · It checks it against what was ordered and received", d: "Finds the vendor and the purchase order (or infers it when none is printed), pairs each line, and compares price and quantity with the PO and the goods received, including what earlier invoices already billed." },
  { t: "5 · It checks it is safe to pay", d: "Is the bank account the one we verified? Is the sender really the vendor? Is anyone pushing to skip checks, or is there text trying to instruct an automated system? Is it a duplicate?" },
  { t: "6 · It applies your policy", d: "Your rules and tolerances, written as a readable table. Every check is recorded, including the ones that passed." },
  { t: "7 · It investigates anything odd", d: "For exceptions, an assistant looks through POs, receipts and history to find an explanation. It can only read, and its findings are re-checked before they count." },
  { t: "8 · It decides, or asks one clear question", d: "Clean invoices are approved and posted. Otherwise the right person gets one specific question, what was already tried, and a recommended action. A payment can only be released by the rules, never by the AI alone." },
  { t: "9 · It learns", d: "Repeat vendors get cheaper and faster to read, confirmed answers are remembered, and repeated decisions become suggested rule changes for a person to approve." },
];

export default function How() {
  return (
    <div>
      <PageHeader title="How it works" sub="For the people who own the process. No jargon." />
      <div className="mx-auto max-w-4xl space-y-6 p-6">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {STEPS.map((s) => (
            <div key={s.t} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="text-sm font-semibold">{s.t}</div>
              <p className="mt-1.5 text-[13px] leading-relaxed text-slate-600">{s.d}</p>
            </div>
          ))}
        </div>
        <div className="rounded-xl border border-slate-200 bg-white p-5 text-[13px] leading-relaxed text-slate-700 shadow-sm">
          <div className="mb-2 text-sm font-semibold">Two questions on every invoice</div>
          <p><b>Do we owe this?</b> Is it a real invoice for something we ordered and received, at the agreed price, not already paid?</p>
          <p className="mt-1"><b>Is it safe to pay where they ask?</b> Is the money going to the account we verified with this vendor, and was it really sent by them?</p>
          <p className="mt-3">An invoice can be owed but unsafe (a perfect match with changed bank details is the classic fraud), or safe but not yet owed (goods still in transit). Clearline answers both separately, so the right person handles each.</p>
        </div>
        <div className="rounded-xl border border-slate-200 bg-white p-5 text-[13px] leading-relaxed text-slate-700 shadow-sm">
          <div className="mb-2 text-sm font-semibold">How sure is it?</div>
          <p><b className="text-emerald-700">Verified</b>: found on the page and confirmed another way (both readings agree, the numbers add up, or it matches our records). <b className="text-sky-700">Read</b>: found on the page only. <b className="text-amber-700">Uncertain</b>: not found, the readings disagree, or it failed a check. An uncertain value that matters never gets paid automatically. It becomes the question.</p>
        </div>
      </div>
    </div>
  );
}
