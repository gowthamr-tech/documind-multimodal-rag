"""Registration, login, refresh-token rotation, and logout.

Refresh tokens rotate on every use. Each login starts a token *family*; presenting a refresh
token that was already rotated (or revoked) is treated as theft and revokes the whole family,
which forces the legitimate user and any attacker to sign in again.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, ConflictError
from app.core.security import (
    Principal,
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    password_needs_rehash,
    verify_password,
)
from app.database.models import Membership, Organization, RefreshToken, Role, User
from app.services import audit


class AuthenticationError(AppError):
    status_code = 401
    problem_type = "unauthenticated"
    title = "Unauthenticated"


@dataclass(frozen=True, slots=True)
class RequestMeta:
    ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    principal: Principal


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "org"
    return f"{base}-{uuid.uuid4().hex[:6]}"


async def _bind_org(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Set the RLS tenant for the remainder of the *current* transaction."""
    await session.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_id)})


async def _issue(
    session: AsyncSession,
    principal: Principal,
    settings: Settings,
    family_id: uuid.UUID | None = None,
) -> IssuedTokens:
    raw = new_refresh_token()
    session.add(
        RefreshToken(
            user_id=principal.user_id,
            org_id=principal.org_id,
            family_id=family_id or uuid.uuid4(),
            token_hash=hash_refresh_token(raw),
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.refresh_token_ttl_seconds),
        )
    )
    access, ttl = create_access_token(principal, settings)
    return IssuedTokens(access, raw, ttl, principal)


async def register(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    org_name: str,
    settings: Settings,
    meta: RequestMeta,
) -> IssuedTokens:
    user = User(email=email, password_hash=hash_password(password))
    org = Organization(name=org_name, slug=_slugify(org_name))
    session.add_all([user, org])
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("An account with this email already exists.") from exc
    session.add(Membership(org_id=org.id, user_id=user.id, role=Role.OWNER))
    principal = Principal(user_id=user.id, org_id=org.id, role=Role.OWNER)
    tokens = await _issue(session, principal, settings)
    await _bind_org(session, org.id)
    await audit.record(
        session,
        "auth.register",
        org_id=org.id,
        actor_id=user.id,
        resource_type="user",
        resource_id=user.id,
        ip=meta.ip,
        user_agent=meta.user_agent,
        request_id=meta.request_id,
    )
    await session.commit()
    return tokens


async def login(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    org_id: uuid.UUID | None,
    settings: Settings,
    meta: RequestMeta,
) -> IssuedTokens:
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    # verify_password runs a full argon2 verification even when the user is unknown.
    if not verify_password(password, user.password_hash if user else None) or user is None:
        await audit.record(
            session,
            "auth.login_failed",
            org_id=None,
            actor_id=None,
            ip=meta.ip,
            user_agent=meta.user_agent,
            request_id=meta.request_id,
            metadata={"reason": "bad_credentials"},
        )
        await session.commit()
        raise AuthenticationError("Invalid email or password.")
    if not user.is_active:
        raise AuthenticationError("This account is disabled.")

    stmt = select(Membership).where(Membership.user_id == user.id)
    if org_id is not None:
        stmt = stmt.where(Membership.org_id == org_id)
    membership = (
        await session.execute(stmt.order_by(Membership.created_at).limit(1))
    ).scalar_one_or_none()
    if membership is None:
        raise AuthenticationError("No membership in the requested organization.")

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    principal = Principal(user_id=user.id, org_id=membership.org_id, role=membership.role)
    tokens = await _issue(session, principal, settings)
    await _bind_org(session, membership.org_id)
    await audit.record(
        session,
        "auth.login",
        org_id=membership.org_id,
        actor_id=user.id,
        ip=meta.ip,
        user_agent=meta.user_agent,
        request_id=meta.request_id,
    )
    await session.commit()
    return tokens


async def refresh(
    session: AsyncSession, *, refresh_token: str, settings: Settings, meta: RequestMeta
) -> IssuedTokens:
    now = datetime.now(UTC)
    stored = (
        await session.execute(
            select(RefreshToken)
            .where(RefreshToken.token_hash == hash_refresh_token(refresh_token))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if stored is None:
        raise AuthenticationError("Invalid refresh token.")

    if stored.revoked_at is not None:
        # Reuse of a rotated token: assume theft, revoke the entire family.
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == stored.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await _bind_org(session, stored.org_id)
        await audit.record(
            session,
            "auth.refresh_reuse_detected",
            org_id=stored.org_id,
            actor_id=stored.user_id,
            ip=meta.ip,
            user_agent=meta.user_agent,
            request_id=meta.request_id,
            metadata={"family_id": str(stored.family_id)},
        )
        await session.commit()
        raise AuthenticationError("Refresh token reuse detected; please sign in again.")

    if stored.expires_at <= now:
        raise AuthenticationError("Refresh token expired.")

    membership = await session.get(Membership, (stored.org_id, stored.user_id))
    user = await session.get(User, stored.user_id)
    if membership is None or user is None or not user.is_active:
        raise AuthenticationError("Account or membership no longer valid.")

    stored.revoked_at = now
    principal = Principal(user_id=user.id, org_id=membership.org_id, role=membership.role)
    tokens = await _issue(session, principal, settings, family_id=stored.family_id)
    await session.commit()
    return tokens


async def logout(session: AsyncSession, *, refresh_token: str, meta: RequestMeta) -> None:
    stored = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(refresh_token))
        )
    ).scalar_one_or_none()
    if stored is None:
        return  # idempotent: unknown tokens are simply ignored
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == stored.family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await _bind_org(session, stored.org_id)
    await audit.record(
        session,
        "auth.logout",
        org_id=stored.org_id,
        actor_id=stored.user_id,
        ip=meta.ip,
        user_agent=meta.user_agent,
        request_id=meta.request_id,
    )
    await session.commit()
