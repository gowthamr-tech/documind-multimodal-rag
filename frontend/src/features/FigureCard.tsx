import { Badge } from "@/components/ui";
import { fileUrl } from "@/lib/api";
import type { FigureInfo } from "@/lib/types";

export function FigureCard({ docId, f }: { docId: string; f: FigureInfo }) {
  const v = f.validation;
  return (
    <div className="grid gap-4 rounded-lg border border-slate-200 bg-white p-4 md:grid-cols-2">
      <div>
        {f.has_image && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={fileUrl(`documents/${docId}/charts/${f.id}/image`)} alt={f.caption ?? "Figure"} className="w-full rounded border border-slate-100" />
        )}
        <p className="mt-2 text-sm font-medium">{f.caption ?? "Untitled figure"}</p>
        <p className="text-xs text-slate-500">Page {f.page_no ?? "—"} · {f.classifier_label ?? f.figure_type}
          {f.classifier_confidence != null && ` (${(f.classifier_confidence * 100).toFixed(0)}%)`}</p>
      </div>
      <div className="space-y-2 text-sm">
        <div className="flex flex-wrap gap-2">
          <Badge tone={f.analysis_status === "succeeded" ? "ok" : f.analysis_status === "failed" ? "bad" : "neutral"}>Analysis: {f.analysis_status}</Badge>
          {f.chart && <Badge tone={f.values_are_estimated ? "warn" : "ok"}>{f.values_are_estimated ? "Values estimated" : "Values OCR-verified"}</Badge>}
          {v?.points_total != null && <Badge>{v.points_verified}/{v.points_total} points matched by OCR</Badge>}
        </div>
        {f.error && <p className="text-xs text-slate-500">{f.error}</p>}
        {f.description && <p className="text-slate-700">{f.description}</p>}
        {f.chart?.series.map((s) => (
          <div key={s.name}>
            <p className="font-medium">{s.name}</p>
            <ul className="grid grid-cols-2 gap-x-4 text-xs text-slate-600">
              {s.points.map((p) => <li key={p.x}>{p.x}: {p.y ?? "n/a"}{!p.value_label_visible && " (est.)"}</li>)}
            </ul>
          </div>
        ))}
        {f.chart?.trends.length ? <ul className="list-disc pl-5 text-slate-700">{f.chart.trends.map((t) => <li key={t}>{t}</li>)}</ul> : null}
      </div>
    </div>
  );
}
