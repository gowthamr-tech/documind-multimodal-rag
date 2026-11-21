"""Row-Level Security is the second line of tenant isolation. These tests bypass the
application layer entirely and talk to Postgres as the app role, proving the database itself
refuses cross-tenant access."""

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.database.models import AuditLog, Document
from app.database.session import async_session_scope
from tests.integration.conftest import make_org


async def _add_doc(org_id: uuid.UUID, title: str) -> uuid.UUID:
    doc = Document(org_id=org_id, title=title)
    async with async_session_scope(org_id) as s:
        s.add(doc)
        await s.commit()
    return doc.id


async def _count_docs(org_id: uuid.UUID | None) -> int:
    async with async_session_scope(org_id) as s:
        return (await s.execute(select(func.count()).select_from(Document))).scalar_one()


async def test_tenant_sees_only_its_own_documents() -> None:
    org_a, org_b = await make_org("A"), await make_org("B")
    await _add_doc(org_a, "a-report")
    await _add_doc(org_b, "b-report")

    async with async_session_scope(org_a) as s:
        titles = (await s.execute(select(Document.title))).scalars().all()
    assert titles == ["a-report"]


async def test_no_tenant_context_sees_nothing() -> None:
    org_a = await make_org("A")
    await _add_doc(org_a, "secret")
    assert await _count_docs(None) == 0


async def test_cannot_insert_row_for_another_tenant() -> None:
    org_a, org_b = await make_org("A"), await make_org("B")
    async with async_session_scope(org_b) as s:
        s.add(Document(org_id=org_a, title="smuggled"))
        with pytest.raises(ProgrammingError, match="row-level security"):
            await s.commit()


async def test_cannot_update_or_delete_another_tenants_rows() -> None:
    org_a, org_b = await make_org("A"), await make_org("B")
    doc_id = await _add_doc(org_a, "original")
    async with async_session_scope(org_b) as s:
        upd = await s.execute(
            text("UPDATE documents SET title = 'pwned' WHERE id = :id"), {"id": doc_id}
        )
        dele = await s.execute(text("DELETE FROM documents WHERE id = :id"), {"id": doc_id})
        await s.commit()
    assert upd.rowcount == 0  # type: ignore[attr-defined]
    assert dele.rowcount == 0  # type: ignore[attr-defined]
    async with async_session_scope(org_a) as s:
        assert (await s.get(Document, doc_id)).title == "original"  # type: ignore[union-attr]


async def test_tenant_context_does_not_leak_through_pooled_connection() -> None:
    org_a = await make_org("A")
    await _add_doc(org_a, "a-doc")
    # pool_size=1 guarantees both sessions reuse the *same* physical connection.
    engine = create_async_engine(get_settings().database_url, pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as conn, conn.begin():
            await conn.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_a)})
            assert (await conn.execute(text("SELECT count(*) FROM documents"))).scalar_one() == 1
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT count(*) FROM documents"))).scalar_one() == 0
    finally:
        await engine.dispose()


async def test_audit_log_is_append_only_for_app_role() -> None:
    org_a = await make_org("A")
    async with async_session_scope(org_a) as s:
        s.add(AuditLog(org_id=org_a, action="test.event"))
        await s.commit()
    async with async_session_scope(org_a) as s:
        with pytest.raises(DBAPIError, match="permission denied"):
            await s.execute(text("DELETE FROM audit_logs"))
    async with async_session_scope(org_a) as s:
        with pytest.raises(DBAPIError, match="permission denied"):
            await s.execute(text("UPDATE audit_logs SET action = 'tampered'"))


async def test_app_role_cannot_read_migration_table() -> None:
    async with async_session_scope() as s:
        with pytest.raises(DBAPIError, match="permission denied"):
            await s.execute(text("SELECT * FROM alembic_version"))


async def test_app_role_is_not_privileged() -> None:
    async with async_session_scope() as s:
        row = (
            await s.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    assert tuple(row) == (False, False)
