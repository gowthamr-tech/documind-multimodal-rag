"""Persist a DocumentGraph. Replaces a version's derived rows in one transaction, so re-running
the pipeline is idempotent and readers never see a half-written version."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from app.database.models import Element, ExtractedTable, Figure, Page, TableCell
from app.database.models.extraction import FigureAnalysisStatus
from app.extraction.models import DocumentGraph, FigureType
from app.extraction.tables.normalize import to_html, to_markdown


def replace_graph(
    session: Session,
    *,
    org_id: uuid.UUID,
    version_id: uuid.UUID,
    graph: DocumentGraph,
    page_image_keys: dict[int, str],
    figure_crop_keys: dict[str, str],
    extractor: str = "docling",
) -> dict[str, int]:
    for model in (Figure, ExtractedTable, Element, Page):  # table_cells cascade from tables
        session.execute(delete(model).where(model.document_version_id == version_id))

    if graph.pages:
        session.execute(
            insert(Page),
            [
                {
                    "id": uuid.uuid4(),
                    "org_id": org_id,
                    "document_version_id": version_id,
                    "page_no": p.page_no,
                    "width": p.width,
                    "height": p.height,
                    "has_text_layer": p.has_text_layer,
                    "ocr_applied": p.ocr_applied,
                    "image_key": page_image_keys.get(p.page_no),
                }
                for p in graph.pages
            ],
        )
    if graph.elements:
        session.execute(
            insert(Element),
            [
                {
                    "id": uuid.uuid4(),
                    "org_id": org_id,
                    "document_version_id": version_id,
                    "ref": e.ref,
                    "type": e.type.value,
                    "reading_order": e.reading_order,
                    "page_no": e.page_no,
                    "bbox": list(e.bbox) if e.bbox else None,
                    "text": e.text,
                    "section_path": e.section_path,
                    "parent_ref": e.parent_ref,
                    "is_furniture": e.is_furniture,
                    "attributes": e.attributes,
                }
                for e in graph.elements
            ],
        )

    cell_rows: list[dict[str, Any]] = []
    for t in graph.tables:
        table_id = uuid.uuid4()
        session.execute(
            insert(ExtractedTable).values(
                id=table_id,
                org_id=org_id,
                document_version_id=version_id,
                element_ref=t.ref,
                page_start=t.page_start,
                page_end=t.page_end,
                bbox=list(t.bbox) if t.bbox else None,
                caption=t.caption,
                footnotes=t.footnotes,
                units=t.units,
                unit=t.unit,
                scale=t.scale,
                header_depth=t.header_depth,
                n_rows=t.n_rows,
                n_cols=t.n_cols,
                section_path=t.section_path,
                markdown=to_markdown(t),
                html=to_html(t),
                stitched_from=t.stitched_from,
                extractor=extractor,
            )
        )
        cell_rows.extend(
            {
                "id": uuid.uuid4(),
                "org_id": org_id,
                "table_id": table_id,
                "row_idx": c.row,
                "col_idx": c.col,
                "row_span": c.row_span,
                "col_span": c.col_span,
                "is_header": c.is_header,
                "header_path": c.header_path,
                "row_path": c.row_path,
                "raw_text": c.raw_text,
                "numeric_value": c.numeric_value,
                "unit": c.unit,
                "scale": c.scale,
                "bbox": list(c.bbox) if c.bbox else None,
            }
            for c in t.cells
        )
    if cell_rows:
        session.execute(insert(TableCell), cell_rows)

    if graph.figures:
        session.execute(
            insert(Figure),
            [
                {
                    "id": uuid.uuid4(),
                    "org_id": org_id,
                    "document_version_id": version_id,
                    "element_ref": f.ref,
                    "page_no": f.page_no,
                    "bbox": list(f.bbox) if f.bbox else None,
                    "figure_type": f.figure_type.value,
                    "classifier_label": f.classifier_label,
                    "classifier_confidence": f.classifier_confidence,
                    "caption": f.caption,
                    "embedded_text": f.embedded_text,
                    "section_path": f.section_path,
                    "crop_key": figure_crop_keys.get(f.ref),
                    "analysis_status": (
                        FigureAnalysisStatus.PENDING
                        if f.figure_type in {FigureType.CHART, FigureType.DIAGRAM, FigureType.IMAGE}
                        else FigureAnalysisStatus.SKIPPED
                    ),
                    "values_are_estimated": True,
                }
                for f in graph.figures
            ],
        )
    return {
        "pages": len(graph.pages),
        "elements": len(graph.elements),
        "tables": len(graph.tables),
        "cells": len(cell_rows),
        "figures": len(graph.figures),
    }
