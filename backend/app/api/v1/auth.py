from fastapi import APIRouter, Depends, status
from sqlalchemy import select

from app.api.deps import MetaDep, PrincipalDep, SettingsDep, TenantSessionDep, UnscopedSessionDep
from app.core.errors import NotFoundError
from app.core.rate_limit import rate_limit
from app.database.models import Organization, User
from app.schemas.auth import LoginIn, MeOut, RefreshIn, RegisterIn, TokenPair
from app.services import auth as auth_service
from app.services.auth import IssuedTokens

router = APIRouter(prefix="/auth", tags=["auth"])

# Strict per-IP limit on credential endpoints (credential stuffing / signup abuse).
_auth_limit = [Depends(rate_limit("auth", "auth_rate_limit_per_minute"))]


def _pair(t: IssuedTokens) -> TokenPair:
    return TokenPair(
        access_token=t.access_token, refresh_token=t.refresh_token, expires_in=t.expires_in
    )


@router.post(
    "/register",
    response_model=TokenPair,
    status_code=status.HTTP_201_CREATED,
    dependencies=_auth_limit,
)
async def register(
    body: RegisterIn, session: UnscopedSessionDep, settings: SettingsDep, meta: MetaDep
) -> TokenPair:
    """Create a user and a new organization owned by that user."""
    return _pair(
        await auth_service.register(
            session,
            email=body.email,
            password=body.password,
            org_name=body.org_name,
            settings=settings,
            meta=meta,
        )
    )


@router.post("/login", response_model=TokenPair, dependencies=_auth_limit)
async def login(
    body: LoginIn, session: UnscopedSessionDep, settings: SettingsDep, meta: MetaDep
) -> TokenPair:
    return _pair(
        await auth_service.login(
            session,
            email=body.email,
            password=body.password,
            org_id=body.org_id,
            settings=settings,
            meta=meta,
        )
    )


@router.post("/refresh", response_model=TokenPair, dependencies=_auth_limit)
async def refresh(
    body: RefreshIn, session: UnscopedSessionDep, settings: SettingsDep, meta: MetaDep
) -> TokenPair:
    """Rotate a refresh token. Reusing a rotated token revokes its whole family."""
    return _pair(
        await auth_service.refresh(
            session, refresh_token=body.refresh_token, settings=settings, meta=meta
        )
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshIn, session: UnscopedSessionDep, meta: MetaDep) -> None:
    await auth_service.logout(session, refresh_token=body.refresh_token, meta=meta)


@router.get("/me", response_model=MeOut)
async def me(principal: PrincipalDep, session: TenantSessionDep) -> MeOut:
    row = (
        await session.execute(
            select(User.email, Organization.name)
            .select_from(User)
            .join(Organization, Organization.id == principal.org_id)
            .where(User.id == principal.user_id)
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("User not found.")
    return MeOut(
        user_id=principal.user_id,
        email=row.email,
        org_id=principal.org_id,
        org_name=row.name,
        role=principal.role.value,
    )
