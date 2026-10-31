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
import shutil
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import bandwidth, buffers, network, video_split
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
from .rclone import PROXY_OPTION, DeleteResult, UploadResult, delete_remote, run_upload

logger = logging.getLogger("offgridcloud.transfers")

# Seconds between checks whether an unplugged buffer drive is back.
OFFLINE_RETRY = 60

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


# --- Manual control (local admin or a pool hub with control rights) ------

CONTROL_ACTIONS = ("pause", "resume", "retry_failed")
JOB_LIST_LIMIT = 200


def list_jobs(
    db: Session, status: TransferStatus | None = None, limit: int = JOB_LIST_LIMIT
) -> list[TransferJob]:
    """Most recently updated jobs first, optionally only one status."""
    stmt = select(TransferJob).order_by(TransferJob.updated_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(TransferJob.status == status)
    return list(db.scalars(stmt))


def retry_job(db: Session, job_id: int) -> TransferJob:
    """Re-queue one job now. Raises LookupError if unknown, ValueError if running."""
    job = db.get(TransferJob, job_id)
    if job is None:
        raise LookupError(job_id)
    if job.status == TransferStatus.RUNNING:
        raise ValueError("Job läuft bereits")
    job.status = TransferStatus.QUEUED
    job.attempts = 0
    job.last_error = ""
    job.next_attempt_at = None
    db.flush()
    recompute_media_status(db, job.media_id)
    db.commit()
    db.refresh(job)
    return job


def control_state(db: Session) -> dict:
    """Whether uploads are manually paused, and how many jobs have failed."""
    failed = db.scalar(
        select(func.count(TransferJob.id)).where(TransferJob.status == TransferStatus.FAILED)
    )
    policy = bandwidth.get_policy(db)
    return {
        "uploads_paused": policy.uploads_paused,
        "failed_transfers": int(failed or 0),
        "pool_limit_kbps": bandwidth.active_pool_limit(policy, _utcnow_naive()),
    }


def apply_control(db: Session, action: str) -> str:
    """Apply one control action and return a short summary for the audit log.

    Deliberately non-destructive: pausing only stops *new* uploads from starting
    (running ones finish), and nothing here deletes data anywhere.
    """
    if action not in CONTROL_ACTIONS:
        raise ValueError(f"unknown action: {action}")
    if action in ("pause", "resume"):
        policy = bandwidth.get_policy(db)
        policy.uploads_paused = action == "pause"
        db.commit()
        return "Uploads pausiert" if policy.uploads_paused else "Uploads fortgesetzt"
    jobs = list(
        db.scalars(select(TransferJob).where(TransferJob.status == TransferStatus.FAILED))
    )
    for job in jobs:
        job.status = TransferStatus.QUEUED
        job.attempts = 0
        job.last_error = ""
        job.next_attempt_at = None
    db.flush()
    for media_id in {job.media_id for job in jobs}:
        recompute_media_status(db, media_id)
    db.commit()
    return f"{len(jobs)} fehlgeschlagene Transfers neu eingereiht"


# --- Job processing -------------------------------------------------------


def _build_dest(dest_path: str, filename: str) -> str:
    return posixpath.join(dest_path.strip("/"), filename) if dest_path.strip("/") else filename


def _split_dests(dest_path: str, filename: str, parts: int) -> list[str]:
    """Remote paths of a video uploaded as ``parts`` pieces (sub folder per video)."""
    folder = _build_dest(dest_path, video_split.part_folder(filename))
    return [
        posixpath.join(folder, video_split.part_name(filename, i, parts))
        for i in range(1, parts + 1)
    ]


def _upload_split(
    local_path: str,
    filename: str,
    dest_path: str,
    limit: int,
    options: dict,
    upload_fn: UploadFn,
    bwlimit_kbps: int,
    on_progress: ProgressCb,
) -> tuple[UploadResult, int]:
    """Cut a too-big video into playable parts and upload them one by one.

    Returns the combined result and the number of parts (0 on failure). The
    parts live next to the original on the same buffer drive, in a hidden
    work folder that is removed afterwards. A retry cuts and uploads again;
    parts already on the remote are simply overwritten.
    """
    src = Path(local_path)
    work = src.parent / f".split-{src.name}"
    try:
        split = video_split.split_video(src, work, limit)
        if not split.ok:
            return UploadResult(False, 0, f"Video teilen fehlgeschlagen: {split.message}"), 0
        sizes = [p.stat().st_size for p in split.parts]
        total = sum(sizes)
        dests = _split_dests(dest_path, filename, len(split.parts))
        done = 0
        rates: list[float] = []
        for index, (part, size, dest) in enumerate(zip(split.parts, sizes, dests, strict=True), 1):
            def _progress(sent: int, _total: int, kbps: float, base: int = done) -> None:
                on_progress(base + sent, total, kbps)

            result = upload_fn(str(part), options, dest, bwlimit_kbps, _progress)
            if not result.ok:
                return UploadResult(
                    False, done + result.bytes_transferred,
                    f"Teil {index}/{len(dests)}: {result.message}", result.kbps,
                ), 0
            done += result.bytes_transferred or size
            if result.kbps > 0:
                rates.append(result.kbps)
        kbps = sum(rates) / len(rates) if rates else 0.0
        return UploadResult(True, done, "", kbps), len(dests)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _rclone_upload_fn(
    local_path: str,
    options: dict,
    dest: str,
    bwlimit_kbps: int,
    on_progress: ProgressCb | None = None,
) -> UploadResult:
    return run_upload(local_path, options, dest, bwlimit_kbps, on_progress)


_last_route = [""]  # last bundling decision, so changes are logged once


def _bonding_proxy(db: Session, options: dict) -> str:
    """net-merge proxy for this upload, or ``""`` to go direct (see app/bonding.py)."""
    from . import bonding
    from . import vpn as vpnsvc

    policy = bandwidth.get_policy(db)
    decision = bonding.decide(
        enabled=True,
        options=options,
        metered_mode=policy.metered_mode,
        metered_present=network.metered_link_present(),
        openvpn_active=vpnsvc.active_type() == "openvpn",
    )
    route = "gebündelt" if decision.proxy else f"direkt ({decision.reason})"
    if route != _last_route[0]:
        _last_route[0] = route
        logger.info("Leitungsbündelung: Uploads %s", route)
    return decision.proxy


def process_job(
    db: Session,
    job: TransferJob,
    upload_fn: UploadFn = _rclone_upload_fn,
    bwlimit_kbps: int = 0,
    metered: bool = False,
    bonding: bool = False,
) -> TransferJob:
    """Run one transfer job through its state machine. Commits its own changes.

    With ``metered`` the bytes sent count against today's metered data budget.
    With ``bonding`` the upload goes through net-merge when that is safe.
    """
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

    if not media.local_deleted and buffers.is_offline(media.stored_path, db):
        # Its buffer drive is unplugged: wait for it instead of burning attempts.
        job.status = TransferStatus.QUEUED
        job.last_error = "Puffer-Laufwerk nicht angeschlossen — wartet"
        job.next_attempt_at = _now() + timedelta(seconds=OFFLINE_RETRY)
        db.commit()
        return job

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
    if pt.upload_options is not None:
        options = pt.upload_options(config, options)
    if bonding:
        proxy = _bonding_proxy(db, options)
        if proxy:
            options[PROXY_OPTION] = proxy
    dest = _build_dest(dest_path, media.filename)

    job_id = job.id
    sent = [0]  # last reported progress, so a failed upload still counts its bytes

    def _on_progress(bytes_done: int, total: int, kbps: float) -> None:
        sent[0] = max(sent[0], bytes_done)
        set_live(job_id, bytes_done, total, kbps)

    split_limit = pt.split_limit(config) if pt.split_limit is not None else None
    split_parts = 0
    try:
        if (
            split_limit
            and media.size > split_limit
            and video_split.is_splittable(media.filename)
        ):
            result, split_parts = _upload_split(
                media.stored_path, media.filename, dest_path, split_limit,
                options, upload_fn, bwlimit_kbps, _on_progress,
            )
        else:
            result = upload_fn(media.stored_path, options, dest, bwlimit_kbps, _on_progress)
    finally:
        clear_live(job_id)

    if metered:
        used = max(sent[0], result.bytes_transferred)
        if result.ok and used <= 0:
            used = media.size
        bandwidth.add_metered_usage(db, used, date.today().isoformat())

    # Feed observed throughput back into the bandwidth gate. With parallel
    # uploads one job only sees its share of the line, so add what the others
    # are moving right now.
    if result.kbps > 0:
        others = sum(v["kbps"] for v in get_live().values())
        bandwidth.record_measurement(db, result.kbps + others)

    settings = get_settings()
    if result.ok:
        job.status = TransferStatus.DONE
        job.progress = 1.0
        job.bytes_transferred = result.bytes_transferred or media.size
        job.split_parts = split_parts
        job.last_error = ""
        job.next_attempt_at = None
    else:
        job.last_error = (
            pt.explain_error(result.message) if pt.explain_error else result.message
        )
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
            dest_path = link.dest_path if link else ""
            job = db.scalar(
                select(TransferJob).where(
                    TransferJob.media_id == media_id, TransferJob.provider_id == provider_id
                )
            )
            parts = job.split_parts if job is not None else 0
            dests = (
                _split_dests(dest_path, media.filename, parts)
                if parts
                else [_build_dest(dest_path, media.filename)]
            )
            attempted += 1
            options = _provider_options(provider)
            failed = ""
            for dest in dests:
                result = delete_fn(options, dest)
                if not result.ok:
                    failed = result.message
                    break
            if not failed:
                remote_deleted += 1
            else:
                remote_errors.append(f"{provider.name}: {failed}")

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


# --- Parallel uploads ------------------------------------------------------
# Up to MAX_PARALLEL worker slots pick jobs at the same time (the admin sets how
# many under Bandbreite). A job counts as taken from the moment a slot picks it
# until process_job returns; the metered budget other slots may still spend is
# reduced by the sizes those jobs reserve.
MAX_PARALLEL = 4
_claim_lock = threading.Lock()
_inflight: dict[int, int] = {}  # job id -> bytes reserved against the metered budget


def inflight_count() -> int:
    with _claim_lock:
        return len(_inflight)


def _pick_eligible(
    db: Session, max_bytes: int | None = None, exclude: set[int] | frozenset = frozenset()
) -> TransferJob | None:
    """Next due job; with ``max_bytes`` only one whose file fits that budget."""
    query = select(TransferJob).where(
        TransferJob.status == TransferStatus.QUEUED,
        or_(TransferJob.next_attempt_at.is_(None), TransferJob.next_attempt_at <= _now()),
    )
    if exclude:
        query = query.where(TransferJob.id.not_in(exclude))
    if max_bytes is not None:
        query = query.join(MediaItem, MediaItem.id == TransferJob.media_id).where(
            MediaItem.size <= max_bytes
        )
    return db.scalar(
        query.order_by(TransferJob.priority.desc(), TransferJob.created_at).limit(1)
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


def _note_bandwidth_gate(db: Session, ok: bool, reason: str) -> None:
    """Best-effort: emit a pause/resume announcement on gate transitions."""
    from . import announce

    try:
        announce.note_bandwidth_gate(
            db,
            gated=not ok,
            reason=reason,
            has_queued=_has_queued(db),
            last_kbps=bandwidth.get_policy(db).last_kbps,
        )
    except Exception:  # pragma: no cover - announcing must never break the worker
        logger.debug("Bandwidth-gate announcement skipped", exc_info=True)


def _metered_check(db: Session, policy) -> tuple[bool, str, int | None, bool]:
    """Apply the metered-uplink rule: ``(ok, reason, max_bytes, metered)``.

    Skips the NetworkManager query entirely while uploads are allowed anyway.
    """
    if policy.metered_mode not in ("pause", "limit"):
        return True, "", None, False
    metered, label = network.metered_uplink()
    used = bandwidth.metered_used_today(policy, date.today().isoformat())
    ok, reason, max_bytes = bandwidth.metered_gate(
        policy.metered_mode, policy.metered_budget_mb, used, metered, label
    )
    return ok, reason, max_bytes, metered


def process_one() -> bool:
    """Process a single eligible job, respecting the bandwidth policy.

    Returns True if a job was handled (so the loop can immediately try the next).
    """
    with SessionLocal() as db:
        policy = bandwidth.get_policy(db)
        if policy.uploads_paused:
            # Manual stop — not a bandwidth problem, so no gate announcement.
            logger.debug("Upload gated: manually paused")
            return False
        # Metered uplink first: if it blocks, don't even spend data on a probe.
        ok, reason, max_bytes, metered = _metered_check(db, policy)
        if ok:
            ok, reason = bandwidth.should_start(
                policy.enabled,
                policy.min_bandwidth_kbps,
                policy.last_kbps,
                policy.last_measured_at,
                _utcnow_naive(),
            )
        if not ok and not metered:
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

        job = None
        if ok:
            with _claim_lock:
                taken = set(_inflight)
                if max_bytes is not None:
                    max_bytes = max(0, max_bytes - sum(_inflight.values()))
                job = _pick_eligible(db, max_bytes, taken)
                if job is not None:
                    reserved = 0
                    if metered and max_bytes is not None:
                        media = db.get(MediaItem, job.media_id)
                        reserved = media.size if media else 0
                    _inflight[job.id] = reserved
            if (
                job is None
                and max_bytes is not None
                and not taken
                and _pick_eligible(db) is not None
            ):
                ok = False
                reason = (
                    f"Restvolumen {_format_bytes(max_bytes)} reicht für keine wartende Datei"
                )

        # Announce a one-shot "paused"/"resumed" on bandwidth-gate transitions.
        _note_bandwidth_gate(db, ok, reason)

        if not ok:
            logger.debug("Upload gated: %s", reason)
            return False
        if job is None:
            return False

        bwlimit = upload_limit(policy)
        try:
            process_job(
                db, job, bwlimit_kbps=bwlimit, metered=metered, bonding=policy.bonding_enabled
            )
        finally:
            with _claim_lock:
                _inflight.pop(job.id, None)
        return True


def upload_limit(policy) -> int:
    """KiB/s for ONE upload right now (0 = unlimited).

    Own limit (with schedule windows) and a pool hub's share of a shared line
    both apply, the lower wins; parallel uploads split it. Used when an upload
    starts and again by ``sync_live_limits`` while it runs.
    """
    limit = bandwidth.combine_limits(
        bandwidth.effective_bwlimit(
            bandwidth.parse_schedule(policy.schedule_json),
            policy.bwlimit_kbps,
            datetime.now(),
        ),
        bandwidth.active_pool_limit(policy, _utcnow_naive()),
    )
    slots = _parallel_slots(policy)
    if limit > 0 and slots > 1:
        limit = max(1, limit // slots)
    return limit


LIVE_LIMIT_INTERVAL = 5.0  # seconds between live-limit checks


def sync_live_limits() -> int:
    """Bring every running upload to the current limit; returns how many changed."""
    from .rclone import live_uploads, set_live_limit

    uploads = live_uploads()
    if not uploads:
        return 0
    with SessionLocal() as db:
        target = upload_limit(bandwidth.get_policy(db))
    return sum(1 for up in uploads if up.kbps != target and set_live_limit(up, target))


async def live_limit_loop(stop: asyncio.Event, interval: float = LIVE_LIMIT_INTERVAL) -> None:
    """Apply limit changes to uploads already running (pool share, schedule, settings)."""
    while not stop.is_set():
        try:
            await asyncio.to_thread(sync_live_limits)
        except Exception:  # noqa: BLE001 - must never stop the worker
            logger.debug("Live limit sync failed", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


def _parallel_slots(policy) -> int:
    return max(1, min(MAX_PARALLEL, policy.parallel_uploads or 1))


def _slot_active(slot: int) -> bool:
    """Whether worker slot ``slot`` (0-based) may run under the current setting."""
    if slot == 0:
        return True
    with SessionLocal() as db:
        return slot < _parallel_slots(bandwidth.get_policy(db))


async def _worker_slot(slot: int, stop: asyncio.Event, interval: float) -> None:
    while not stop.is_set():
        try:
            handled = await asyncio.to_thread(
                lambda: _slot_active(slot) and process_one()
            )
        except Exception:  # pragma: no cover - keep the worker alive
            logger.exception("Transfer worker error")
            handled = False
        if not handled:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                pass


async def worker_loop(stop: asyncio.Event) -> None:
    """Run MAX_PARALLEL slots; ``parallel_uploads`` decides how many pick jobs."""
    interval = get_settings().worker_poll_interval
    recover_running_jobs()
    logger.info("Transfer worker started")
    await asyncio.gather(*(_worker_slot(i, stop, interval) for i in range(MAX_PARALLEL)))
    logger.info("Transfer worker stopped")
