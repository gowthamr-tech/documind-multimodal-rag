import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.database.models import JobStatus, Sensitivity, VersionStatus


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = Field(
        default=None, description="Opaque cursor for the next page; null when exhausted"
    )


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version_no: int
    sha256: str
    mime_type: str
    size_bytes: int
    original_filename: str
    page_count: int | None
    status: VersionStatus
    pipeline_version: str | None
    error: dict[str, Any] | None
    created_at: datetime


class DocumentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    tags: list[str]
    sensitivity: Sensitivity
    classification: str | None
    created_at: datetime
    updated_at: datetime
    current_version: VersionOut | None


class DocumentDetail(DocumentSummary):
    versions: list[VersionOut]


class UploadOut(BaseModel):
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_no: int
    status: VersionStatus
    deduplicated: bool = Field(
        description="True if identical content already existed in this organization; the "
        "existing version is returned and nothing new is stored or processed."
    )


class StageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stage: str
    status: JobStatus
    attempt: int
    started_at: datetime | None
    finished_at: datetime | None
    metrics: dict[str, Any]
    error: dict[str, Any] | None


class StatusOut(BaseModel):
    document_id: uuid.UUID
    version_id: uuid.UUID
    status: VersionStatus
    error: dict[str, Any] | None
    stages: list[StageOut]


class ProcessingJobOut(StageOut):
    id: uuid.UUID
    document_version_id: uuid.UUID
    created_at: datetime
