import time
import uuid

import jwt
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import Settings
from app.database.models import AuditLog
from app.database.session import async_session_scope

PASSWORD = "correct horse battery staple"


async def _register(client: AsyncClient, email: str = "ada@example.com") -> dict[str, str]:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "org_name": "Analytical Engines"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_register_then_me(client: AsyncClient) -> None:
    tokens = await _register(client)
    me = await client.get("/api/v1/auth/me", headers=_auth(tokens["access_token"]))
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "ada@example.com"
    assert body["role"] == "owner"
    assert body["org_name"] == "Analytical Engines"


async def test_duplicate_email_is_conflict_case_insensitive(client: AsyncClient) -> None:
    await _register(client, "ada@example.com")
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "ADA@example.com", "password": PASSWORD, "org_name": "Other"},
    )
    assert resp.status_code == 409
    assert resp.headers["content-type"] == "application/problem+json"


async def test_weak_password_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "a@example.com", "password": "short", "org_name": "Org"},
    )
    assert resp.status_code == 422


async def test_login_success_and_failure_are_audited(client: AsyncClient) -> None:
    await _register(client)
    ok = await client.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )
    assert ok.status_code == 200
    bad = await client.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": "wrong-password!"}
    )
    unknown = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever-123"}
    )
    assert bad.status_code == unknown.status_code == 401
    # Same message for unknown user and wrong password: no account enumeration.
    assert bad.json()["detail"] == unknown.json()["detail"]

    org_id = jwt.decode(ok.json()["access_token"], options={"verify_signature": False})["org"]
    async with async_session_scope(uuid.UUID(org_id)) as s:
        actions = (await s.execute(select(AuditLog.action))).scalars().all()
    assert {"auth.register", "auth.login"} <= set(actions)


async def test_missing_and_garbage_tokens_rejected(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    resp = await client.get("/api/v1/auth/me", headers=_auth("not.a.jwt"))
    assert resp.status_code == 401


async def test_tampered_and_alg_none_tokens_rejected(
    client: AsyncClient, settings: Settings
) -> None:
    tokens = await _register(client)
    claims = jwt.decode(tokens["access_token"], options={"verify_signature": False})
    claims["role"] = "owner"
    claims["org"] = str(uuid.uuid4())  # try to jump tenants
    forged = jwt.encode(claims, "x" * 40, algorithm="HS256")
    none_alg = jwt.encode(claims, key=None, algorithm="none")  # type: ignore[arg-type]
    for bad in (forged, none_alg):
        assert (await client.get("/api/v1/auth/me", headers=_auth(bad))).status_code == 401


async def test_expired_access_token_rejected(client: AsyncClient, settings: Settings) -> None:
    tokens = await _register(client)
    claims = jwt.decode(tokens["access_token"], options={"verify_signature": False})
    claims["exp"] = int(time.time()) - 10
    expired = jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256")
    assert (await client.get("/api/v1/auth/me", headers=_auth(expired))).status_code == 401


async def test_refresh_rotates_and_reuse_revokes_family(client: AsyncClient) -> None:
    first = await _register(client)
    r1 = await client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r1.status_code == 200
    second = r1.json()
    assert second["refresh_token"] != first["refresh_token"]

    # Attacker replays the already-rotated token -> rejected and whole family revoked.
    replay = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert replay.status_code == 401
    assert "reuse" in replay.json()["detail"].lower()

    # The legitimate user's newer token is now dead too.
    r2 = await client.post("/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert r2.status_code == 401


async def test_logout_revokes_refresh_token(client: AsyncClient) -> None:
    tokens = await _register(client)
    out = await client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert out.status_code == 204
    again = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert again.status_code == 401
