import { toRenderRows } from "@/lib/table";
import type { TableInfo } from "@/lib/types";

// Rendered from structured cells (not backend HTML) — spans preserved, no HTML injection.
export function TableView({ t }: { t: TableInfo }) {
  const rows = toRenderRows(t.cells, t.n_rows, t.n_cols);
  return (
    <figure className="space-y-2">
      {t.caption && <figcaption className="text-sm font-medium text-slate-800">{t.caption}</figcaption>}
      <div className="overflow-x-auto">
        <table className="min-w-full border-collapse text-sm">
          <tbody>
            {rows.map((row, r) => (
              <tr key={r}>
                {row.map((c, k) => {
                  const Tag = c.header ? "th" : "td";
                  return (
                    <Tag key={k} rowSpan={c.rowSpan} colSpan={c.colSpan}
                      className={`border border-slate-200 px-2 py-1 ${c.header ? "bg-slate-50 text-left font-semibold" : ""} ${c.numeric ? "text-right tabular-nums" : ""}`}>
                      {c.text}
                    </Tag>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-500">
        {t.units && <>Units: {t.units} · </>}
        Page {t.page_start}{t.page_end && t.page_end !== t.page_start ? `–${t.page_end}` : ""}
        {t.stitched_from > 1 && <> · merged from {t.stitched_from} page segments</>}
        {t.section_path.length > 0 && <> · {t.section_path.join(" › ")}</>}
      </p>
      {t.footnotes.map((f) => <p key={f} className="text-xs text-slate-500">{f}</p>)}
    </figure>
  );
}
