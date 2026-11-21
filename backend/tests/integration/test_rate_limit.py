from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis

from app.core.config import Settings
from app.main import create_app
from tests.integration.helpers import PASSWORD, auth, register


@pytest.fixture
async def limited_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    redis = Redis.from_url(settings.redis_url)
    await redis.flushdb()
    await redis.aclose()
    cfg = settings.model_copy(
        update={
            "rate_limit_enabled": True,
            "auth_rate_limit_per_minute": 3,
            "rate_limit_per_minute": 1000,
        }
    )
    app = create_app(cfg)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_login_is_limited_per_ip_with_retry_after(limited_client: AsyncClient) -> None:
    body = {"email": "x@example.com", "password": "wrong-password-123"}
    codes = [
        (await limited_client.post("/api/v1/auth/login", json=body)).status_code for _ in range(4)
    ]
    assert codes == [401, 401, 401, 429]
    r = await limited_client.post("/api/v1/auth/login", json=body)
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    assert r.headers["content-type"] == "application/problem+json"
    assert r.json()["type"].endswith("/rate-limited")


async def test_rate_limit_headers_on_success(limited_client: AsyncClient) -> None:
    # (Headers set by dependencies are applied to successful responses; 429s carry their own.)
    await register(limited_client, "h@example.com")  # consumes 1 of 3
    r = await limited_client.post(
        "/api/v1/auth/login", json={"email": "h@example.com", "password": PASSWORD}
    )
    assert r.status_code == 200
    assert r.headers["ratelimit-limit"] == "3"
    assert r.headers["ratelimit-remaining"] == "1"


async def test_authenticated_bucket_is_per_user_not_per_token(
    settings: Settings,
) -> None:
    redis = Redis.from_url(settings.redis_url)
    await redis.flushdb()
    await redis.aclose()
    cfg = settings.model_copy(update={"rate_limit_enabled": True, "rate_limit_per_minute": 3})
    app = create_app(cfg)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        tokens = await register(c, "u@example.com")  # uses 1 IP-bucket token
        refreshed = (
            await c.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        ).json()
        codes = []
        for i in range(4):
            # Alternate tokens: both belong to the same user, so they share one bucket.
            t = tokens if i % 2 == 0 else refreshed
            codes.append((await c.get("/api/v1/auth/me", headers=auth(t))).status_code)
    assert codes == [200, 200, 200, 429]


async def test_redis_outage_fails_open(settings: Settings) -> None:
    cfg = settings.model_copy(
        update={"rate_limit_enabled": True, "redis_url": "redis://localhost:1/0"}
    )
    app = create_app(cfg)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/health/live")
    assert r.status_code == 200
