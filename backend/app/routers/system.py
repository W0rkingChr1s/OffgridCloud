"""System operations: status/health, settings and the audit log (admin only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..admin_ops import audit, disk_usage, get_system_settings
from ..config import get_settings
from ..crypto import encrypt
from ..db import get_db
from ..deps import require_admin
from ..models import AuditEvent, SystemSettings, User
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
        delete_remote_on_local_delete=settings_row.delete_remote_on_local_delete,
        auto_resync=settings_row.auto_resync,
        reconcile_interval=get_settings().reconcile_interval,
        probe_url=settings_row.probe_url,
        webhook_url=settings_row.webhook_url,
        disk=DiskUsageOut(**disk_usage()),
        rclone_available=check_rclone().available,
        notify_on_received=settings_row.notify_on_received,
        notify_on_done=settings_row.notify_on_done,
        notify_on_failed=settings_row.notify_on_failed,
        notify_on_low_space=settings_row.notify_on_low_space,
        telegram_chat_id=settings_row.telegram_chat_id,
        telegram_configured=bool(settings_row.telegram_bot_token_encrypted),
        smtp_host=settings_row.smtp_host,
        smtp_port=settings_row.smtp_port,
        smtp_username=settings_row.smtp_username,
        smtp_from=settings_row.smtp_from,
        smtp_to=settings_row.smtp_to,
        smtp_tls=settings_row.smtp_tls,
        smtp_configured=bool(settings_row.smtp_password_encrypted),
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
    if payload.delete_remote_on_local_delete is not None:
        row.delete_remote_on_local_delete = payload.delete_remote_on_local_delete
        changed.append(f"delete_remote={payload.delete_remote_on_local_delete}")
    if payload.auto_resync is not None:
        row.auto_resync = payload.auto_resync
        changed.append(f"auto_resync={payload.auto_resync}")
    if payload.probe_url is not None:
        row.probe_url = payload.probe_url.strip()
        changed.append("probe_url")
    if payload.webhook_url is not None:
        row.webhook_url = payload.webhook_url.strip()
        changed.append("webhook_url")
    _apply_notify_settings(row, payload, changed)
    if changed:
        db.commit()
        audit(db, admin, "system.update", ", ".join(changed))
    return _status(db)


def _apply_notify_settings(
    row: SystemSettings, payload: SystemSettingsUpdate, changed: list[str]
) -> None:
    """Apply notification fields onto the settings row. Secrets are write-only:
    a non-null value replaces (or, if empty, clears) the stored credential."""
    for flag in ("notify_on_received", "notify_on_done", "notify_on_failed", "notify_on_low_space"):
        value = getattr(payload, flag)
        if value is not None:
            setattr(row, flag, value)
            changed.append(f"{flag}={value}")

    for field in ("telegram_chat_id", "smtp_host", "smtp_username", "smtp_from", "smtp_to"):
        value = getattr(payload, field)
        if value is not None:
            setattr(row, field, value.strip())
            changed.append(field)
    if payload.smtp_port is not None:
        row.smtp_port = payload.smtp_port
        changed.append("smtp_port")
    if payload.smtp_tls is not None:
        row.smtp_tls = payload.smtp_tls
        changed.append(f"smtp_tls={payload.smtp_tls}")

    if payload.telegram_bot_token is not None:
        token = payload.telegram_bot_token.strip()
        row.telegram_bot_token_encrypted = encrypt(token) if token else ""
        changed.append("telegram_bot_token")
    if payload.smtp_password is not None:
        pw = payload.smtp_password
        row.smtp_password_encrypted = encrypt(pw) if pw else ""
        changed.append("smtp_password")


@router.get("/audit", response_model=list[AuditEventOut])
def list_audit(
    _: User = Depends(require_admin), db: Session = Depends(get_db)
) -> list[AuditEvent]:
    return list(
        db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(200))
    )
