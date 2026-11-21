from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import PrincipalDep, TenantSessionDep
from app.database.models import JobStatus
from app.schemas.documents import Page, ProcessingJobOut
from app.services import documents as svc

router = APIRouter(prefix="/processing", tags=["processing"])


@router.get("/history", response_model=Page[ProcessingJobOut])
async def processing_history(
    _: PrincipalDep,
    session: TenantSessionDep,
    status: JobStatus | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[ProcessingJobOut]:
    jobs, next_cursor = await svc.processing_history(
        session, limit=limit, cursor=cursor, status=status, since=from_, until=to
    )
    return Page[ProcessingJobOut](
        items=[ProcessingJobOut.model_validate(j) for j in jobs], next_cursor=next_cursor
    )
