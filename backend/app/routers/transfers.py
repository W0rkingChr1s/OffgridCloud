"""Transfer jobs: admin overview and manual retry."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import CloudProvider, MediaItem, TransferJob, TransferStatus, User
from ..schemas import TransferJobOut

router = APIRouter(
    prefix="/api/transfers",
    tags=["transfers"],
    dependencies=[Depends(require_admin)],
)


def _enrich(db: Session, jobs: list[TransferJob]) -> list[TransferJobOut]:
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
    stmt = select(TransferJob).order_by(TransferJob.updated_at.desc()).limit(200)
    if status_filter is not None:
        stmt = stmt.where(TransferJob.status == status_filter)
    return _enrich(db, list(db.scalars(stmt)))


@router.post("/{job_id}/retry", response_model=TransferJobOut)
def retry_transfer(
    job_id: int,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> TransferJobOut:
    job = db.get(TransferJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.status == TransferStatus.RUNNING:
        raise HTTPException(status_code=400, detail="Job läuft bereits")
    job.status = TransferStatus.QUEUED
    job.attempts = 0
    job.last_error = ""
    job.next_attempt_at = None
    db.commit()
    db.refresh(job)
    return _enrich(db, [job])[0]
