"""Document lifecycle: upload (validated, deduplicated, versioned), listing, deletion."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import NotFoundError
from app.core.pagination import decode_cursor, encode_cursor
from app.core.security import Principal
from app.database.models import Document, DocumentVersion, ProcessingJob, VersionStatus
from app.ingestion.validation import ValidatedUpload
from app.services import audit
from app.services.auth import RequestMeta
from app.storage import ObjectStorage


@dataclass(frozen=True, slots=True)
class UploadResult:
    document: Document
    version: DocumentVersion
    deduplicated: bool


def storage_key_for(org_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID) -> str:
    return f"org/{org_id}/docs/{document_id}/versions/{version_id}/original"


async def _existing_by_hash(session: AsyncSession, sha256: str) -> DocumentVersion | None:
    # RLS already restricts to the caller's org; the explicit join excludes deleted documents.
    return (
        await session.execute(
            select(DocumentVersion)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.sha256 == sha256, DocumentVersion.deleted_at.is_(None))
        )
    ).scalar_one_or_none()


async def get_document(
    session: AsyncSession, document_id: uuid.UUID, *, with_versions: bool = False
) -> Document:
    stmt = select(Document).where(Document.id == document_id, Document.deleted_at.is_(None))
    stmt = stmt.options(selectinload(Document.current_version))
    if with_versions:
        stmt = stmt.options(selectinload(Document.versions))
    doc = (await session.execute(stmt)).scalar_one_or_none()
    if doc is None:
        raise NotFoundError("Document not found.")
    return doc


async def create_upload(
    session: AsyncSession,
    *,
    principal: Principal,
    upload: ValidatedUpload,
    original_filename: str,
    title: str | None,
    tags: list[str],
    document_id: uuid.UUID | None,
    storage: ObjectStorage,
    meta: RequestMeta,
) -> UploadResult:
    """Store and register an upload. Identical content (same SHA-256 in the org) is
    deduplicated: the existing version is returned and nothing is re-processed."""
    existing = await _existing_by_hash(session, upload.sha256)
    if existing is not None:
        return UploadResult(await get_document(session, existing.document_id), existing, True)

    if document_id is not None:
        document = await get_document(session, document_id, with_versions=True)
        next_no = (max((v.version_no for v in document.versions), default=0)) + 1
    else:
        document = Document(
            org_id=principal.org_id,
            title=(title or Path(original_filename).stem or "Untitled")[:500],
            tags=tags,
            created_by=principal.user_id,
        )
        session.add(document)
        await session.flush()
        next_no = 1

    version_id = uuid.uuid4()
    key = storage_key_for(principal.org_id, document.id, version_id)
    version = DocumentVersion(
        id=version_id,
        document_id=document.id,
        org_id=principal.org_id,
        version_no=next_no,
        sha256=upload.sha256,
        mime_type=upload.mime_type,
        size_bytes=upload.size_bytes,
        storage_key=key,
        original_filename=original_filename,
        page_count=upload.page_count,
        metadata_={"kind": upload.kind.value, **upload.metadata},
        status=VersionStatus.QUEUED,
    )
    session.add(version)
    await session.flush()
    document.current_version_id = version.id
    if title and document_id is not None:
        document.title = title[:500]

    # Blob first, then commit: a failed commit leaves an orphan blob (cleaned up below),
    # never a DB row pointing at a missing object.
    storage.put_file(key, upload.path, upload.mime_type)
    await audit.record(
        session,
        "document.upload",
        org_id=principal.org_id,
        actor_id=principal.user_id,
        resource_type="document_version",
        resource_id=version.id,
        ip=meta.ip,
        user_agent=meta.user_agent,
        request_id=meta.request_id,
        metadata={"sha256": upload.sha256, "size": upload.size_bytes, "kind": upload.kind.value},
    )
    try:
        await session.commit()
    except IntegrityError:
        # Lost a race with an identical concurrent upload: return the winner.
        await session.rollback()
        storage.delete_prefix(f"org/{principal.org_id}/docs/{document.id}/versions/{version_id}/")
        winner = await _existing_by_hash(session, upload.sha256)
        if winner is None:
            raise
        return UploadResult(await get_document(session, winner.document_id), winner, True)
    except BaseException:
        storage.delete_prefix(f"org/{principal.org_id}/docs/{document.id}/versions/{version_id}/")
        raise
    return UploadResult(document, version, False)


async def list_documents(
    session: AsyncSession,
    *,
    limit: int,
    cursor: str | None,
    q: str | None,
    tag: str | None,
    status: VersionStatus | None,
) -> tuple[list[Document], str | None]:
    stmt = (
        select(Document)
        .where(Document.deleted_at.is_(None))
        .options(selectinload(Document.current_version))
    )
    if q:
        stmt = stmt.where(Document.title.ilike(f"%{_escape_like(q)}%", escape="\\"))
    if tag:
        stmt = stmt.where(Document.tags.contains([tag]))
    if status:
        stmt = stmt.join(DocumentVersion, DocumentVersion.id == Document.current_version_id).where(
            DocumentVersion.status == status
        )
    if cursor:
        c_time, c_id = decode_cursor(cursor)
        stmt = stmt.where(tuple_(Document.created_at, Document.id) < tuple_(c_time, c_id))
    rows = (
        (
            await session.execute(
                stmt.order_by(Document.created_at.desc(), Document.id.desc()).limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    next_cursor = (
        encode_cursor(rows[limit - 1].created_at, rows[limit - 1].id) if len(rows) > limit else None
    )
    return list(rows[:limit]), next_cursor


async def soft_delete(
    session: AsyncSession, *, document_id: uuid.UUID, principal: Principal, meta: RequestMeta
) -> None:
    doc = await get_document(session, document_id)
    now = datetime.now(UTC)
    doc.deleted_at = now
    # Release the versions' content hashes so the same file can be uploaded again.
    await session.execute(
        update(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .values(deleted_at=now)
    )
    await audit.record(
        session,
        "document.delete",
        org_id=principal.org_id,
        actor_id=principal.user_id,
        resource_type="document",
        resource_id=document_id,
        ip=meta.ip,
        user_agent=meta.user_agent,
        request_id=meta.request_id,
    )
    await session.commit()


async def version_status(
    session: AsyncSession, document_id: uuid.UUID
) -> tuple[Document, DocumentVersion, list[ProcessingJob]]:
    doc = await get_document(session, document_id)
    if doc.current_version is None:
        raise NotFoundError("Document has no versions.")
    jobs = (
        (
            await session.execute(
                select(ProcessingJob)
                .where(ProcessingJob.document_version_id == doc.current_version.id)
                .order_by(ProcessingJob.created_at)
            )
        )
        .scalars()
        .all()
    )
    return doc, doc.current_version, list(jobs)


async def processing_history(
    session: AsyncSession,
    *,
    limit: int,
    cursor: str | None,
    status: str | None,
    since: datetime | None,
    until: datetime | None,
) -> tuple[list[ProcessingJob], str | None]:
    stmt = select(ProcessingJob)
    if status:
        stmt = stmt.where(ProcessingJob.status == status)
    if since:
        stmt = stmt.where(ProcessingJob.created_at >= since)
    if until:
        stmt = stmt.where(ProcessingJob.created_at < until)
    if cursor:
        c_time, c_id = decode_cursor(cursor)
        stmt = stmt.where(tuple_(ProcessingJob.created_at, ProcessingJob.id) < tuple_(c_time, c_id))
    rows = (
        (
            await session.execute(
                stmt.order_by(ProcessingJob.created_at.desc(), ProcessingJob.id.desc()).limit(
                    limit + 1
                )
            )
        )
        .scalars()
        .all()
    )
    next_cursor = (
        encode_cursor(rows[limit - 1].created_at, rows[limit - 1].id) if len(rows) > limit else None
    )
    return list(rows[:limit]), next_cursor


async def count_documents(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                select(func.count()).select_from(Document).where(Document.deleted_at.is_(None))
            )
        ).scalar_one()
    )


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
