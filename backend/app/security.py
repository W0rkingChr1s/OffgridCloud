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


def create_access_token(*, user_id: int, role: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL,
    }
    return jwt.encode(payload, get_settings().secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and validate a token. Raises jwt.PyJWTError on failure."""
    return jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])
