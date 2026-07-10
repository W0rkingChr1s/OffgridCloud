"""Update checks and (opt-in) one-click self-update — admin only."""

from __future__ import annotations

import shlex
import subprocess

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import __version__
from ..admin_ops import audit
from ..config import get_settings
from ..db import get_db
from ..deps import require_admin
from ..models import User
from ..schemas import UpdateApplyResult, UpdateInfoOut
from ..updater import check_for_update

router = APIRouter(prefix="/api/updates", tags=["updates"], dependencies=[Depends(require_admin)])


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
        # Detached: the command is expected to rebuild and restart the service,
        # which will terminate this process — so we don't wait on it.
        subprocess.Popen(  # noqa: S603 - operator-configured command
            shlex.split(settings.update_command),
            start_new_session=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Start fehlgeschlagen: {exc}") from exc
    return UpdateApplyResult(
        started=True,
        message="Update gestartet. Der Dienst startet nach dem Bau neu — kurz warten.",
    )
