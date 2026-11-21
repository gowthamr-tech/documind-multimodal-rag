import { describe, expect, it } from "vitest";
import { bboxStyle, citationHref, parseAnswer, parseHighlight, parseInline } from "./answer";
import { createSseParser, type SseEvent } from "./sse";
import { toRenderRows } from "./table";
import type { Cell } from "./types";

describe("sse parser", () => {
  it("handles events split across chunks and multi-line data", () => {
    const got: SseEvent[] = [];
    const feed = createSseParser((e) => got.push(e));
    feed("event: agent_step\ndata: {\"node\":");
    feed("\"retrieve\"}\n\nevent: final\r\ndata: line1\r\ndata: line2\r\n\r\n: comment\n\n");
    expect(got).toEqual([
      { event: "agent_step", data: '{"node":"retrieve"}' },
      { event: "final", data: "line1\nline2" },
    ]);
  });
});

describe("answer parsing", () => {
  it("turns citation markers into cite segments and never into HTML", () => {
    expect(parseInline("Revenue was **USD 1.5bn** [S1, S3]. <script>x</script>")).toEqual([
      { kind: "text", text: "Revenue was " },
      { kind: "bold", text: "USD 1.5bn" },
      { kind: "text", text: " " },
      { kind: "cite", id: "S1" },
      { kind: "cite", id: "S3" },
      { kind: "text", text: ". <script>x</script>" },
    ]);
  });
  it("detects bullet lists and paragraphs", () => {
    const blocks = parseAnswer("Intro [S1].\n\n- a [S2]\n- b");
    expect(blocks.map((b) => b.kind)).toEqual(["p", "ul"]);
  });
});

describe("viewer helpers", () => {
  it("maps bbox to percentages and clamps", () => {
    expect(bboxStyle([0.1, 0.2, 0.5, 1.2])).toEqual({ left: "10.00%", top: "20.00%", width: "40.00%", height: "80.00%" });
  });
  it("builds citation links and validates highlights", () => {
    const href = citationHref({ document_id: "d1", page: 2, bbox: [0.1, 0.2, 0.3, 0.4] });
    expect(href).toBe("/documents/d1?page=2&hl=0.1000%2C0.2000%2C0.3000%2C0.4000");
    expect(parseHighlight("0.1,0.2,0.3,0.4")).toEqual([0.1, 0.2, 0.3, 0.4]);
    expect(parseHighlight("1,2,javascript:alert(1)")).toBeNull();
  });
});

describe("table rendering", () => {
  it("keeps spans and omits covered positions", () => {
    const c = (r: number, k: number, text: string, rs = 1, cs = 1, h = false): Cell => ({
      row_idx: r, col_idx: k, row_span: rs, col_span: cs, is_header: h, header_path: [], row_path: [],
      raw_text: text, numeric_value: null, unit: null, scale: null,
    });
    const rows = toRenderRows([c(0, 0, "Segment", 2, 1, true), c(0, 1, "Fiscal year", 1, 2, true),
      c(1, 1, "2023", 1, 1, true), c(1, 2, "2024", 1, 1, true), c(2, 0, "Cloud"), c(2, 1, "1"), c(2, 2, "2")], 3, 3);
    expect(rows.map((r) => r.map((x) => x.text))).toEqual([["Segment", "Fiscal year"], ["2023", "2024"], ["Cloud", "1", "2"]]);
    expect(rows[0]![1]!.colSpan).toBe(2);
  });
});
