"""System operations: status/health, settings and the audit log (admin only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..admin_ops import audit, disk_usage, get_system_settings
from ..db import get_db
from ..deps import require_admin
from ..models import AuditEvent, User
from ..rclone import check_rclone
from ..schemas import (
    AuditEventOut,
    DiskUsageOut,
    SystemSettingsUpdate,
    SystemStatusOut,
)

router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(require_admin)])


def _status(db: Session) -> SystemStatusOut:
    settings_row = get_system_settings(db)
    return SystemStatusOut(
        delete_local_after_upload=settings_row.delete_local_after_upload,
        probe_url=settings_row.probe_url,
        webhook_url=settings_row.webhook_url,
        disk=DiskUsageOut(**disk_usage()),
        rclone_available=check_rclone().available,
    )


@router.get("", response_model=SystemStatusOut)
def get_status(_: User = Depends(require_admin), db: Session = Depends(get_db)) -> SystemStatusOut:
    return _status(db)


@router.put("", response_model=SystemStatusOut)
def update_settings(
    payload: SystemSettingsUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> SystemStatusOut:
    row = get_system_settings(db)
    changed = []
    if payload.delete_local_after_upload is not None:
        row.delete_local_after_upload = payload.delete_local_after_upload
        changed.append(f"delete_local={payload.delete_local_after_upload}")
    if payload.probe_url is not None:
        row.probe_url = payload.probe_url.strip()
        changed.append("probe_url")
    if payload.webhook_url is not None:
        row.webhook_url = payload.webhook_url.strip()
        changed.append("webhook_url")
    if changed:
        db.commit()
        audit(db, admin, "system.update", ", ".join(changed))
    return _status(db)


@router.get("/audit", response_model=list[AuditEventOut])
def list_audit(
    _: User = Depends(require_admin), db: Session = Depends(get_db)
) -> list[AuditEvent]:
    return list(
        db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(200))
    )
