"""Transfer orchestration: enqueue jobs, run them via rclone, manage retries.

A single background worker processes one job at a time (Pi-friendly). The pure
state-machine in ``process_job`` is decoupled from rclone via an injectable
``upload_fn`` so it can be tested without the binary.
"""

from __future__ import annotations

import asyncio
import json
import logging
import posixpath
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .config import get_settings
from .crypto import decrypt
from .db import SessionLocal
from .models import (
    CloudProvider,
    FolderProviderLink,
    MediaItem,
    MediaStatus,
    TransferJob,
    TransferStatus,
)
from .providers_registry import get_type
from .rclone import UploadResult, run_upload

logger = logging.getLogger("offgridcloud.transfers")

# Signature: (local_path, rclone_options, dest) -> UploadResult
UploadFn = Callable[[str, dict, str], UploadResult]


def _now() -> datetime:
    return datetime.now(UTC)


# --- Enqueue --------------------------------------------------------------


def enqueue_for_media(db: Session, media: MediaItem) -> int:
    """Create queued jobs for each enabled provider linked to the media's folder."""
    links = db.scalars(
        select(FolderProviderLink).where(
            FolderProviderLink.folder_id == media.folder_id,
            FolderProviderLink.enabled.is_(True),
        )
    ).all()
    created = 0
    for link in links:
        exists = db.scalar(
            select(TransferJob).where(
                TransferJob.media_id == media.id,
                TransferJob.provider_id == link.provider_id,
            )
        )
        if exists is None:
            db.add(TransferJob(media_id=media.id, provider_id=link.provider_id))
            created += 1
    db.flush()
    recompute_media_status(db, media.id)
    return created


def enqueue_for_link(db: Session, link: FolderProviderLink) -> int:
    """Backfill jobs for media already in the folder when a provider is linked."""
    media_items = db.scalars(
        select(MediaItem).where(MediaItem.folder_id == link.folder_id)
    ).all()
    created = 0
    for media in media_items:
        exists = db.scalar(
            select(TransferJob).where(
                TransferJob.media_id == media.id,
                TransferJob.provider_id == link.provider_id,
            )
        )
        if exists is None:
            db.add(TransferJob(media_id=media.id, provider_id=link.provider_id))
            created += 1
        recompute_media_status(db, media.id)
    db.flush()
    return created


# --- Status aggregation ---------------------------------------------------


def recompute_media_status(db: Session, media_id: int) -> None:
    media = db.get(MediaItem, media_id)
    if media is None:
        return
    statuses = list(
        db.scalars(select(TransferJob.status).where(TransferJob.media_id == media_id))
    )
    if not statuses:
        media.status = MediaStatus.RECEIVED
    elif TransferStatus.RUNNING in statuses:
        media.status = MediaStatus.UPLOADING
    elif TransferStatus.QUEUED in statuses:
        media.status = MediaStatus.QUEUED
    elif all(s == TransferStatus.DONE for s in statuses):
        media.status = MediaStatus.DONE
    else:
        media.status = MediaStatus.FAILED


# --- Job processing -------------------------------------------------------


def _build_dest(dest_path: str, filename: str) -> str:
    return posixpath.join(dest_path.strip("/"), filename) if dest_path.strip("/") else filename


def _rclone_upload_fn(local_path: str, options: dict, dest: str) -> UploadResult:
    return run_upload(local_path, options, dest)


def process_job(
    db: Session, job: TransferJob, upload_fn: UploadFn = _rclone_upload_fn
) -> TransferJob:
    """Run one transfer job through its state machine. Commits its own changes."""
    media = db.get(MediaItem, job.media_id)
    provider = db.get(CloudProvider, job.provider_id)
    if media is None or provider is None:
        job.status = TransferStatus.FAILED
        job.last_error = "Medium oder Provider nicht gefunden"
        db.commit()
        return job

    pt = get_type(provider.type)
    if pt is None:
        job.status = TransferStatus.FAILED
        job.last_error = f"Unbekannter Provider-Typ '{provider.type}'"
        db.commit()
        return job

    link = db.scalar(
        select(FolderProviderLink).where(
            FolderProviderLink.folder_id == media.folder_id,
            FolderProviderLink.provider_id == provider.id,
        )
    )
    dest_path = link.dest_path if link else ""

    job.status = TransferStatus.RUNNING
    job.attempts += 1
    db.commit()
    recompute_media_status(db, media.id)
    db.commit()

    try:
        config = json.loads(decrypt(provider.config_encrypted) or "{}")
    except json.JSONDecodeError:
        config = {}
    options = pt.to_rclone_options(config)
    dest = _build_dest(dest_path, media.filename)

    result = upload_fn(media.stored_path, options, dest)

    settings = get_settings()
    if result.ok:
        job.status = TransferStatus.DONE
        job.progress = 1.0
        job.bytes_transferred = result.bytes_transferred or media.size
        job.last_error = ""
        job.next_attempt_at = None
    else:
        job.last_error = result.message
        if job.attempts >= settings.worker_max_attempts:
            job.status = TransferStatus.FAILED
            job.next_attempt_at = None
        else:
            job.status = TransferStatus.QUEUED
            backoff = min(300, 5 * (2 ** (job.attempts - 1)))
            job.next_attempt_at = _now() + timedelta(seconds=backoff)
    db.commit()
    recompute_media_status(db, media.id)
    db.commit()
    return job


# --- Worker loop ----------------------------------------------------------


def _pick_eligible(db: Session) -> TransferJob | None:
    return db.scalar(
        select(TransferJob)
        .where(
            TransferJob.status == TransferStatus.QUEUED,
            or_(TransferJob.next_attempt_at.is_(None), TransferJob.next_attempt_at <= _now()),
        )
        .order_by(TransferJob.created_at)
        .limit(1)
    )


def recover_running_jobs() -> None:
    """On startup, requeue jobs left 'running' by a previous crash/restart."""
    with SessionLocal() as db:
        stuck = db.scalars(
            select(TransferJob).where(TransferJob.status == TransferStatus.RUNNING)
        ).all()
        for job in stuck:
            job.status = TransferStatus.QUEUED
            job.next_attempt_at = None
        if stuck:
            db.commit()
            logger.info("Requeued %d interrupted transfer job(s)", len(stuck))


def process_one() -> bool:
    """Process a single eligible job. Returns True if one was handled."""
    with SessionLocal() as db:
        job = _pick_eligible(db)
        if job is None:
            return False
        process_job(db, job)
        return True


async def worker_loop(stop: asyncio.Event) -> None:
    interval = get_settings().worker_poll_interval
    recover_running_jobs()
    logger.info("Transfer worker started")
    while not stop.is_set():
        try:
            handled = await asyncio.to_thread(process_one)
        except Exception:  # pragma: no cover - keep the worker alive
            logger.exception("Transfer worker error")
            handled = False
        if not handled:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                pass
    logger.info("Transfer worker stopped")
