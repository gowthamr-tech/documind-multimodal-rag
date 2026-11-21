"""Build chunking `Block`s from a version's persisted Document Graph.

Tables become a markdown block plus per-row flattened strings ("Cloud Services | Fiscal year /
2024: 328.6 (USD millions)") so row-level questions retrieve the right row. Figures become text
from caption + VLM analysis; chart values are labelled as verified or estimated.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chunking.base import Block, BlockKind
from app.database.models import Element, ExtractedTable, Figure, TableCell

_SKIP_TYPES = {"table", "figure"}


def _fmt_scale(unit: str | None, scale: int | None) -> str:
    words = {1_000: "thousands", 1_000_000: "millions", 1_000_000_000: "billions"}
    if unit == "%":
        return "%"
    parts = [p for p in (unit, words.get(int(scale)) if scale else None) if p]
    return " ".join(parts)


def flatten_rows(table: ExtractedTable, cells: list[TableCell]) -> tuple[str, list[str]]:
    header = " | ".join(
        " / ".join(
            dict.fromkeys(
                c.raw_text for c in cells if c.is_header and c.col_idx == k and c.raw_text
            )
        )
        for k in range(table.n_cols)
    )
    rows: dict[int, list[TableCell]] = {}
    for c in cells:
        if c.row_idx >= table.header_depth:
            rows.setdefault(c.row_idx, []).append(c)
    out = []
    for r in sorted(rows):
        row = sorted(rows[r], key=lambda c: c.col_idx)
        label = next((c.raw_text for c in row if c.col_idx == 0 and c.raw_text), "")
        vals = []
        for c in row:
            if c.col_idx == 0 or not c.raw_text:
                continue
            head = " / ".join(c.header_path) or f"col {c.col_idx}"
            suffix = _fmt_scale(c.unit, c.scale)
            vals.append(
                f"{head}: {c.raw_text}" + (f" ({suffix})" if suffix and suffix != "%" else "")
            )
        if label or vals:
            out.append(f"{label} | " + "; ".join(vals) if label else "; ".join(vals))
    return header, out


def figure_text(f: Figure) -> str:
    lines = []
    if f.caption:
        lines.append(f.caption)
    kind = f.classifier_label or f.figure_type
    lines.append(f"[{f.figure_type}: {kind}]")
    if f.embedded_text:
        lines.append("Text in figure: " + "; ".join(f.embedded_text))
    if f.description:
        lines.append(f.description)
    chart: dict[str, Any] | None = f.chart
    if chart and chart.get("series"):
        verified = {(p["series"], p["x"]) for p in (f.validation or {}).get("verified_points", [])}
        for s in chart["series"]:
            pts = []
            for p in s.get("points", []):
                if p.get("y") is None:
                    continue
                tag = "" if (s["name"], p["x"]) in verified else " (est.)"
                pts.append(f"{p['x']}={p['y']}{tag}")
            if pts:
                lines.append(f"Series {s['name']}: " + ", ".join(pts))
        if chart.get("trends"):
            lines.append("Trends: " + " ".join(chart["trends"]))
    return "\n".join(lines)


def build_blocks(session: Session, version_id: uuid.UUID) -> list[Block]:
    elements = (
        session.execute(
            select(Element)
            .where(Element.document_version_id == version_id)
            .order_by(Element.reading_order)
        )
        .scalars()
        .all()
    )
    tables = {
        t.element_ref: t
        for t in session.execute(
            select(ExtractedTable).where(ExtractedTable.document_version_id == version_id)
        ).scalars()
    }
    figures = {
        f.element_ref: f
        for f in session.execute(
            select(Figure).where(Figure.document_version_id == version_id)
        ).scalars()
    }
    cells_by_table: dict[uuid.UUID, list[TableCell]] = {}
    if tables:
        for c in session.execute(
            select(TableCell).where(TableCell.table_id.in_([t.id for t in tables.values()]))
        ).scalars():
            cells_by_table.setdefault(c.table_id, []).append(c)

    blocks: list[Block] = []
    for e in elements:
        if e.is_furniture:
            continue
        if e.type == "table" and e.ref in tables:
            t = tables[e.ref]
            header, rows = flatten_rows(t, cells_by_table.get(t.id, []))
            blocks.append(
                Block(
                    kind=BlockKind.TABLE,
                    text=t.markdown,
                    page_no=t.page_start,
                    page_end=t.page_end,
                    bbox=t.bbox,
                    section_path=list(t.section_path),
                    element_ref=e.ref,
                    table_header=header,
                    table_rows=rows,
                    extra={"caption": t.caption, "table_id": str(t.id), "units": t.units},
                )
            )
            continue
        if e.type == "figure" and e.ref in figures:
            f = figures[e.ref]
            blocks.append(
                Block(
                    kind=BlockKind.FIGURE,
                    text=figure_text(f),
                    page_no=f.page_no,
                    bbox=f.bbox,
                    section_path=list(f.section_path),
                    element_ref=e.ref,
                    extra={
                        "figure_type": f.figure_type,
                        "figure_id": str(f.id),
                        "values_are_estimated": f.values_are_estimated,
                    },
                )
            )
            continue
        if e.type in _SKIP_TYPES or e.parent_ref is not None or not e.text.strip():
            continue  # captions/footnotes/in-figure text are folded into their table/figure block
        kind = BlockKind.HEADING if e.type in {"title", "section_header"} else BlockKind.TEXT
        blocks.append(
            Block(
                kind=kind,
                text=e.text,
                page_no=e.page_no,
                bbox=e.bbox,
                section_path=list(e.section_path),
                element_ref=e.ref,
            )
        )
    return blocks
