import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from starlette.concurrency import run_in_threadpool

from app.api.deps import MetaDep, PrincipalDep, SettingsDep, TenantSessionDep, require_role
from app.core.rate_limit import rate_limit
from app.core.security import Principal
from app.database.models import Role, VersionStatus
from app.ingestion.validation import safe_display_name, spool_to_disk, validate_file
from app.schemas.documents import (
    DocumentDetail,
    DocumentSummary,
    Page,
    StageOut,
    StatusOut,
    UploadOut,
)
from app.services import documents as svc
from app.storage import get_storage
from app.workers.tasks import process_document_version, purge_document

router = APIRouter(prefix="/documents", tags=["documents"])

MemberDep = Annotated[Principal, Depends(require_role(Role.MEMBER))]
AdminDep = Annotated[Principal, Depends(require_role(Role.ADMIN))]


@router.post(
    "/upload",
    response_model=UploadOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("upload", "upload_rate_limit_per_minute"))],
)
async def upload_document(
    principal: MemberDep,
    session: TenantSessionDep,
    settings: SettingsDep,
    meta: MetaDep,
    file: Annotated[UploadFile, File(description="PDF, PNG, JPEG, DOCX, XLSX or PPTX")],
    title: Annotated[str | None, Form(max_length=500)] = None,
    tags: Annotated[list[str] | None, Form(max_length=20)] = None,
    document_id: Annotated[
        uuid.UUID | None, Form(description="Upload as a new version of this document")
    ] = None,
) -> UploadOut:
    """Validate, store and enqueue a document for processing.

    Idempotent by content: re-uploading identical bytes returns the existing version with
    `deduplicated: true`."""
    filename = safe_display_name(file.filename)
    path, sha256, size = await run_in_threadpool(
        spool_to_disk, file.file, settings.max_upload_bytes, settings.worker_tmp_dir
    )
    try:
        validated = await run_in_threadpool(
            validate_file,
            path,
            sha256=sha256,
            size_bytes=size,
            original_filename=filename,
            max_pages=settings.max_pages_per_document,
        )
        result = await svc.create_upload(
            session,
            principal=principal,
            upload=validated,
            original_filename=filename,
            title=title,
            tags=sorted({t.strip()[:64] for t in tags or [] if t.strip()}),
            document_id=document_id,
            storage=get_storage(),
            meta=meta,
        )
    finally:
        path.unlink(missing_ok=True)

    if not result.deduplicated:
        process_document_version.delay(str(result.version.id), str(principal.org_id))
    return UploadOut(
        document_id=result.document.id,
        version_id=result.version.id,
        version_no=result.version.version_no,
        status=result.version.status,
        deduplicated=result.deduplicated,
    )


@router.get("", response_model=Page[DocumentSummary])
async def list_documents(
    _: PrincipalDep,
    session: TenantSessionDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    tag: Annotated[str | None, Query(max_length=64)] = None,
    status_: Annotated[VersionStatus | None, Query(alias="status")] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[DocumentSummary]:
    docs, next_cursor = await svc.list_documents(
        session, limit=limit, cursor=cursor, q=q, tag=tag, status=status_
    )
    return Page[DocumentSummary](
        items=[DocumentSummary.model_validate(d) for d in docs], next_cursor=next_cursor
    )


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: uuid.UUID, _: PrincipalDep, session: TenantSessionDep
) -> DocumentDetail:
    return DocumentDetail.model_validate(
        await svc.get_document(session, document_id, with_versions=True)
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID, principal: AdminDep, session: TenantSessionDep, meta: MetaDep
) -> None:
    """Soft-delete immediately (invisible to all queries), then purge blobs and rows async."""
    await svc.soft_delete(session, document_id=document_id, principal=principal, meta=meta)
    purge_document.delay(str(document_id), str(principal.org_id))


@router.get("/{document_id}/status", response_model=StatusOut)
async def document_status(
    document_id: uuid.UUID, _: PrincipalDep, session: TenantSessionDep
) -> StatusOut:
    doc, version, jobs = await svc.version_status(session, document_id)
    return StatusOut(
        document_id=doc.id,
        version_id=version.id,
        status=version.status,
        error=version.error,
        stages=[StageOut.model_validate(j) for j in jobs],
    )
