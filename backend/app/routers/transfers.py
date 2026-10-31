"""Transfer jobs: admin overview, manual retry and pause/resume."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import transfers as transfers_core
from ..admin_ops import audit
from ..db import get_db
from ..deps import require_admin
from ..models import CloudProvider, MediaItem, TransferJob, TransferStatus, User
from ..schemas import TransferControlIn, TransferControlOut, TransferJobOut

router = APIRouter(
    prefix="/api/transfers",
    tags=["transfers"],
    dependencies=[Depends(require_admin)],
)


def enrich_jobs(db: Session, jobs: list[TransferJob]) -> list[TransferJobOut]:
    """Add file, target and folder names for display (also used by the pool)."""
    media_map = dict(db.execute(select(MediaItem.id, MediaItem.filename)).all())
    folder_map = dict(db.execute(select(MediaItem.id, MediaItem.folder_id)).all())
    provider_map = dict(db.execute(select(CloudProvider.id, CloudProvider.name)).all())
    out = []
    for j in jobs:
        out.append(
            TransferJobOut(
                id=j.id,
                media_id=j.media_id,
                provider_id=j.provider_id,
                status=j.status,
                progress=j.progress,
                bytes_transferred=j.bytes_transferred,
                attempts=j.attempts,
                last_error=j.last_error,
                created_at=j.created_at,
                updated_at=j.updated_at,
                split_parts=j.split_parts or 0,
                split_done=j.split_done or 0,
                media_filename=media_map.get(j.media_id, ""),
                provider_name=provider_map.get(j.provider_id, ""),
                folder_id=folder_map.get(j.media_id),
            )
        )
    return out


@router.get("", response_model=list[TransferJobOut])
def list_transfers(
    status_filter: TransferStatus | None = None,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[TransferJobOut]:
    return enrich_jobs(db, transfers_core.list_jobs(db, status_filter))


@router.get("/control", response_model=TransferControlOut)
def get_control(
    _: User = Depends(require_admin), db: Session = Depends(get_db)
) -> TransferControlOut:
    return TransferControlOut(**transfers_core.control_state(db))


@router.post("/control", response_model=TransferControlOut)
def post_control(
    payload: TransferControlIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> TransferControlOut:
    """Pause/resume new uploads or re-queue every failed job on this box."""
    message = transfers_core.apply_control(db, payload.action)
    audit(db, admin, f"transfers.{payload.action}", message)
    return TransferControlOut(**transfers_core.control_state(db), message=message)


@router.post("/{job_id}/retry", response_model=TransferJobOut)
def retry_transfer(
    job_id: int,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> TransferJobOut:
    try:
        job = transfers_core.retry_job(db, job_id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return enrich_jobs(db, [job])[0]
