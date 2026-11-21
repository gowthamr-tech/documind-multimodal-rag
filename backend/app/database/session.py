"""Database engines and tenant-scoped sessions.

Tenant isolation (ADR 0008): every transaction opened by a session whose `info["org_id"]` is set
starts with `set_config('app.org_id', <org>, true)`. The `true` makes it transaction-local, so a
pooled connection never carries one tenant's context into another tenant's request. RLS policies
read that setting; with no org set, tenant-scoped tables return no rows.

The API uses the async engine; Celery workers use the sync engine. Both use psycopg 3.
"""

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from functools import lru_cache
from typing import Any

from sqlalchemy import Connection, create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

from app.core.config import get_settings

ORG_KEY = "org_id"


@event.listens_for(Session, "after_begin")
def _apply_tenant_context(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    org_id = session.info.get(ORG_KEY)
    if org_id is not None:
        connection.execute(
            text("SELECT set_config('app.org_id', :org_id, true)"), {"org_id": str(org_id)}
        )


@lru_cache
def get_async_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        pool_pre_ping=True,
    )


@lru_cache
def get_sync_engine() -> Engine:
    settings = get_settings()
    return create_engine(settings.database_url, pool_size=5, pool_pre_ping=True)


@lru_cache
def _async_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_async_engine(), expire_on_commit=False)


@lru_cache
def _sync_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(get_sync_engine(), expire_on_commit=False)


def new_async_session(org_id: uuid.UUID | None = None) -> AsyncSession:
    info: dict[str, Any] = {ORG_KEY: org_id} if org_id else {}
    return _async_sessionmaker()(info=info)


@asynccontextmanager
async def async_session_scope(org_id: uuid.UUID | None = None) -> AsyncIterator[AsyncSession]:
    async with new_async_session(org_id) as session:
        yield session


@contextmanager
def sync_session_scope(org_id: uuid.UUID | None = None) -> Iterator[Session]:
    """Transactional scope for workers: commits on success, rolls back on error."""
    info: dict[str, Any] = {ORG_KEY: org_id} if org_id else {}
    with _sync_sessionmaker()(info=info) as session, session.begin():
        yield session


def set_session_org(session: AsyncSession | Session, org_id: uuid.UUID) -> None:
    """Bind an org to a session whose org was unknown at creation (e.g. after auth)."""
    target = session.sync_session if isinstance(session, AsyncSession) else session
    if target.in_transaction():
        raise RuntimeError("set_session_org must be called before the first query")
    target.info[ORG_KEY] = org_id


async def dispose_engines() -> None:
    if get_async_engine.cache_info().currsize:
        await get_async_engine().dispose()
    if get_sync_engine.cache_info().currsize:
        get_sync_engine().dispose()
