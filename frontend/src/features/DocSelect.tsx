"use client";
import { useDocuments } from "@/hooks/queries";

export function DocSelect({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const docs = useDocuments();
  const ready = (docs.data?.items ?? []).filter((d) => d.current_version?.status === "ready");
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} aria-label="Document"
      className="w-full max-w-md rounded-md border border-slate-300 bg-white px-3 py-2 text-sm">
      <option value="">Select a document…</option>
      {ready.map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}
    </select>
  );
}
