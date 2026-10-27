"""Auth dependencies: current user resolution and role guards."""

from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db import get_db
from .models import Role, User
from .security import decode_access_token

_bearer = HTTPBearer(auto_error=True)

# Scope of the short-lived token the upload handover hands out (app.handover).
UPLOAD_SCOPE = "upload"


def _invalid() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _payload(credentials: HTTPAuthorizationCredentials, scope: str | None = None) -> dict:
    try:
        payload = decode_access_token(credentials.credentials, scope=scope)
        int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise _invalid() from None
    return payload


def _active_user(db: Session, payload: dict) -> User:
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.active:
        raise _invalid()
    return user


def get_token_payload(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """Claims of a regular (unscoped) token, e.g. ``dev`` when it came via BLE."""
    return _payload(credentials)


def get_current_user(
    payload: dict = Depends(get_token_payload),
    db: Session = Depends(get_db),
) -> User:
    return _active_user(db, payload)


def get_upload_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Like :func:`get_current_user`, but also accepts the handover's upload token."""
    try:
        return _active_user(db, _payload(credentials))
    except HTTPException:
        payload = _payload(credentials, UPLOAD_SCOPE)
    from . import handover

    if not handover.token_valid(db, payload):
        raise _invalid()
    return _active_user(db, payload)


def user_from_query_token(db: Session, token: str) -> User:
    """Resolve a user from a JWT passed in the query string.

    Used by download/thumbnail links that must live in a plain ``<a href>`` /
    ``<img src>`` where an ``Authorization`` header can't be set.
    """
    invalid = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    try:
        payload = decode_access_token(token)
        user = db.get(User, int(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise invalid from None
    if user is None or not user.active:
        raise invalid
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required",
        )
    return user
