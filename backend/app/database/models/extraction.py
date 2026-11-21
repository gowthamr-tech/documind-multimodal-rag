"""Extracted structure (Phase 3). Every row carries org_id for RLS and document_version_id so a
re-run can atomically replace a version's derived data."""

import enum
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    REAL,
    Boolean,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, Timestamps, UUIDPrimaryKey


def _pg_enum(enum_cls: type[enum.Enum], name: str) -> Enum:
    return Enum(enum_cls, name=name, values_callable=lambda e: [m.value for m in e])


class FigureAnalysisStatus(enum.StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


def _version_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )


def _org_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )


class Page(UUIDPrimaryKey, Base):
    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("document_version_id", "page_no", name="uq_pages_version_page"),
    )

    org_id: Mapped[uuid.UUID] = _org_fk()
    document_version_id: Mapped[uuid.UUID] = _version_fk()
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[float] = mapped_column(Float, nullable=False)
    height: Mapped[float] = mapped_column(Float, nullable=False)
    has_text_layer: Mapped[bool | None] = mapped_column(Boolean)
    ocr_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    image_key: Mapped[str | None] = mapped_column(Text)


class Element(UUIDPrimaryKey, Base):
    __tablename__ = "elements"
    __table_args__ = (Index("ix_elements_version_order", "document_version_id", "reading_order"),)

    org_id: Mapped[uuid.UUID] = _org_fk()
    document_version_id: Mapped[uuid.UUID] = _version_fk()
    ref: Mapped[str] = mapped_column(String(80), nullable=False)
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    reading_order: Mapped[int] = mapped_column(Integer, nullable=False)
    page_no: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list[float] | None] = mapped_column(ARRAY(REAL))
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    section_path: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    parent_ref: Mapped[str | None] = mapped_column(String(80))
    is_furniture: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class ExtractedTable(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "extracted_tables"
    __table_args__ = (Index("ix_extracted_tables_version", "document_version_id"),)

    org_id: Mapped[uuid.UUID] = _org_fk()
    document_version_id: Mapped[uuid.UUID] = _version_fk()
    element_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list[float] | None] = mapped_column(ARRAY(REAL))
    caption: Mapped[str | None] = mapped_column(Text)
    footnotes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    units: Mapped[str | None] = mapped_column(String(120))
    unit: Mapped[str | None] = mapped_column(String(16))
    scale: Mapped[int | None] = mapped_column(Numeric(20, 0))
    header_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    n_cols: Mapped[int] = mapped_column(Integer, nullable=False)
    section_path: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    html: Mapped[str] = mapped_column(Text, nullable=False)
    stitched_from: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    extractor: Mapped[str] = mapped_column(String(40), nullable=False)


class TableCell(UUIDPrimaryKey, Base):
    __tablename__ = "table_cells"
    __table_args__ = (Index("ix_table_cells_table", "table_id", "row_idx", "col_idx"),)

    org_id: Mapped[uuid.UUID] = _org_fk()
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("extracted_tables.id", ondelete="CASCADE"), nullable=False
    )
    row_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    col_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    row_span: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    col_span: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_header: Mapped[bool] = mapped_column(Boolean, nullable=False)
    header_path: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    row_path: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    numeric_value: Mapped[Decimal | None] = mapped_column(Numeric(38, 10))
    unit: Mapped[str | None] = mapped_column(String(16))
    scale: Mapped[int | None] = mapped_column(Numeric(20, 0))
    bbox: Mapped[list[float] | None] = mapped_column(ARRAY(REAL))


class Figure(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "figures"
    __table_args__ = (Index("ix_figures_version", "document_version_id"),)

    org_id: Mapped[uuid.UUID] = _org_fk()
    document_version_id: Mapped[uuid.UUID] = _version_fk()
    element_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    page_no: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list[float] | None] = mapped_column(ARRAY(REAL))
    figure_type: Mapped[str] = mapped_column(String(20), nullable=False)
    classifier_label: Mapped[str | None] = mapped_column(String(60))
    classifier_confidence: Mapped[float | None] = mapped_column(Float)
    caption: Mapped[str | None] = mapped_column(Text)
    embedded_text: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    section_path: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    crop_key: Mapped[str | None] = mapped_column(Text)
    analysis_status: Mapped[FigureAnalysisStatus] = mapped_column(
        _pg_enum(FigureAnalysisStatus, "figure_analysis_status"),
        nullable=False,
        default=FigureAnalysisStatus.PENDING,
    )
    description: Mapped[str | None] = mapped_column(Text)
    chart: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    values_are_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    model: Mapped[str | None] = mapped_column(String(80))
    prompt_version: Mapped[str | None] = mapped_column(String(20))
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
