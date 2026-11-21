"""Health endpoints.

`/health/live`: process liveness only (no dependencies) — for container restarts.
`/health/ready`: can this instance serve traffic? Checks Postgres, Redis and object storage.
"""

import asyncio
import time
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from app import __version__
from app.api.deps import SettingsDep
from app.database.session import async_session_scope
from app.storage import get_storage

router = APIRouter(prefix="/health", tags=["health"])


class LivenessOut(BaseModel):
    status: Literal["ok"]
    version: str


class CheckOut(BaseModel):
    ok: bool
    latency_ms: float
    error: str | None = None


class ReadinessOut(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, CheckOut]


@router.get("/live", response_model=LivenessOut, summary="Process liveness probe")
async def live() -> LivenessOut:
    return LivenessOut(status="ok", version=__version__)


async def _timed(coro_fn) -> CheckOut:  # type: ignore[no-untyped-def]
    start = time.perf_counter()
    try:
        await asyncio.wait_for(coro_fn(), timeout=3)
        return CheckOut(ok=True, latency_ms=round((time.perf_counter() - start) * 1000, 2))
    except Exception as exc:  # report, never raise, from a health check
        return CheckOut(
            ok=False,
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
            error=type(exc).__name__,  # class name only: don't leak connection strings
        )


@router.get(
    "/ready",
    response_model=ReadinessOut,
    summary="Readiness probe (DB, Redis, storage)",
    responses={503: {"model": ReadinessOut}},
)
async def ready(settings: SettingsDep) -> JSONResponse:
    async def db() -> None:
        async with async_session_scope() as s:
            await s.execute(text("SELECT 1"))

    async def redis() -> None:
        client = Redis.from_url(settings.redis_url)
        try:
            await client.ping()
        finally:
            await client.aclose()

    async def storage() -> None:
        await run_in_threadpool(get_storage().healthcheck)

    names = ("database", "redis", "storage")
    results = await asyncio.gather(_timed(db), _timed(redis), _timed(storage))
    checks = dict(zip(names, results, strict=True))
    healthy = all(c.ok for c in checks.values())
    body = ReadinessOut(status="ok" if healthy else "degraded", checks=checks)
    return JSONResponse(body.model_dump(), status_code=200 if healthy else 503)
