"""Transfer orchestration: enqueue jobs, run them via rclone, manage retries.

A single background worker processes one job at a time (Pi-friendly). The pure
state-machine in ``process_job`` is decoupled from rclone via an injectable
``upload_fn`` so it can be tested without the binary.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import posixpath
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import bandwidth
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
from .rclone import DeleteResult, UploadResult, delete_remote, run_upload

logger = logging.getLogger("offgridcloud.transfers")

# Progress callback: (bytes_done, total_bytes, kbps) -> None
ProgressCb = Callable[[int, int, float], None]
# Signature: (local_path, rclone_options, dest, bwlimit_kbps, on_progress) -> UploadResult
UploadFn = Callable[[str, dict, str, int, ProgressCb | None], UploadResult]


# --- Live progress registry (in-memory, thread-safe) ----------------------
# Lets the SSE stream report byte-level progress of the running job without
# hammering the database.
_live_lock = threading.Lock()
_live: dict[int, dict] = {}


def set_live(job_id: int, bytes_done: int, total: int, kbps: float) -> None:
    with _live_lock:
        _live[job_id] = {"bytes": bytes_done, "total": total, "kbps": kbps}


def clear_live(job_id: int) -> None:
    with _live_lock:
        _live.pop(job_id, None)


def get_live() -> dict[int, dict]:
    with _live_lock:
        return {k: dict(v) for k, v in _live.items()}


def _now() -> datetime:
    return datetime.now(UTC)


def _utcnow_naive() -> datetime:
    return datetime.utcnow()


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
            db.add(
                TransferJob(
                    media_id=media.id,
                    provider_id=link.provider_id,
                    priority=link.priority,
                )
            )
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
            db.add(
                TransferJob(
                    media_id=media.id,
                    provider_id=link.provider_id,
                    priority=link.priority,
                )
            )
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


def _rclone_upload_fn(
    local_path: str,
    options: dict,
    dest: str,
    bwlimit_kbps: int,
    on_progress: ProgressCb | None = None,
) -> UploadResult:
    return run_upload(local_path, options, dest, bwlimit_kbps, on_progress)


def process_job(
    db: Session,
    job: TransferJob,
    upload_fn: UploadFn = _rclone_upload_fn,
    bwlimit_kbps: int = 0,
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

    job_id = job.id

    def _on_progress(bytes_done: int, total: int, kbps: float) -> None:
        set_live(job_id, bytes_done, total, kbps)

    try:
        result = upload_fn(media.stored_path, options, dest, bwlimit_kbps, _on_progress)
    finally:
        clear_live(job_id)

    # Feed observed throughput back into the bandwidth gate.
    if result.kbps > 0:
        bandwidth.record_measurement(db, result.kbps)

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
    maybe_notify(db, media.id)
    maybe_notify_failed(db, media.id)
    maybe_delete_local(db, media.id)
    return job


def _format_bytes(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def _media_payload(media: MediaItem, event: str) -> dict:
    return {
        "event": event,
        "media_id": media.id,
        "folder_id": media.folder_id,
        "filename": media.filename,
        "size": media.size,
        "sha256": media.sha256,
    }


def notify_received(db: Session, media_id: int) -> None:
    """Notify (if enabled) that a fresh upload was accepted and queued."""
    from . import notify

    media = db.get(MediaItem, media_id)
    if media is None:
        return
    notify.notify_event(
        db,
        "media.received",
        "Upload angenommen",
        f'„{media.filename}" wurde angenommen — Cloud-Transfer beginnt.',
        _media_payload(media, "media.received"),
    )


def maybe_notify(
    db: Session,
    media_id: int,
    send_fn: Callable[[str, dict], None] | None = None,
) -> None:
    """Notify once when a media item finishes uploading everywhere.

    Best-effort across every configured channel (webhook/Telegram/e-mail). The
    ``send_fn`` override replaces just the webhook transport (kept for tests).
    """
    from . import notify

    media = db.get(MediaItem, media_id)
    if media is None or media.status != MediaStatus.DONE or media.notified:
        return
    senders = None
    if send_fn is not None:
        senders = notify.Senders(
            webhook=send_fn, telegram=notify._send_telegram, email=notify._send_email
        )
    notified = notify.notify_event(
        db,
        "media.done",
        "Upload fertig",
        f'„{media.filename}" wurde in alle Ziele hochgeladen.',
        _media_payload(media, "media.done"),
        **({"senders": senders} if senders is not None else {}),
    )
    if notified:
        media.notified = True
        db.commit()


def maybe_notify_failed(db: Session, media_id: int) -> None:
    """Notify once when a media item's transfer has finally failed."""
    from . import notify

    media = db.get(MediaItem, media_id)
    if media is None or media.status != MediaStatus.FAILED or media.notified_failed:
        return
    notified = notify.notify_event(
        db,
        "media.failed",
        "Transfer fehlgeschlagen",
        f'„{media.filename}" konnte nicht hochgeladen werden (nach mehreren Versuchen).',
        _media_payload(media, "media.failed"),
    )
    if notified:
        media.notified_failed = True
        db.commit()


def check_low_space(db: Session) -> None:
    """Send a one-shot alert when the buffer runs low; re-arm once it recovers."""
    from . import notify
    from .admin_ops import disk_usage, get_system_settings

    settings = get_system_settings(db)
    usage = disk_usage()
    if usage["low_space"] and not settings.low_space_notified:
        free = _format_bytes(usage["free"])
        notify.dispatch(
            settings,
            "disk.low",
            "Speicher wird knapp",
            f"Nur noch {free} freier Puffer-Speicher ({usage['percent_used']}% belegt).",
            {"event": "disk.low", **usage},
        )
        settings.low_space_notified = True
        db.commit()
    elif not usage["low_space"] and settings.low_space_notified:
        settings.low_space_notified = False  # re-arm for the next episode
        db.commit()


def maybe_delete_local(db: Session, media_id: int) -> None:
    """If enabled, delete the local buffer copy once a media item is fully done."""
    from .admin_ops import get_system_settings

    if not get_system_settings(db).delete_local_after_upload:
        return
    media = db.get(MediaItem, media_id)
    if media is None or media.status != MediaStatus.DONE or media.local_deleted:
        return
    try:
        os.unlink(media.stored_path)
    except FileNotFoundError:
        pass
    except OSError as exc:  # pragma: no cover
        logger.warning("Could not delete local copy %s: %s", media.stored_path, exc)
        return
    media.local_deleted = True
    db.commit()
    logger.info("Deleted local copy of media %d after verified upload", media_id)


# --- Deletion -------------------------------------------------------------

# Signature: (rclone_options, dest) -> DeleteResult
DeleteFn = Callable[[dict, str], DeleteResult]


def _provider_options(provider: CloudProvider) -> dict:
    """Decrypt a provider's credentials into rclone options (empty on error)."""
    pt = get_type(provider.type)
    if pt is None:
        return {}
    try:
        config = json.loads(decrypt(provider.config_encrypted) or "{}")
    except json.JSONDecodeError:
        config = {}
    return pt.to_rclone_options(config)


def delete_media(
    db: Session,
    media_id: int,
    delete_fn: DeleteFn = delete_remote,
) -> dict:
    """Delete a media item: its local buffer copy and DB row (cascading its
    transfer jobs). If the ``delete_remote_on_local_delete`` setting is on, also
    remove the copies already uploaded to each linked provider.

    Returns a summary dict with the remote-deletion outcome so the caller (and
    UI) can surface any provider that refused.
    """
    from .admin_ops import get_system_settings

    media = db.get(MediaItem, media_id)
    if media is None:
        return {
            "deleted": False,
            "remote_attempted": 0,
            "remote_deleted": 0,
            "remote_errors": [],
        }

    remote_deleted = 0
    remote_errors: list[str] = []
    attempted = 0

    if get_system_settings(db).delete_remote_on_local_delete:
        # Only touch providers the file actually reached (a DONE job exists), so
        # we never issue deletes for uploads that never completed.
        done_provider_ids = set(
            db.scalars(
                select(TransferJob.provider_id).where(
                    TransferJob.media_id == media_id,
                    TransferJob.status == TransferStatus.DONE,
                )
            )
        )
        for provider_id in done_provider_ids:
            provider = db.get(CloudProvider, provider_id)
            if provider is None:
                continue
            link = db.scalar(
                select(FolderProviderLink).where(
                    FolderProviderLink.folder_id == media.folder_id,
                    FolderProviderLink.provider_id == provider_id,
                )
            )
            dest = _build_dest(link.dest_path if link else "", media.filename)
            attempted += 1
            result = delete_fn(_provider_options(provider), dest)
            if result.ok:
                remote_deleted += 1
            else:
                remote_errors.append(f"{provider.name}: {result.message}")

    # Remove the local buffer copy (best-effort — it may already be gone).
    if not media.local_deleted:
        try:
            os.unlink(media.stored_path)
        except FileNotFoundError:
            pass
        except OSError as exc:  # pragma: no cover
            logger.warning("Could not delete local copy %s: %s", media.stored_path, exc)

    filename = media.filename
    db.delete(media)  # cascades transfer jobs
    db.commit()
    logger.info(
        "Deleted media %d (%s); remote deleted %d/%d",
        media_id, filename, remote_deleted, attempted,
    )
    return {
        "deleted": True,
        "remote_attempted": attempted,
        "remote_deleted": remote_deleted,
        "remote_errors": remote_errors,
    }


# --- Reconciliation -------------------------------------------------------


def reconcile_once(db: Session) -> dict:
    """Self-heal transfers so temporary outages recover without manual action.

    Two passes:
      1. Backfill — every media in a folder with an *enabled* provider link gets
         a job for that provider if one is missing.
      2. Re-queue — failed/exhausted jobs whose source file is still on disk get
         a fresh attempt budget and go back to ``QUEUED``.

    Returns {"backfilled": int, "requeued": int}.
    """
    backfilled = 0
    links = db.scalars(
        select(FolderProviderLink).where(FolderProviderLink.enabled.is_(True))
    ).all()
    for link in links:
        media_ids = list(
            db.scalars(select(MediaItem.id).where(MediaItem.folder_id == link.folder_id))
        )
        for media_id in media_ids:
            exists = db.scalar(
                select(TransferJob.id).where(
                    TransferJob.media_id == media_id,
                    TransferJob.provider_id == link.provider_id,
                )
            )
            if exists is None:
                db.add(
                    TransferJob(
                        media_id=media_id,
                        provider_id=link.provider_id,
                        priority=link.priority,
                    )
                )
                backfilled += 1
    if backfilled:
        db.flush()

    failed = db.scalars(
        select(TransferJob).where(TransferJob.status == TransferStatus.FAILED)
    ).all()
    requeued = 0
    affected_media: set[int] = set()
    for job in failed:
        media = db.get(MediaItem, job.media_id)
        if media is None or media.local_deleted:
            continue  # a deleted source can't be re-uploaded
        job.status = TransferStatus.QUEUED
        job.attempts = 0
        job.last_error = ""
        job.next_attempt_at = None
        media.notified_failed = False  # re-arm: a fresh failure should re-notify
        requeued += 1
        affected_media.add(job.media_id)

    db.commit()
    # Backfilled + requeued media need their aggregate status recomputed.
    for link in links:
        affected_media.update(
            db.scalars(select(MediaItem.id).where(MediaItem.folder_id == link.folder_id))
        )
    for media_id in affected_media:
        recompute_media_status(db, media_id)
    if affected_media:
        db.commit()
    return {"backfilled": backfilled, "requeued": requeued}


def _vpn_watchdog() -> None:
    """Best-effort: bring a dropped autostart VPN tunnel back up.

    VPN is an optional convenience (reaching a LAN-only target), never a hard
    requirement — so any failure here is swallowed and must not disturb the
    reconcile loop or the connection-independent re-sync above.
    """
    try:
        from . import vpn as vpnsvc
        from .bootstrap import autostart_vpn

        caps = vpnsvc.capabilities()
        if not (caps.net_admin and caps.tun_device):
            return
        if vpnsvc.active_id() is None:
            autostart_vpn()
    except Exception:  # pragma: no cover - watchdog must never raise
        logger.debug("VPN watchdog skipped", exc_info=True)


def _reconcile_tick() -> None:
    from .admin_ops import get_system_settings

    with SessionLocal() as db:
        # Disk alert is independent of auto-resync — a full buffer stops uploads
        # regardless, so the field team should hear about it either way.
        try:
            check_low_space(db)
        except Exception:  # pragma: no cover - alerting must never break reconcile
            logger.debug("Low-space check skipped", exc_info=True)
        if not get_system_settings(db).auto_resync:
            return
        result = reconcile_once(db)
    if result["backfilled"] or result["requeued"]:
        logger.info(
            "Reconcile: backfilled %d, re-queued %d transfer job(s)",
            result["backfilled"], result["requeued"],
        )
    _vpn_watchdog()


async def reconcile_loop(stop: asyncio.Event) -> None:
    """Periodically self-heal transfers (see :func:`reconcile_once`)."""
    interval = get_settings().reconcile_interval
    if interval <= 0:
        return
    logger.info("Transfer reconciler started (every %.0fs)", interval)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            break  # stop was signalled during the wait
        except TimeoutError:
            pass
        try:
            await asyncio.to_thread(_reconcile_tick)
        except Exception:  # pragma: no cover - keep the loop alive
            logger.exception("Reconciler error")
    logger.info("Transfer reconciler stopped")


# --- Worker loop ----------------------------------------------------------


def _has_queued(db: Session) -> bool:
    return db.scalar(
        select(TransferJob.id).where(TransferJob.status == TransferStatus.QUEUED).limit(1)
    ) is not None


def _pick_eligible(db: Session) -> TransferJob | None:
    return db.scalar(
        select(TransferJob)
        .where(
            TransferJob.status == TransferStatus.QUEUED,
            or_(TransferJob.next_attempt_at.is_(None), TransferJob.next_attempt_at <= _now()),
        )
        .order_by(TransferJob.priority.desc(), TransferJob.created_at)
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
    """Process a single eligible job, respecting the bandwidth policy.

    Returns True if a job was handled (so the loop can immediately try the next).
    """
    with SessionLocal() as db:
        policy = bandwidth.get_policy(db)
        ok, reason = bandwidth.should_start(
            policy.enabled,
            policy.min_bandwidth_kbps,
            policy.last_kbps,
            policy.last_measured_at,
            _utcnow_naive(),
        )
        if not ok:
            # Try to self-heal: actively re-measure the link if a probe URL is
            # configured, then re-evaluate the gate.
            from .admin_ops import get_system_settings

            probe_url = get_system_settings(db).probe_url
            if probe_url and _has_queued(db):
                kbps = bandwidth.active_probe(probe_url)
                if kbps > 0:
                    bandwidth.record_measurement(db, kbps)
                    policy = bandwidth.get_policy(db)
                    ok, reason = bandwidth.should_start(
                        policy.enabled,
                        policy.min_bandwidth_kbps,
                        policy.last_kbps,
                        policy.last_measured_at,
                        _utcnow_naive(),
                    )
            if not ok:
                logger.debug("Upload gated: %s", reason)
                return False

        job = _pick_eligible(db)
        if job is None:
            return False

        bwlimit = bandwidth.effective_bwlimit(
            bandwidth.parse_schedule(policy.schedule_json),
            policy.bwlimit_kbps,
            datetime.now(),
        )
        process_job(db, job, bwlimit_kbps=bwlimit)
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
