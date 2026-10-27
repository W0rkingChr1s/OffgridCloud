"""Password hashing (bcrypt) and JWT access tokens."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from .config import get_settings

ALGORITHM = "HS256"
ACCESS_TOKEN_TTL = timedelta(hours=12)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(
    *,
    user_id: int,
    role: str,
    ttl: timedelta = ACCESS_TOKEN_TTL,
    claims: dict | None = None,
) -> str:
    """``claims`` adds e.g. ``dev`` (BLE device) or ``scope`` (upload handover)."""
    now = datetime.now(UTC)
    payload = {
        **(claims or {}),
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, get_settings().secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str, *, scope: str | None = None) -> dict:
    """Decode and validate a token. Raises jwt.PyJWTError on failure.

    A token carrying a ``scope`` claim (the upload handover's) is only accepted
    where exactly that scope is asked for, so it never opens the rest of the API.
    """
    payload = jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])
    if payload.get("scope") != scope:
        raise jwt.InvalidTokenError("token scope not accepted here")
    return payload
