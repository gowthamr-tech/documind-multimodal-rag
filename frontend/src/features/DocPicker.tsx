"use client";
import { useDocuments } from "@/hooks/queries";
import { Spinner } from "@/components/ui";

export function DocPicker({ selected, onChange, min = 0 }: { selected: string[]; onChange: (ids: string[]) => void; min?: number }) {
  const docs = useDocuments();
  const ready = (docs.data?.items ?? []).filter((d) => d.current_version?.status === "ready");
  if (docs.isLoading) return <Spinner label="Loading documents" />;
  if (!ready.length) return <p className="text-sm text-slate-500">No processed documents yet.</p>;
  const toggle = (id: string) => onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  return (
    <fieldset className="space-y-1">
      <legend className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
        Documents {min > 0 ? `(select at least ${min})` : "(none selected = search all)"}
      </legend>
      {ready.map((d) => (
        <label key={d.id} className="flex cursor-pointer items-start gap-2 rounded px-1 py-1 text-sm hover:bg-slate-50">
          <input type="checkbox" checked={selected.includes(d.id)} onChange={() => toggle(d.id)} className="mt-0.5" />
          <span className="min-w-0 break-words">{d.title}{d.sensitivity === "confidential" && <span className="ml-1 text-xs text-warn">(confidential)</span>}</span>
        </label>
      ))}
    </fieldset>
  );
}
