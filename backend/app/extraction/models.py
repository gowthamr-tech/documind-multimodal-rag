"""The normalised Document Graph: parser-independent extraction output.

Everything downstream (persistence, chunking, retrieval, citations) consumes these types, never
parser objects, so the parser can be swapped (ADR 0002) without touching the rest of the system.

Coordinates: `BBox` is (x0, y0, x1, y1) normalised to [0, 1] by page width/height, origin at the
top-left — the same convention the viewer uses to draw highlight overlays.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

BBox = tuple[float, float, float, float]


class ElementType(enum.StrEnum):
    TITLE = "title"
    SECTION_HEADER = "section_header"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    FIGURE = "figure"
    CAPTION = "caption"
    FOOTNOTE = "footnote"
    HEADER = "header"
    FOOTER = "footer"
    FORMULA = "formula"
    CODE = "code"
    OTHER = "other"


class FigureType(enum.StrEnum):
    CHART = "chart"
    DIAGRAM = "diagram"
    IMAGE = "image"
    OTHER = "other"


@dataclass(slots=True)
class PageRecord:
    page_no: int  # 1-based
    width: float
    height: float
    has_text_layer: bool | None = None  # None: not applicable (e.g. DOCX without rendering)
    ocr_applied: bool = False
    image_key: str | None = None


@dataclass(slots=True)
class ElementRecord:
    ref: str  # stable within a version, e.g. "#/texts/12"
    type: ElementType
    reading_order: int
    text: str
    page_no: int | None
    bbox: BBox | None
    section_path: list[str] = field(default_factory=list)
    parent_ref: str | None = None
    is_furniture: bool = False  # page headers/footers: excluded from body chunks
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CellRecord:
    row: int
    col: int
    row_span: int
    col_span: int
    is_header: bool
    raw_text: str
    header_path: list[str] = field(default_factory=list)  # column headers, outermost first
    row_path: list[str] = field(default_factory=list)  # row header(s) for this row
    numeric_value: Decimal | None = None
    unit: str | None = None
    scale: int | None = None  # multiplier implied by the table's units, e.g. 1_000_000
    bbox: BBox | None = None


@dataclass(slots=True)
class TableRecord:
    ref: str
    page_start: int | None
    page_end: int | None
    n_rows: int
    n_cols: int
    cells: list[CellRecord]
    caption: str | None = None
    footnotes: list[str] = field(default_factory=list)
    units: str | None = None  # e.g. "USD millions"
    unit: str | None = None  # e.g. "USD", "%"
    scale: int | None = None
    header_depth: int = 0
    bbox: BBox | None = None
    section_path: list[str] = field(default_factory=list)
    stitched_from: int = 1  # >1 when merged across pages

    def grid(self) -> list[list[str]]:
        """Dense grid (spans expanded) — used for markdown/HTML and evaluation."""
        g = [["" for _ in range(self.n_cols)] for _ in range(self.n_rows)]
        for c in self.cells:
            for r in range(c.row, min(c.row + c.row_span, self.n_rows)):
                for k in range(c.col, min(c.col + c.col_span, self.n_cols)):
                    g[r][k] = c.raw_text
        return g


@dataclass(slots=True)
class FigureRecord:
    ref: str
    page_no: int | None
    bbox: BBox | None
    figure_type: FigureType
    classifier_label: str | None = None
    classifier_confidence: float | None = None
    caption: str | None = None
    embedded_text: list[str] = field(default_factory=list)  # e.g. OCR'd chart title
    image_png: bytes | None = None
    section_path: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DocumentGraph:
    pages: list[PageRecord]
    elements: list[ElementRecord]
    tables: list[TableRecord]
    figures: list[FigureRecord]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return len(self.pages)
