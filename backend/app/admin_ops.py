"""Audit logging and system-settings helpers."""

from __future__ import annotations

import shutil

from sqlalchemy.orm import Session

from .config import get_settings
from .models import AuditEvent, SystemSettings, User

# Free space below this fraction (or absolute bytes) is flagged as low.
LOW_SPACE_FRACTION = 0.10
LOW_SPACE_MIN_BYTES = 500 * 1024 * 1024  # 500 MiB


def audit(db: Session, user: User | None, action: str, detail: str = "") -> None:
    """Append an audit record. Safe to call after the main commit."""
    db.add(
        AuditEvent(
            user_id=user.id if user else None,
            user_email=user.email if user else "",
            action=action,
            detail=detail[:2000],
        )
    )
    db.commit()


def get_system_settings(db: Session) -> SystemSettings:
    row = db.get(SystemSettings, 1)
    if row is None:
        row = SystemSettings(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def ensure_system_settings() -> None:
    from .db import SessionLocal

    with SessionLocal() as db:
        if db.get(SystemSettings, 1) is None:
            db.add(SystemSettings(id=1))
            db.commit()


def disk_usage() -> dict:
    """Disk usage of the media buffer directory."""
    path = get_settings().buffer_dir
    path.mkdir(parents=True, exist_ok=True)
    total, used, free = shutil.disk_usage(path)
    percent_used = (used / total * 100) if total else 0.0
    low = free < LOW_SPACE_MIN_BYTES or (total and free / total < LOW_SPACE_FRACTION)
    return {
        "total": total,
        "used": used,
        "free": free,
        "percent_used": round(percent_used, 1),
        "low_space": bool(low),
    }
