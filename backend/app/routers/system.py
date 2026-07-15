"""System operations: status/health, settings and the audit log (admin only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..admin_ops import audit, disk_usage, get_system_settings
from ..config import get_settings
from ..crypto import encrypt
from ..db import get_db
from ..deps import require_admin
from ..models import AuditEvent, SystemSettings, User
from ..power import run_power_command
from ..rclone import check_rclone
from ..schemas import (
    AuditEventOut,
    DiskUsageOut,
    PowerActionResult,
    SystemSettingsUpdate,
    SystemStatusOut,
)

router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(require_admin)])


def _status(db: Session) -> SystemStatusOut:
    settings_row = get_system_settings(db)
    settings = get_settings()
    return SystemStatusOut(
        delete_local_after_upload=settings_row.delete_local_after_upload,
        delete_remote_on_local_delete=settings_row.delete_remote_on_local_delete,
        auto_resync=settings_row.auto_resync,
        reconcile_interval=settings.reconcile_interval,
        probe_url=settings_row.probe_url,
        webhook_url=settings_row.webhook_url,
        disk=DiskUsageOut(**disk_usage()),
        rclone_available=check_rclone().available,
        notify_on_received=settings_row.notify_on_received,
        notify_on_done=settings_row.notify_on_done,
        notify_on_failed=settings_row.notify_on_failed,
        notify_on_low_space=settings_row.notify_on_low_space,
        notify_on_startup=settings_row.notify_on_startup,
        notify_on_reconnect=settings_row.notify_on_reconnect,
        notify_on_bandwidth=settings_row.notify_on_bandwidth,
        telegram_chat_id=settings_row.telegram_chat_id,
        telegram_configured=bool(settings_row.telegram_bot_token_encrypted),
        smtp_host=settings_row.smtp_host,
        smtp_port=settings_row.smtp_port,
        smtp_username=settings_row.smtp_username,
        smtp_from=settings_row.smtp_from,
        smtp_to=settings_row.smtp_to,
        smtp_tls=settings_row.smtp_tls,
        smtp_configured=bool(settings_row.smtp_password_encrypted),
        power_restart_service_enabled=bool(settings.restart_service_command.strip()),
        power_reboot_enabled=bool(settings.reboot_command.strip()),
        power_shutdown_enabled=bool(settings.shutdown_command.strip()),
    )


# Power-control actions exposed under POST /api/system/power/{action}. Each maps
# to an operator-configured privileged command; an empty command means the action
# is disabled (button greyed out in the portal, endpoint returns 409).
_POWER_ACTIONS: dict[str, tuple[str, str, str]] = {
    # action slug: (settings attribute, audit action, success message)
    "restart-service": (
        "restart_service_command",
        "system.power.restart_service",
        "OffgridCloud wird neu gestartet …",
    ),
    "reboot": (
        "reboot_command",
        "system.power.reboot",
        "System wird neu gestartet …",
    ),
    "shutdown": (
        "shutdown_command",
        "system.power.shutdown",
        "System wird heruntergefahren …",
    ),
}


@router.post("/power/{action}", response_model=PowerActionResult)
def power_action(
    action: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> PowerActionResult:
    """Run a configured power command (restart service / reboot / shutdown).

    Opt-in: each command must be wired up by the operator (needs root), so an
    unconfigured action returns 409 with a hint to re-run the installer. The
    command is launched detached after a short delay so this response reaches the
    portal before the service (or the whole box) goes down.
    """
    entry = _POWER_ACTIONS.get(action)
    if entry is None:
        raise HTTPException(status_code=404, detail="Unbekannte Aktion.")
    attr, audit_action, message = entry
    command = getattr(get_settings(), attr).strip()
    if not command:
        raise HTTPException(
            status_code=409,
            detail=(
                "Diese Aktion ist deaktiviert (Befehl leer). In der .env den passenden "
                "OGC_*_COMMAND setzen oder den Installer erneut ausführen, um die "
                "sudoers-Regeln einzurichten."
            ),
        )
    audit(db, admin, audit_action, command)
    try:
        run_power_command(command)
    except Exception as exc:  # noqa: BLE001 - surface the launch failure to the UI
        raise HTTPException(status_code=500, detail=f"Start fehlgeschlagen: {exc}") from exc
    return PowerActionResult(started=True, message=message)


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
    for flag in (
        "notify_on_received",
        "notify_on_done",
        "notify_on_failed",
        "notify_on_low_space",
        "notify_on_startup",
        "notify_on_reconnect",
        "notify_on_bandwidth",
    ):
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
