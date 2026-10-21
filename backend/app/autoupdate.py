"""Unattended updates: run the one-click update on its own, at a quiet time.

Opt-in per box (System → Version). A background loop looks every few minutes
and starts ``update.sh`` through the same runner as the portal button
(``updater.start_update``) only when all of this holds:

* the box-local hour is inside the configured window (``start == end`` means
  around the clock; ``22 → 4`` wraps midnight),
* nothing is being moved: no running transfer, no upload that received data
  recently, no card import — the restart would cut them off,
* the uplink is not metered (USB-tethered phone), unless allowed,
* there is no attempt yet today, and the target didn't already fail unattended.

The outcome is reported once on every notification channel (Telegram, e-mail,
webhook) and in the portal's notices. A restart kills this process mid-update,
so the pending report is remembered on disk and sent by the restarted process.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import __version__, _read_channel, _read_commit, _read_version, notices, notify
from .admin_ops import get_system_settings
from .config import get_settings
from .models import SystemSettings, TransferJob, TransferStatus, UploadSession
from .updater import PHASE_RUNNING, PHASE_SUCCESS, check_for_update, read_state, start_update

logger = logging.getLogger(__name__)

AUTO_UPDATE_INTERVAL = 300.0  # seconds between looks; GitHub answers are cached 15 min
# An upload that received data this recently counts as in progress.
UPLOAD_ACTIVE_SECONDS = 600.0

_PENDING_FILE = "auto-update-pending.json"

# What the scheduler waits for, shown in the portal (process-local).
_status = ""


def _pending_path() -> Path:
    return Path(get_settings().data_dir) / _PENDING_FILE


def available() -> bool:
    """Whether this box can update itself at all (not Docker, update wired up)."""
    from .vpn import in_docker

    settings = get_settings()
    return bool(settings.self_update and settings.update_command) and not in_docker()


def in_window(hour: int, start: int, end: int) -> bool:
    """Is ``hour`` inside ``[start, end)``? Wraps midnight; ``start == end`` is all day."""
    if start == end:
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def busy_reason(db: Session, *, now: float | None = None) -> str:
    """Why an update would interrupt work right now, or ``""`` if the box is idle."""
    from . import storage_import

    now = time.time() if now is None else now
    running = db.scalar(
        select(TransferJob.id).where(TransferJob.status == TransferStatus.RUNNING).limit(1)
    )
    if running is not None:
        return "Übertragungen laufen"
    for (temp_path,) in db.execute(select(UploadSession.temp_path)):
        try:
            if now - Path(temp_path).stat().st_mtime < UPLOAD_ACTIVE_SECONDS:
                return "Uploads auf die Box laufen"
        except OSError:
            continue  # abandoned session, file already gone
    if any(j.status in {"pending", "running"} for j in storage_import.list_jobs()):
        return "Karten-Import läuft"
    return ""


def _metered() -> bool:
    from . import network

    try:
        return network.metered_uplink()[0]
    except Exception:  # noqa: BLE001 - unknown uplink: don't block on it
        return False


def _target(info) -> str:
    return str(info.latest or "")[:100]


def evaluate(
    db: Session,
    row: SystemSettings,
    *,
    now: datetime,
    checker=check_for_update,
    metered=None,
) -> tuple[str, str]:
    """Decide one round: ``("", target)`` to update now, else ``(reason, "")``."""
    if not row.auto_update_enabled:
        return "Automatische Updates sind aus.", ""
    if not available():
        return "Auf dieser Box nicht möglich (Docker oder One-Click-Update aus).", ""
    if read_state(get_settings().data_dir).phase == PHASE_RUNNING:
        return "Ein Update läuft gerade.", ""
    start, end = row.auto_update_start_hour, row.auto_update_end_hour
    if not in_window(now.hour, start, end):
        return f"Wartet auf das Zeitfenster ({start:02d}–{end:02d} Uhr).", ""
    if row.auto_update_last_day == now.date().isoformat():
        return "Heute schon versucht, nächster Versuch morgen.", ""
    if not row.auto_update_metered and (metered or _metered)():
        return "Wartet: getaktete Verbindung (Handy).", ""
    busy = busy_reason(db, now=now.timestamp())
    if busy:
        return f"Wartet: {busy}.", ""
    settings = get_settings()
    info = checker(
        __version__, settings.github_repo, channel=_read_channel(), commit=_read_commit()
    )
    if info.error:
        return info.error, ""
    if not info.update_available:
        return f"Aktuell ({info.current}).", ""
    target = _target(info)
    if target and target == row.auto_update_failed_target:
        return f"{target} schlug fehl, wird bis zur nächsten Version übersprungen.", ""
    return "", target or "neue Version"


def _announce(db: Session, ok: bool, title: str, message: str, payload: dict) -> None:
    notices.push("success" if ok else "error", title, message)
    notify.dispatch(get_system_settings(db), "server.update", title, message, payload)


def report_result(db: Session) -> bool:
    """Send the outcome of a finished unattended update once. True if reported."""
    path = _pending_path()
    if not path.is_file():
        return False
    state = read_state(get_settings().data_dir)
    if state.phase == PHASE_RUNNING:
        return False
    try:
        target = str(json.loads(path.read_text("utf-8")).get("target") or "")
    except (OSError, ValueError):
        target = ""
    ok = state.phase == PHASE_SUCCESS
    row = get_system_settings(db)
    if ok:
        row.auto_update_failed_target = ""
        title = "Automatisches Update abgeschlossen"
    else:
        # Don't retry the same broken release every night.
        row.auto_update_failed_target = target
        title = "Automatisches Update fehlgeschlagen"
    db.commit()
    versions = f"v{state.from_version or '?'} → v{state.to_version or '?'}"
    message = f"{versions}\n{state.message}".strip()
    if not ok:
        message += "\nDetails: Portal → System → Update-Protokoll."
    try:
        _announce(
            db,
            ok,
            title,
            message,
            {
                "event": "server.update",
                "ok": ok,
                "phase": state.phase,
                "from_version": state.from_version,
                "to_version": state.to_version,
                "target": target,
            },
        )
    finally:
        path.unlink(missing_ok=True)
    return True


def tick(db: Session, *, now: datetime | None = None, checker=check_for_update) -> str:
    """One scheduler round. Returns the status line (also kept for the portal)."""
    global _status
    now = datetime.now() if now is None else now
    report_result(db)
    row = get_system_settings(db)
    reason, target = evaluate(db, row, now=now, checker=checker)
    if reason:
        _status = reason
        return reason

    settings = get_settings()
    row.auto_update_last_day = now.date().isoformat()
    db.commit()
    _pending_path().write_text(json.dumps({"target": target}), "utf-8")
    try:
        start_update(
            settings.data_dir,
            settings.update_command,
            __version__,
            current_version=_read_version,
        )
    except RuntimeError as exc:
        _pending_path().unlink(missing_ok=True)
        _status = f"Start fehlgeschlagen: {exc}"
        return _status
    logger.info("Unattended update to %s started", target)
    _status = f"Update auf {target} gestartet."
    return _status


def status() -> str:
    return _status


def reset_status() -> None:
    global _status
    _status = ""


async def auto_update_loop(stop: asyncio.Event, interval: float = AUTO_UPDATE_INTERVAL) -> None:
    """Background scheduler (see module docstring). Never raises."""
    from .db import SessionLocal

    def round_() -> None:
        with SessionLocal() as db:
            tick(db)

    while not stop.is_set():
        try:
            await asyncio.to_thread(round_)
        except Exception:  # noqa: BLE001 - keep the scheduler alive
            logger.exception("Auto-update round failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass
