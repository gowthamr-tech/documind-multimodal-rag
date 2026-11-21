import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class CellOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    row_idx: int
    col_idx: int
    row_span: int
    col_span: int
    is_header: bool
    header_path: list[str]
    row_path: list[str]
    raw_text: str
    numeric_value: Decimal | None
    unit: str | None
    scale: int | None


class TableOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    page_start: int | None
    page_end: int | None
    bbox: list[float] | None
    caption: str | None
    footnotes: list[str]
    units: str | None
    unit: str | None
    scale: int | None
    header_depth: int
    n_rows: int
    n_cols: int
    section_path: list[str]
    markdown: str
    html: str
    stitched_from: int
    cells: list[CellOut] = []


class FigureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    page_no: int | None
    bbox: list[float] | None
    figure_type: str
    classifier_label: str | None
    classifier_confidence: float | None
    caption: str | None
    section_path: list[str]
    analysis_status: str
    description: str | None
    chart: dict[str, Any] | None
    values_are_estimated: bool
    validation: dict[str, Any] | None
    model: str | None
    error: str | None
    has_image: bool = False


class PageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    page_no: int
    width: float
    height: float
    has_text_layer: bool | None
    ocr_applied: bool
    has_image: bool = False


class ElementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ref: str
    type: str
    page_no: int | None
    bbox: list[float] | None
    text: str
    section_path: list[str]
    reading_order: int
