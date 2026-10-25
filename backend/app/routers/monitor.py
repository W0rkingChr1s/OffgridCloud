"""Health monitor API: snapshot, history, Prometheus/PRTG and monitoring tokens.

Read access (``/api/monitor``, ``/history``, ``/metrics``) takes either an
admin session or a monitoring token — as ``Authorization: Bearer <token>`` or,
for tools that can't set headers, ``?token=``. Token management is admin only.
The coarse, unauthenticated liveness check stays at ``/api/health``.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import health
from ..admin_ops import audit
from ..db import get_db
from ..deps import require_admin
from ..models import MonitorToken, Role, User
from ..schemas import MonitorTokenCreate, MonitorTokenCreated, MonitorTokenOut
from ..security import decode_access_token

router = APIRouter(prefix="/api/monitor", tags=["monitor"])

TOKEN_PREFIX = "ogcm_"
_LAST_USED_EVERY = timedelta(seconds=60)
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _presented_token(request: Request, token: str | None) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return token or None


def require_monitor_access(
    request: Request,
    token: str | None = Query(None, description="Monitoring-Token, falls kein Header möglich ist"),
    db: Session = Depends(get_db),
) -> None:
    """Admin session or a monitoring token; 401 otherwise."""
    raw = _presented_token(request, token)
    denied = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Monitoring-Token oder Admin-Anmeldung erforderlich",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not raw:
        raise denied
    if raw.startswith(TOKEN_PREFIX):
        row = db.scalar(select(MonitorToken).where(MonitorToken.token_hash == _hash(raw)))
        if row is None:
            raise denied
        now = _utcnow()
        if row.last_used_at is None or now - row.last_used_at >= _LAST_USED_EVERY:
            row.last_used_at = now
            db.commit()
        return
    try:
        user = db.get(User, int(decode_access_token(raw)["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise denied from None
    if user is None or not user.active:
        raise denied
    if user.role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Administrator privileges required"
        )


@router.get("", dependencies=[Depends(require_monitor_access)])
def snapshot(
    format: str = Query("json", pattern="^(json|prometheus|prtg)$"),
    db: Session = Depends(get_db),
):
    """The full health snapshot; ``?format=prometheus|prtg`` for monitoring tools."""
    snap = health.collect(db)
    if format == "prometheus":
        return PlainTextResponse(health.to_prometheus(snap), media_type=PROMETHEUS_CONTENT_TYPE)
    if format == "prtg":
        return health.to_prtg(snap)
    return snap


@router.get("/metrics", dependencies=[Depends(require_monitor_access)])
def metrics(db: Session = Depends(get_db)) -> PlainTextResponse:
    """Prometheus scrape target (same as ``?format=prometheus``)."""
    return PlainTextResponse(
        health.to_prometheus(health.collect(db)), media_type=PROMETHEUS_CONTENT_TYPE
    )


@router.get("/history", dependencies=[Depends(require_monitor_access)])
def history() -> dict:
    """One point per minute for the last 24 h (in memory, empty after a restart)."""
    return {"interval_seconds": int(health.HISTORY_INTERVAL), "points": health.history()}


# --- Monitoring tokens (admin) --------------------------------------------


@router.get("/tokens", response_model=list[MonitorTokenOut])
def list_tokens(
    _: User = Depends(require_admin), db: Session = Depends(get_db)
) -> list[MonitorTokenOut]:
    rows = db.scalars(select(MonitorToken).order_by(MonitorToken.name))
    return [MonitorTokenOut.model_validate(r) for r in rows]


@router.post("/tokens", response_model=MonitorTokenCreated, status_code=201)
def create_token(
    payload: MonitorTokenCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> MonitorTokenCreated:
    """Issue a read-only token. The plaintext is returned only in this response."""
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = MonitorToken(name=payload.name.strip(), token_hash=_hash(raw), hint=raw[-4:])
    db.add(row)
    db.commit()
    db.refresh(row)
    audit(db, admin, "monitor.token_create", row.name)
    return MonitorTokenCreated(**MonitorTokenOut.model_validate(row).model_dump(), token=raw)


@router.delete(
    "/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
def delete_token(
    token_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    row = db.get(MonitorToken, token_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    name = row.name
    db.delete(row)
    db.commit()
    audit(db, admin, "monitor.token_revoke", name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
