"""Distributed token-bucket rate limiting in Redis.

One Lua script does read-refill-take-write atomically, using Redis' own clock (TIME) so API
replicas with skewed clocks still share one consistent bucket. Buckets are keyed per user when
authenticated, per client IP otherwise.

Failure policy: if Redis is unreachable the request is *allowed* and a warning is logged.
Rate limiting protects capacity; it must not turn a cache outage into a full API outage.
Authentication and authorization never depend on this path.
"""

import math
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.security import TokenError, decode_access_token

logger = get_logger("app.rate_limit")

_TOKEN_BUCKET = """
local capacity = tonumber(ARGV[1])
local refill_per_ms = tonumber(ARGV[2])
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1]) or capacity
local ts = tonumber(data[2]) or now
tokens = math.min(capacity, tokens + math.max(0, now - ts) * refill_per_ms)
local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(capacity / refill_per_ms) + 1000)
return {allowed, tostring(tokens)}
"""  # noqa: S105 - a Lua script, not a secret


class RateLimitedError(AppError):
    status_code = 429
    problem_type = "rate-limited"
    title = "Too Many Requests"


def get_redis(request: Request) -> Redis:
    redis: Redis | None = getattr(request.app.state, "redis", None)
    if redis is None:
        redis = Redis.from_url(request.app.state.settings.redis_url, socket_timeout=0.5)
        request.app.state.redis = redis
    return redis


def rate_limit(
    bucket: str, per_minute_setting: str
) -> Callable[[Request, Response], Coroutine[Any, Any, None]]:
    """FastAPI dependency factory. `per_minute_setting` names a Settings attribute so limits
    are configurable per environment."""

    async def _dependency(request: Request, response: Response) -> None:
        settings = request.app.state.settings
        if not settings.rate_limit_enabled:
            return
        capacity = int(getattr(settings, per_minute_setting))
        refill_per_ms = capacity / 60_000

        identity = _identity(request)
        key = f"rl:{bucket}:{identity}"
        try:
            allowed, remaining = await get_redis(request).eval(
                _TOKEN_BUCKET, 1, key, capacity, refill_per_ms
            )
        except (RedisError, OSError) as exc:
            logger.warning("rate_limit_unavailable", bucket=bucket, error=type(exc).__name__)
            return

        remaining_f = float(remaining)
        response.headers["RateLimit-Limit"] = str(capacity)
        response.headers["RateLimit-Remaining"] = str(max(0, math.floor(remaining_f)))
        if not int(allowed):
            retry_after = max(1, math.ceil((1 - remaining_f) / refill_per_ms / 1000))
            raise RateLimitedError(
                f"Rate limit exceeded for '{bucket}'. Retry in {retry_after}s.",
                extra={"retry_after": retry_after},
                headers={"Retry-After": str(retry_after), "RateLimit-Limit": str(capacity)},
            )

    return _dependency


def _identity(request: Request) -> str:
    """Bucket per verified user (stable across token refreshes); per IP otherwise."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            principal = decode_access_token(auth[7:], request.app.state.settings)
        except TokenError:
            pass  # falls through to the IP bucket; the auth dependency will reject it
        else:
            return f"u:{principal.user_id}"
    client = request.client.host if request.client else "unknown"
    return f"ip:{client}"
