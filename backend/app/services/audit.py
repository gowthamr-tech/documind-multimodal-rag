"""Append-only audit logging. Written in the caller's transaction, so an action and its audit
record commit (or roll back) together.

Rows are written with a Core INSERT *without* RETURNING. With RETURNING, PostgreSQL also applies
the table's SELECT policy to the new row, and org-less rows (e.g. failed logins) are
deliberately unreadable by every tenant — so the ORM's default `INSERT ... RETURNING id` would
be rejected by RLS.
"""

import uuid
from typing import Any, cast

from sqlalchemy import Table, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.database.models import AuditLog

_AUDIT_TABLE = cast(Table, AuditLog.__table__)


def _values(
    action: str,
    *,
    org_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    resource_type: str | None = None,
    resource_id: object | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "org_id": org_id,
        "actor_id": actor_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": str(resource_id) if resource_id is not None else None,
        "ip": ip,
        "user_agent": (user_agent or "")[:512] or None,
        "request_id": request_id,
        "metadata": metadata or {},
    }


async def record(session: AsyncSession, action: str, **kwargs: Any) -> None:
    await session.execute(insert(_AUDIT_TABLE).values(**_values(action, **kwargs)))


def record_sync(session: Session, action: str, **kwargs: Any) -> None:
    session.execute(insert(_AUDIT_TABLE).values(**_values(action, **kwargs)))
