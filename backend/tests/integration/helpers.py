import uuid
from pathlib import Path

import jwt
from httpx import AsyncClient, Response

from app.core.security import hash_password
from app.database.models import Membership, Role, User
from app.database.session import async_session_scope

PASSWORD = "correct horse battery staple"


async def register(client: AsyncClient, email: str, org: str = "Org") -> dict[str, str]:
    resp = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD, "org_name": org}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


def auth(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def org_of(tokens: dict[str, str]) -> uuid.UUID:
    claims = jwt.decode(tokens["access_token"], options={"verify_signature": False})
    return uuid.UUID(claims["org"])


async def add_member(
    client: AsyncClient, org_id: uuid.UUID, email: str, role: Role
) -> dict[str, str]:
    """Create a user directly in `org_id` with `role` and log them in."""
    async with async_session_scope() as s:
        user = User(email=email, password_hash=hash_password(PASSWORD))
        s.add(user)
        await s.flush()
        s.add(Membership(org_id=org_id, user_id=user.id, role=role))
        await s.commit()
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD, "org_id": str(org_id)}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()  # type: ignore[no-any-return]


async def upload(
    client: AsyncClient,
    tokens: dict[str, str],
    path: Path,
    *,
    filename: str | None = None,
    content_type: str = "application/octet-stream",
    data: dict[str, str] | None = None,
) -> Response:
    with path.open("rb") as fh:
        return await client.post(
            "/api/v1/documents/upload",
            headers=auth(tokens),
            files={"file": (filename or path.name, fh, content_type)},
            data=data or {},
        )
