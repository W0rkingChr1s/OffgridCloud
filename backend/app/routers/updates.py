"""Update checks and (opt-in) one-click self-update — admin only."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import __version__
from ..admin_ops import audit
from ..config import get_settings
from ..db import get_db
from ..deps import require_admin
from ..models import User
from ..schemas import UpdateApplyResult, UpdateInfoOut, UpdateProgressOut
from ..updater import check_for_update, read_log_tail, read_state, start_update

router = APIRouter(prefix="/api/updates", tags=["updates"], dependencies=[Depends(require_admin)])


def _current_version() -> str:
    """Re-read the deployed version fresh (a rebuild restamps it)."""
    from .. import _read_version

    return _read_version()


@router.get("", response_model=UpdateInfoOut)
def get_update_status(
    force: bool = False, _: User = Depends(require_admin)
) -> UpdateInfoOut:
    """Current version vs. latest GitHub release. Never fails hard (offline-safe)."""
    settings = get_settings()
    info = check_for_update(__version__, settings.github_repo, use_cache=not force)
    return UpdateInfoOut(
        current=info.current,
        latest=info.latest,
        update_available=info.update_available,
        release_url=info.release_url,
        release_name=info.release_name,
        published_at=info.published_at,
        notes=info.notes,
        error=info.error,
        self_update_enabled=bool(settings.self_update and settings.update_command),
    )


@router.post("/apply", response_model=UpdateApplyResult)
def apply_update(
    admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> UpdateApplyResult:
    """Run the configured update command detached (opt-in).

    Disabled unless the operator wired it up (``OGC_SELF_UPDATE=true`` +
    ``OGC_UPDATE_COMMAND``), because updating a systemd service from the web
    needs elevated rights. When off, we return the manual command instead.

    The command's output is captured to a log and its progress persisted so the
    portal can show live status and the final result across the service restart
    the update itself triggers (see ``GET /api/updates/progress``).
    """
    settings = get_settings()
    if not (settings.self_update and settings.update_command):
        raise HTTPException(
            status_code=409,
            detail=(
                "One-Click-Update ist nicht aktiviert. Auf dem Server ausführen: "
                "sudo /opt/offgridcloud/src/deploy/update.sh  (oder Installer mit "
                "--self-update erneut ausführen)."
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
    from ..updater import PHASE_RUNNING

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
