"""Document processing pipeline runner.

Each stage is a function `(ctx) -> metrics`. The runner:
  * claims the version atomically (`queued|failed|uploaded -> processing`), so concurrent
    deliveries of the same task can't process one version twice;
  * is idempotent: a version already `ready` at the current PIPELINE_VERSION is skipped;
  * records one `processing_jobs` row per stage with timing, attempt and error details;
  * marks the version `ready` or `failed` (with a client-safe error summary).

Stages are appended as phases land (Phase 3: render/parse/OCR/tables/figures; Phase 4: chunk
and embed). PIPELINE_VERSION changes whenever stage outputs change.
"""

import tempfile
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.logging import get_logger
from app.database.models import (
    DocumentVersion,
    JobStatus,
    ProcessingJob,
    VersionStatus,
)
from app.database.session import sync_session_scope
from app.storage import ObjectStorage, get_storage

PIPELINE_VERSION = "2026.11.4"

logger = get_logger("app.pipeline")


class StageError(Exception):
    """An expected, client-explainable stage failure (message is shown to users)."""


@dataclass
class StageContext:
    org_id: uuid.UUID
    version_id: uuid.UUID
    storage: ObjectStorage
    workdir: Path
    sha256: str
    storage_key: str
    mime_type: str
    page_count: int | None
    original_path: Path | None = None
    state: dict[str, Any] = field(default_factory=dict)  # data passed between stages


Stage = Callable[[StageContext], dict[str, Any]]


def stage_fetch_and_verify(ctx: StageContext) -> dict[str, Any]:
    """Download the original and verify it against the SHA-256 recorded at upload."""
    import hashlib

    path = ctx.storage.download_to(ctx.storage_key, ctx.workdir / "original")
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != ctx.sha256:
        raise StageError("Stored original failed its integrity check (SHA-256 mismatch).")
    ctx.original_path = path
    return {"bytes": path.stat().st_size, "sha256_verified": True}


def default_stages() -> list[tuple[str, Stage]]:
    """The ordered production pipeline."""
    from app.ingestion.stages import (
        stage_analyze_figures,
        stage_chunk_embed,
        stage_parse,
        stage_render,
    )

    return [
        ("fetch_verify", stage_fetch_and_verify),
        ("render", stage_render),
        ("parse_extract", stage_parse),
        ("analyze_figures", stage_analyze_figures),
        ("chunk_embed", stage_chunk_embed),
    ]


def _claim(version_id: uuid.UUID, org_id: uuid.UUID) -> DocumentVersion | None:
    with sync_session_scope(org_id) as s:
        claimed = s.execute(
            update(DocumentVersion)
            .where(
                DocumentVersion.id == version_id,
                DocumentVersion.status.in_(
                    [VersionStatus.UPLOADED, VersionStatus.QUEUED, VersionStatus.FAILED]
                ),
            )
            .values(status=VersionStatus.PROCESSING, error=None)
            .returning(DocumentVersion.id)
        ).scalar_one_or_none()
        if claimed is None:
            return None
        version = s.get(DocumentVersion, version_id)
        assert version is not None  # noqa: S101 - just claimed it
        s.expunge(version)
        return version


def run_pipeline(
    version_id: uuid.UUID,
    org_id: uuid.UUID,
    *,
    attempt: int = 1,
    stages: list[tuple[str, Stage]] | None = None,
    storage: ObjectStorage | None = None,
) -> VersionStatus | None:
    """Process one document version. Returns the final status, or None if not claimable."""
    with sync_session_scope(org_id) as s:
        current = s.execute(
            select(DocumentVersion.status, DocumentVersion.pipeline_version).where(
                DocumentVersion.id == version_id
            )
        ).one_or_none()
    if current is None:
        logger.warning("pipeline_version_missing", version_id=str(version_id))
        return None
    if current.status is VersionStatus.READY and current.pipeline_version == PIPELINE_VERSION:
        logger.info("pipeline_skip_already_ready", version_id=str(version_id))
        return VersionStatus.READY

    version = _claim(version_id, org_id)
    if version is None:
        logger.info("pipeline_not_claimable", version_id=str(version_id), status=current.status)
        return None

    stages = stages if stages is not None else default_stages()
    storage = storage or get_storage()
    log = logger.bind(version_id=str(version_id), org_id=str(org_id))
    settings = get_settings()

    with tempfile.TemporaryDirectory(prefix="documind-", dir=settings.worker_tmp_dir) as tmp:
        ctx = StageContext(
            org_id=org_id,
            version_id=version_id,
            storage=storage,
            workdir=Path(tmp),
            sha256=version.sha256,
            storage_key=version.storage_key,
            mime_type=version.mime_type,
            page_count=version.page_count,
        )
        for name, stage in stages:
            job_id = _start_job(org_id, version_id, name, attempt)
            started = time.perf_counter()
            try:
                metrics = stage(ctx)
            except Exception as exc:
                elapsed = round((time.perf_counter() - started) * 1000, 1)
                expected = isinstance(exc, StageError)
                public = str(exc) if expected else "Internal processing error."
                log.error(
                    "pipeline_stage_failed",
                    stage=name,
                    error=str(exc),
                    expected=expected,
                    exc_info=not expected,
                )
                _finish_job(
                    org_id,
                    job_id,
                    JobStatus.FAILED,
                    {"duration_ms": elapsed},
                    error={
                        "message": public,
                        "type": type(exc).__name__,
                        "trace": None if expected else traceback.format_exc(limit=5),
                    },
                )
                _set_version(
                    org_id,
                    version_id,
                    VersionStatus.FAILED,
                    error={"stage": name, "message": public},
                )
                return VersionStatus.FAILED
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            _finish_job(org_id, job_id, JobStatus.SUCCEEDED, {"duration_ms": elapsed, **metrics})
            log.info("pipeline_stage_done", stage=name, duration_ms=elapsed)

    _set_version(
        org_id, version_id, VersionStatus.READY, pipeline_version=PIPELINE_VERSION, state=ctx.state
    )
    log.info("pipeline_done")
    return VersionStatus.READY


def _start_job(org_id: uuid.UUID, version_id: uuid.UUID, stage: str, attempt: int) -> uuid.UUID:
    with sync_session_scope(org_id) as s:
        job = ProcessingJob(
            org_id=org_id,
            document_version_id=version_id,
            stage=stage,
            status=JobStatus.RUNNING,
            attempt=attempt,
            started_at=datetime.now(UTC),
            metrics={},
        )
        s.add(job)
        s.flush()
        return job.id


def _finish_job(
    org_id: uuid.UUID,
    job_id: uuid.UUID,
    status: JobStatus,
    metrics: dict[str, Any],
    error: dict[str, Any] | None = None,
) -> None:
    with sync_session_scope(org_id) as s:
        s.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id)
            .values(status=status, finished_at=datetime.now(UTC), metrics=metrics, error=error)
        )


def _set_version(
    org_id: uuid.UUID,
    version_id: uuid.UUID,
    status: VersionStatus,
    *,
    error: dict[str, Any] | None = None,
    pipeline_version: str | None = None,
    state: dict[str, Any] | None = None,
) -> None:
    values: dict[str, Any] = {"status": status, "error": error}
    if pipeline_version:
        values["pipeline_version"] = pipeline_version
    if state and "page_count" in state:
        values["page_count"] = state["page_count"]
    with sync_session_scope(org_id) as s:
        s.execute(update(DocumentVersion).where(DocumentVersion.id == version_id).values(**values))
