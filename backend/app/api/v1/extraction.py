"""Read access to extracted structure and binary renditions (always tenant-scoped via RLS)."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.api.deps import PrincipalDep, TenantSessionDep
from app.core.errors import NotFoundError
from app.database.models import Element, ExtractedTable, Figure, Page, TableCell
from app.schemas.extraction import CellOut, ElementOut, FigureOut, PageOut, TableOut
from app.services.documents import get_document
from app.storage import ObjectNotFoundError, get_storage

router = APIRouter(prefix="/documents", tags=["extraction"])


async def _version_id(session: TenantSessionDep, document_id: uuid.UUID) -> uuid.UUID:
    doc = await get_document(session, document_id)
    if doc.current_version_id is None:
        raise NotFoundError("Document has no versions.")
    return doc.current_version_id


@router.get("/{document_id}/tables", response_model=list[TableOut])
async def list_tables(
    document_id: uuid.UUID,
    _: PrincipalDep,
    session: TenantSessionDep,
    page: Annotated[int | None, Query(ge=1)] = None,
    include_cells: bool = True,
) -> list[TableOut]:
    vid = await _version_id(session, document_id)
    stmt = select(ExtractedTable).where(ExtractedTable.document_version_id == vid)
    if page is not None:
        stmt = stmt.where(ExtractedTable.page_start <= page, ExtractedTable.page_end >= page)
    tables = (
        (await session.execute(stmt.order_by(ExtractedTable.page_start, ExtractedTable.created_at)))
        .scalars()
        .all()
    )
    out = [TableOut.model_validate(t) for t in tables]
    if include_cells and tables:
        cells = (
            (
                await session.execute(
                    select(TableCell)
                    .where(TableCell.table_id.in_([t.id for t in tables]))
                    .order_by(TableCell.row_idx, TableCell.col_idx)
                )
            )
            .scalars()
            .all()
        )
        by_table: dict[uuid.UUID, list[CellOut]] = {}
        for c in cells:
            by_table.setdefault(c.table_id, []).append(CellOut.model_validate(c))
        for t in out:
            t.cells = by_table.get(t.id, [])
    return out


@router.get("/{document_id}/charts", response_model=list[FigureOut])
async def list_figures(
    document_id: uuid.UUID,
    _: PrincipalDep,
    session: TenantSessionDep,
    type_: Annotated[
        Literal["chart", "diagram", "image", "other"] | None, Query(alias="type")
    ] = None,
) -> list[FigureOut]:
    """Figures with their analysis. `?type=chart` for charts only."""
    vid = await _version_id(session, document_id)
    stmt = select(Figure).where(Figure.document_version_id == vid)
    if type_:
        stmt = stmt.where(Figure.figure_type == type_)
    figs = (await session.execute(stmt.order_by(Figure.page_no))).scalars().all()
    out = []
    for f in figs:
        item = FigureOut.model_validate(f)
        item.has_image = f.crop_key is not None
        out.append(item)
    return out


@router.get("/{document_id}/charts/{figure_id}/image", response_class=Response)
async def figure_image(
    document_id: uuid.UUID, figure_id: uuid.UUID, _: PrincipalDep, session: TenantSessionDep
) -> Response:
    vid = await _version_id(session, document_id)
    key = (
        await session.execute(
            select(Figure.crop_key).where(Figure.id == figure_id, Figure.document_version_id == vid)
        )
    ).scalar_one_or_none()
    return await _blob(key, "image/png")


@router.get("/{document_id}/pages", response_model=list[PageOut])
async def list_pages(
    document_id: uuid.UUID, _: PrincipalDep, session: TenantSessionDep
) -> list[PageOut]:
    vid = await _version_id(session, document_id)
    pages = (
        (
            await session.execute(
                select(Page).where(Page.document_version_id == vid).order_by(Page.page_no)
            )
        )
        .scalars()
        .all()
    )
    out = []
    for p in pages:
        item = PageOut.model_validate(p)
        item.has_image = p.image_key is not None
        out.append(item)
    return out


@router.get("/{document_id}/pages/{page_no}/image", response_class=Response)
async def page_image(
    document_id: uuid.UUID, page_no: int, _: PrincipalDep, session: TenantSessionDep
) -> Response:
    vid = await _version_id(session, document_id)
    key = (
        await session.execute(
            select(Page.image_key).where(Page.document_version_id == vid, Page.page_no == page_no)
        )
    ).scalar_one_or_none()
    return await _blob(key, "image/png")


@router.get("/{document_id}/elements", response_model=list[ElementOut])
async def list_elements(
    document_id: uuid.UUID,
    _: PrincipalDep,
    session: TenantSessionDep,
    page: Annotated[int | None, Query(ge=1)] = None,
) -> list[ElementOut]:
    vid = await _version_id(session, document_id)
    stmt = select(Element).where(
        Element.document_version_id == vid, Element.is_furniture.is_(False)
    )
    if page is not None:
        stmt = stmt.where(Element.page_no == page)
    rows = (await session.execute(stmt.order_by(Element.reading_order))).scalars().all()
    return [ElementOut.model_validate(e) for e in rows]


@router.get("/{document_id}/file", response_class=Response)
async def document_file(
    document_id: uuid.UUID,
    _: PrincipalDep,
    session: TenantSessionDep,
    rendition: Literal["original", "pdf"] = "original",
) -> Response:
    """The original bytes, or the PDF rendition (Office files converted for viewing)."""
    doc = await get_document(session, document_id)
    v = doc.current_version
    if v is None:
        raise NotFoundError("Document has no versions.")
    if rendition == "pdf":
        if v.mime_type == "application/pdf":
            return await _blob(v.storage_key, "application/pdf")
        return await _blob(v.storage_key.rsplit("/", 1)[0] + "/rendition.pdf", "application/pdf")
    resp = await _blob(v.storage_key, v.mime_type)
    resp.headers["Content-Disposition"] = (
        f'inline; filename="{v.original_filename.replace(chr(34), "")}"'
    )
    return resp


async def _blob(key: str | None, media_type: str) -> Response:
    if key is None:
        raise NotFoundError("Not available for this document.")
    try:
        data = await run_in_threadpool(get_storage().get_bytes, key)
    except ObjectNotFoundError as exc:
        raise NotFoundError("Not available for this document.") from exc
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )
