"""Table and Chart analysis agents.

Deterministic by design: they turn retrieved table/chart evidence into *facts* with exact
provenance (source id, table caption, row/column path, value, unit, scale, page) by reading the
persisted cells/chart data — not by asking an LLM to re-read markdown. The reasoning agent then
works over these facts, and every operand it uses can be traced back to a cell.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Chunk, ExtractedTable, Figure, TableCell

_WORD = re.compile(r"[a-z0-9][a-z0-9\-]+")


@dataclass(slots=True)
class Fact:
    fact_id: str
    source_id: str
    kind: str  # "table_cell" | "chart_point"
    label: str  # human-readable path, e.g. "Table 1 > Cloud Services > Fiscal year / 2024"
    value: str
    unit: str | None
    scale: int | None
    page: int | None
    document_title: str
    estimated: bool
    relevance: float


def _terms(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _relevance(q: set[str], label: str) -> float:
    lt = _terms(label)
    return len(q & lt) / max(1, len(q)) if q else 0.0


async def analyze_tables(
    session: AsyncSession, question: str, evidence: list[dict[str, Any]], max_facts: int = 80
) -> list[Fact]:
    q = _terms(question)
    facts: list[Fact] = []
    table_sources = [e for e in evidence if e["chunk_type"] in {"table", "table_rows"}]
    for ev in table_sources:
        chunk = await session.get(Chunk, uuid.UUID(ev["chunk_id"]))
        if chunk is None:
            continue
        tables = (
            (
                await session.execute(
                    select(ExtractedTable).where(
                        ExtractedTable.document_version_id == chunk.document_version_id,
                        ExtractedTable.element_ref.in_(chunk.element_refs),
                    )
                )
            )
            .scalars()
            .all()
        )
        rows_wanted: tuple[int, int] | None = None
        if "row_start" in chunk.metadata_:
            rows_wanted = (int(chunk.metadata_["row_start"]), int(chunk.metadata_["row_end"]))
        for t in tables:
            cells = (
                (
                    await session.execute(
                        select(TableCell)
                        .where(TableCell.table_id == t.id, TableCell.numeric_value.is_not(None))
                        .order_by(TableCell.row_idx, TableCell.col_idx)
                    )
                )
                .scalars()
                .all()
            )
            for c in cells:
                body_row = c.row_idx - t.header_depth
                if rows_wanted and not (rows_wanted[0] <= body_row <= rows_wanted[1]):
                    continue
                label = " > ".join(
                    filter(
                        None,
                        [t.caption or "Table", " / ".join(c.row_path), " / ".join(c.header_path)],
                    )
                )
                facts.append(
                    Fact(
                        fact_id="",
                        source_id=ev["source_id"],
                        kind="table_cell",
                        label=label,
                        value=str(
                            c.numeric_value.normalize()
                            if c.numeric_value is not None
                            else c.raw_text
                        ),
                        unit=c.unit,
                        scale=int(c.scale) if c.scale else None,
                        page=t.page_start,
                        document_title=ev["document_title"],
                        estimated=False,
                        relevance=_relevance(q, label),
                    )
                )
    return _finalise(facts, max_facts, prefix="T")


async def analyze_charts(
    session: AsyncSession, question: str, evidence: list[dict[str, Any]], max_facts: int = 60
) -> list[Fact]:
    q = _terms(question)
    facts: list[Fact] = []
    for ev in [e for e in evidence if e["chunk_type"] == "chart"]:
        chunk = await session.get(Chunk, uuid.UUID(ev["chunk_id"]))
        if chunk is None:
            continue
        figs = (
            (
                await session.execute(
                    select(Figure).where(
                        Figure.document_version_id == chunk.document_version_id,
                        Figure.element_ref.in_(chunk.element_refs),
                    )
                )
            )
            .scalars()
            .all()
        )
        for f in figs:
            chart = f.chart or {}
            verified = {
                (p["series"], p["x"]) for p in (f.validation or {}).get("verified_points", [])
            }
            unit = (chart.get("y_axis") or {}).get("unit")
            for s in chart.get("series", []):
                for p in s.get("points", []):
                    if p.get("y") is None:
                        continue
                    label = f"{f.caption or 'Chart'} > {s['name']} > {p['x']}"
                    facts.append(
                        Fact(
                            fact_id="",
                            source_id=ev["source_id"],
                            kind="chart_point",
                            label=label,
                            value=str(p["y"]),
                            unit=unit,
                            scale=None,
                            page=f.page_no,
                            document_title=ev["document_title"],
                            estimated=(s["name"], p["x"]) not in verified,
                            relevance=_relevance(q, label),
                        )
                    )
    return _finalise(facts, max_facts, prefix="C")


def _finalise(facts: list[Fact], limit: int, prefix: str) -> list[Fact]:
    facts.sort(key=lambda f: -f.relevance)  # stable: keeps table order within equal relevance
    kept = facts[:limit]
    for i, f in enumerate(kept, start=1):
        f.fact_id = f"{prefix}{i}"
    return kept


def facts_as_dicts(facts: list[Fact]) -> list[dict[str, Any]]:
    return [asdict(f) for f in facts]


def format_facts(facts: list[dict[str, Any]]) -> str:
    lines = []
    for f in facts:
        unit = " ".join(
            p
            for p in (
                f["unit"],
                {1000: "thousands", 10**6: "millions", 10**9: "billions"}.get(f["scale"] or 0),
            )
            if p
        )
        est = " (ESTIMATED from chart)" if f["estimated"] else ""
        lines.append(
            f"{f['fact_id']} [{f['source_id']}] {f['label']} = {f['value']}{(' ' + unit) if unit else ''}{est} (p. {f['page']})"
        )
    return "\n".join(lines)
