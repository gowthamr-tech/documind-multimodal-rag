"""Password hashing (argon2id), JWT access tokens, and opaque refresh tokens."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings
from app.database.models.identity import Role

_hasher = PasswordHasher()  # argon2id with library-default (OWASP-aligned) parameters
# Verified against when the user does not exist, so login timing doesn't reveal valid emails.
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller, as established by a verified access token."""

    user_id: uuid.UUID
    org_id: uuid.UUID
    role: Role


class TokenError(Exception):
    pass


def create_access_token(principal: Principal, settings: Settings) -> tuple[str, int]:
    now = datetime.now(UTC)
    ttl = settings.access_token_ttl_seconds
    claims: dict[str, Any] = {
        "sub": str(principal.user_id),
        "org": str(principal.org_id),
        "role": principal.role.value,
        "typ": "access",
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(seconds=ttl),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(
        claims, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    return token, ttl


def decode_access_token(token: str, settings: Settings) -> Principal:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],  # pinned: never trust the token's own "alg"
            options={"require": ["sub", "org", "role", "typ", "exp", "iat"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if claims.get("typ") != "access":
        raise TokenError("not an access token")
    try:
        return Principal(
            user_id=uuid.UUID(claims["sub"]),
            org_id=uuid.UUID(claims["org"]),
            role=Role(claims["role"]),
        )
    except (ValueError, KeyError) as exc:
        raise TokenError("malformed claims") from exc


def new_refresh_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits of entropy


def hash_refresh_token(token: str) -> str:
    # Refresh tokens are high-entropy random strings, so a fast hash is appropriate here
    # (unlike passwords); only the hash is stored.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
