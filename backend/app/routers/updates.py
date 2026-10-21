"""Update checks and (opt-in) one-click self-update — admin only."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import __version__, _read_channel, _read_commit, autoupdate
from ..admin_ops import audit, get_system_settings
from ..config import get_settings
from ..db import get_db
from ..deps import require_admin
from ..models import User
from ..schemas import (
    AutoUpdateIn,
    AutoUpdateOut,
    UpdateApplyResult,
    UpdateInfoOut,
    UpdateProgressOut,
)
from ..updater import (
    PHASE_RUNNING,
    check_for_update,
    clear_state,
    read_log_tail,
    read_state,
    start_update,
)
from ..vpn import in_docker

router = APIRouter(prefix="/api/updates", tags=["updates"], dependencies=[Depends(require_admin)])


def _current_version() -> str:
    """Re-read the deployed version fresh (a rebuild restamps it)."""
    from .. import _read_version

    return _read_version()


@router.get("", response_model=UpdateInfoOut)
def get_update_status(
    force: bool = False, _: User = Depends(require_admin)
) -> UpdateInfoOut:
    """Current version vs. latest GitHub release (or main, on the main channel).

    Never fails hard (offline-safe).
    """
    settings = get_settings()
    info = check_for_update(
        __version__,
        settings.github_repo,
        channel=_read_channel(),
        commit=_read_commit(),
        use_cache=not force,
    )
    return UpdateInfoOut(
        current=info.current,
        channel=info.channel,
        commit=info.commit,
        latest=info.latest,
        behind_by=info.behind_by,
        update_available=info.update_available,
        release_url=info.release_url,
        release_name=info.release_name,
        published_at=info.published_at,
        notes=info.notes,
        error=info.error,
        self_update_enabled=bool(settings.self_update and settings.update_command),
        in_docker=in_docker(),
    )


@router.post("/apply", response_model=UpdateApplyResult)
def apply_update(
    admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> UpdateApplyResult:
    """Run the configured update command detached (opt-in).

    On by default; the installer wires up the matching NOPASSWD sudoers rule so
    ``sudo update.sh`` can run headless. Only disabled when the operator opts out
    (``OGC_SELF_UPDATE=false`` or an empty ``OGC_UPDATE_COMMAND``, e.g. on
    Docker) — then we return the manual command instead.

    The command's output is captured to a log and its progress persisted so the
    portal can show live status and the final result across the service restart
    the update itself triggers (see ``GET /api/updates/progress``).
    """
    settings = get_settings()
    if not (settings.self_update and settings.update_command):
        raise HTTPException(
            status_code=409,
            detail=(
                "One-Click-Update ist deaktiviert. Auf dem Server ausführen: "
                "sudo /opt/offgridcloud/src/deploy/update.sh"
            ),
        )
    audit(db, admin, "system.update.apply", settings.update_command)
    try:
        start_update(
            settings.data_dir,
            settings.update_command,
            __version__,
            current_version=_current_version,
        )
    except RuntimeError as exc:
        # Already running, or the command failed to launch — surface it as 409
        # so the UI shows the reason instead of a generic error.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return UpdateApplyResult(
        started=True,
        message="Update gestartet. Der Dienst startet nach dem Bau neu — Fortschritt unten.",
    )


@router.get("/progress", response_model=UpdateProgressOut)
def update_progress(_: User = Depends(require_admin)) -> UpdateProgressOut:
    """Live status + log tail of the running (or last) self-update.

    Polled by the portal so the operator sees the whole update — including
    success/failure and the transcript — without opening a terminal.
    """
    settings = get_settings()
    state = read_state(settings.data_dir)
    return UpdateProgressOut(
        phase=state.phase,
        running=state.phase == PHASE_RUNNING,
        from_version=state.from_version,
        to_version=state.to_version,
        message=state.message,
        returncode=state.returncode,
        started_at=state.started_at,
        finished_at=state.finished_at,
        log=read_log_tail(settings.data_dir),
    )


@router.post("/dismiss", response_model=UpdateProgressOut)
def dismiss_update(_: User = Depends(require_admin)) -> UpdateProgressOut:
    """Clear a finished update result so the portal card can be dismissed.

    Refuses while an update is still running (nothing to dismiss yet). Resets the
    persisted state to idle and drops the log — used by the "Verstanden" button.
    """
    settings = get_settings()
    if read_state(settings.data_dir).phase == PHASE_RUNNING:
        raise HTTPException(status_code=409, detail="Update läuft noch — bitte warten.")
    clear_state(settings.data_dir)
    return UpdateProgressOut(phase="idle", running=False)


def _auto_out(db: Session) -> AutoUpdateOut:
    row = get_system_settings(db)
    status = autoupdate.status()
    if not row.auto_update_enabled:
        status = "Automatische Updates sind aus."
    elif not status:
        status = "Wird beim nächsten Durchlauf geprüft (alle 5 Minuten)."
    return AutoUpdateOut(
        enabled=row.auto_update_enabled,
        start_hour=row.auto_update_start_hour,
        end_hour=row.auto_update_end_hour,
        allow_metered=row.auto_update_metered,
        available=autoupdate.available(),
        status=status,
        last_day=row.auto_update_last_day,
        failed_target=row.auto_update_failed_target,
    )


@router.get("/auto", response_model=AutoUpdateOut)
def get_auto_update(db: Session = Depends(get_db)) -> AutoUpdateOut:
    """Unattended-update settings and what the scheduler is waiting for."""
    return _auto_out(db)


@router.put("/auto", response_model=AutoUpdateOut)
def set_auto_update(
    payload: AutoUpdateIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> AutoUpdateOut:
    """Change the unattended-update settings (opt-in, see ``app.autoupdate``)."""
    if payload.enabled and not autoupdate.available():
        raise HTTPException(
            status_code=409,
            detail="Automatische Updates gehen nur mit aktivem One-Click-Update (nicht in Docker).",
        )
    row = get_system_settings(db)
    changed = []
    if payload.enabled is not None:
        if payload.enabled and not row.auto_update_enabled:
            # Switching on again is the way to retry a release that failed.
            row.auto_update_failed_target = ""
            row.auto_update_last_day = ""
        row.auto_update_enabled = payload.enabled
        changed.append(f"enabled={payload.enabled}")
    if payload.start_hour is not None:
        row.auto_update_start_hour = payload.start_hour
        changed.append(f"start={payload.start_hour}")
    if payload.end_hour is not None:
        row.auto_update_end_hour = payload.end_hour
        changed.append(f"end={payload.end_hour}")
    if payload.allow_metered is not None:
        row.auto_update_metered = payload.allow_metered
        changed.append(f"metered={payload.allow_metered}")
    if changed:
        db.commit()
        autoupdate.reset_status()
        audit(db, admin, "system.update.auto", ", ".join(changed))
    return _auto_out(db)
