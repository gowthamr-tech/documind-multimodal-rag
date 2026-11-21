"""Integration fixtures: a real PostgreSQL (pgvector) and Redis from docker compose.

The schema is rebuilt once per session with Alembic (so migrations themselves are under test),
and all tables are truncated between tests. The app under test connects as the non-owner
`documind_app` role, so Row-Level Security is exercised exactly as in production.
"""

import uuid
from collections.abc import AsyncIterator, Iterator

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.core.config import get_settings
from app.database.models import Organization
from app.database.session import async_session_scope, dispose_engines

pytestmark = pytest.mark.integration


def _owner_url() -> str:
    return get_settings().migration_database_url


def _db_reachable() -> bool:
    dsn = _owner_url().replace("postgresql+psycopg://", "postgresql://")
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


@pytest.fixture(scope="session")
def owner_engine() -> Iterator[Engine]:
    if not _db_reachable():
        pytest.skip("PostgreSQL not reachable on the test URL — run `docker compose up -d`")
    engine = create_engine(_owner_url())
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = _owner_url()
    cfg.attributes["configure_logger"] = False
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_database(owner_engine: Engine) -> AsyncIterator[None]:
    yield
    await dispose_engines()  # don't keep pooled app connections across truncation
    with owner_engine.begin() as conn:
        tables = conn.execute(
            text(
                "SELECT string_agg(quote_ident(tablename), ', ') FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        ).scalar_one()
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


async def make_org(name: str = "Acme") -> uuid.UUID:
    org = Organization(name=name, slug=f"{name.lower()}-{uuid.uuid4().hex[:8]}")
    async with async_session_scope() as session:
        session.add(org)
        await session.commit()
    return org.id


@pytest.fixture(autouse=True)
def _minimal_pipeline(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """API tests exercise the HTTP/DB contract, not ML extraction: run only the integrity stage.
    Tests marked `full_pipeline` run every production stage (real Docling)."""
    if request.node.get_closest_marker("full_pipeline"):
        return
    from app.ingestion import pipeline

    monkeypatch.setattr(
        pipeline, "default_stages", lambda: [("fetch_verify", pipeline.stage_fetch_and_verify)]
    )
