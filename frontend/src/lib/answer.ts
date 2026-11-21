// Safe rendering model for answers: text is never injected as HTML. Paragraphs, bullets, **bold**
// and citation markers like [S2] / [S1, S3] are parsed into typed segments.
export type Segment = { kind: "text"; text: string } | { kind: "bold"; text: string } | { kind: "cite"; id: string };
export type Block = { kind: "p"; segments: Segment[] } | { kind: "ul"; items: Segment[][] };

const INLINE = /(\*\*[^*]+\*\*|\[(?:S\d+)(?:\s*,\s*S\d+)*\])/g;

export function parseInline(text: string): Segment[] {
  const out: Segment[] = [];
  let last = 0;
  for (const m of text.matchAll(INLINE)) {
    if (m.index! > last) out.push({ kind: "text", text: text.slice(last, m.index) });
    const tok = m[0];
    if (tok.startsWith("**")) out.push({ kind: "bold", text: tok.slice(2, -2) });
    else for (const id of tok.slice(1, -1).split(",")) out.push({ kind: "cite", id: id.trim() });
    last = m.index! + tok.length;
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) });
  return out;
}

export function parseAnswer(md: string): Block[] {
  const blocks: Block[] = [];
  for (const para of md.trim().split(/\n{2,}/)) {
    const lines = para.split("\n").filter((l) => l.trim());
    if (lines.length && lines.every((l) => /^\s*[-*]\s+/.test(l))) {
      blocks.push({ kind: "ul", items: lines.map((l) => parseInline(l.replace(/^\s*[-*]\s+/, ""))) });
    } else if (lines.length) {
      blocks.push({ kind: "p", segments: parseInline(lines.join(" ")) });
    }
  }
  return blocks;
}

/** Normalised [x0,y0,x1,y1] -> CSS percentages for an absolutely positioned overlay. */
export function bboxStyle(b: number[]): { left: string; top: string; width: string; height: string } {
  const clamp = (v: number | undefined) => Math.min(1, Math.max(0, v ?? 0));
  const [x0, y0, x1, y1] = [clamp(b[0]), clamp(b[1]), clamp(b[2]), clamp(b[3])];
  const pct = (v: number) => `${(v * 100).toFixed(2)}%`;
  return { left: pct(x0), top: pct(y0), width: pct(Math.max(0, x1 - x0)), height: pct(Math.max(0, y1 - y0)) };
}

/** Link target for a citation: the viewer page with the evidence highlighted. */
export function citationHref(c: { document_id: string; page: number | null; bbox: number[] | null; bbox_page?: number | null }): string {
  const page = c.bbox_page ?? c.page ?? 1;
  const q = new URLSearchParams({ page: String(page) });
  if (c.bbox) q.set("hl", c.bbox.map((v) => v.toFixed(4)).join(","));
  return `/documents/${c.document_id}?${q}`;
}

export function parseHighlight(hl: string | null): number[] | null {
  if (!hl) return null;
  const v = hl.split(",").map(Number);
  return v.length === 4 && v.every((n) => Number.isFinite(n) && n >= 0 && n <= 1) ? v : null;
}
