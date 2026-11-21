import os

# Point every engine at the dedicated test database *before* app modules read settings.
# Override with real env vars to target another server (e.g. CI service containers).
os.environ.setdefault("DOCUMIND_ENVIRONMENT", "test")
os.environ.setdefault(
    "DOCUMIND_DATABASE_URL",
    "postgresql+psycopg://documind_app:documind_dev_app@localhost:5433/documind_test",
)
os.environ.setdefault(
    "DOCUMIND_MIGRATION_DATABASE_URL",
    "postgresql+psycopg://documind:documind_dev_owner@localhost:5433/documind_test",
)
os.environ.setdefault("DOCUMIND_REDIS_URL", "redis://localhost:6380/15")
os.environ.setdefault("DOCUMIND_LOG_LEVEL", "WARNING")
os.environ.setdefault("DOCUMIND_RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("DOCUMIND_CELERY_TASK_ALWAYS_EAGER", "true")
os.environ.setdefault(
    "DOCUMIND_STORAGE_LOCAL_ROOT",
    __import__("tempfile").mkdtemp(prefix="documind-test-storage-"),
)

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.core.config import Environment, Settings
from app.core.errors import NotFoundError
from app.main import create_app


@pytest.fixture
def settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    return Settings(
        environment=Environment.TEST,
        log_json=True,
        log_level="WARNING",
        storage_local_root=tmp_path_factory.mktemp("storage"),
        rate_limit_enabled=False,
    )


class _Payload(BaseModel):
    name: str
    count: int


def _add_probe_routes(app: FastAPI) -> None:
    """Test-only routes that trigger each error path through the real middleware stack."""

    @app.get("/_probe/not-found")
    async def not_found() -> None:
        raise NotFoundError("document 123 not found", extra={"resource": "document"})

    @app.get("/_probe/crash")
    async def crash() -> None:
        raise RuntimeError("db password is hunter2")  # must never reach the client

    @app.post("/_probe/validate")
    async def validate(payload: _Payload) -> _Payload:
        return payload


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    application = create_app(settings)
    _add_probe_routes(application)
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
