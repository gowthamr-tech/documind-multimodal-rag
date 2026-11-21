"""Shared FastAPI dependencies: settings, authentication, role checks, DB sessions."""

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import Principal, TokenError, decode_access_token
from app.database.models import ROLE_RANK, Role
from app.database.session import async_session_scope
from app.services.auth import AuthenticationError, RequestMeta

_bearer = HTTPBearer(auto_error=False)


class ForbiddenError(AppError):
    status_code = 403
    problem_type = "forbidden"
    title = "Forbidden"


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def request_meta(request: Request) -> RequestMeta:
    return RequestMeta(
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        request_id=getattr(request.state, "request_id", None),
    )


MetaDep = Annotated[RequestMeta, Depends(request_meta)]


async def get_principal(
    settings: SettingsDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise AuthenticationError("Missing bearer token.")
    try:
        return decode_access_token(creds.credentials, settings)
    except TokenError as exc:
        raise AuthenticationError("Invalid or expired access token.") from exc


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def require_role(minimum: Role) -> Callable[..., Coroutine[Any, Any, Principal]]:
    async def _check(principal: PrincipalDep) -> Principal:
        if ROLE_RANK[principal.role] < ROLE_RANK[minimum]:
            raise ForbiddenError(f"Requires role '{minimum.value}' or higher.")
        return principal

    return _check


async def unscoped_session() -> AsyncIterator[AsyncSession]:
    """For auth endpoints only: no tenant context exists yet."""
    async with async_session_scope() as session:
        yield session


async def tenant_session(principal: PrincipalDep) -> AsyncIterator[AsyncSession]:
    """Session whose every transaction is RLS-scoped to the caller's organization."""
    async with async_session_scope(principal.org_id) as session:
        yield session


UnscopedSessionDep = Annotated[AsyncSession, Depends(unscoped_session)]
TenantSessionDep = Annotated[AsyncSession, Depends(tenant_session)]
