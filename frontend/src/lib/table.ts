import type { Cell } from "./types";

export interface GridCell { text: string; rowSpan: number; colSpan: number; header: boolean; numeric: boolean }

/** Build render rows from sparse cells, keeping row/col spans (covered positions are omitted). */
export function toRenderRows(cells: Cell[], nRows: number, nCols: number): GridCell[][] {
  const covered = new Set<string>();
  const byPos = new Map(cells.map((c) => [`${c.row_idx}:${c.col_idx}`, c]));
  const rows: GridCell[][] = [];
  for (let r = 0; r < nRows; r++) {
    const row: GridCell[] = [];
    for (let k = 0; k < nCols; k++) {
      if (covered.has(`${r}:${k}`)) continue;
      const c = byPos.get(`${r}:${k}`);
      const rs = c?.row_span ?? 1;
      const cs = c?.col_span ?? 1;
      for (let i = r; i < r + rs; i++) for (let j = k; j < k + cs; j++) if (i !== r || j !== k) covered.add(`${i}:${j}`);
      row.push({ text: c?.raw_text ?? "", rowSpan: rs, colSpan: cs, header: c?.is_header ?? false, numeric: c?.numeric_value != null });
    }
    rows.push(row);
  }
  return rows;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 ** 2).toFixed(1)} MB`;
}
