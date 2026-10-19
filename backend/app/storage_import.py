"""Import files from an attached medium into an upload folder.

"Für den Upload markieren" = the files are copied into the box's media buffer
as regular :class:`MediaItem` rows and queued for every cloud target linked to
the folder — exactly like a browser upload. We copy rather than reference the
medium in place: the card goes back into the camera long before a slow uplink
has finished sending, and the pipeline's whole point is "accept locally, send
later".

Jobs run in a background thread, one file at a time, streaming 1 MiB blocks
(hash while copying, fsync before the rename) so a 1 GB Pi never holds a video
in RAM and a power cut leaves at most an orphan ``.part`` file that the startup
integrity check sweeps away. Job state is kept in memory only: after a restart
the admin simply re-runs the import — already imported files are skipped.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from .storage import folder_dir, safe_filename, uploads_tmp_dir

logger = logging.getLogger("offgridcloud.storage_import")

CHUNK = 1024 * 1024
# Leave this much headroom in the buffer so the box never fills to the brim.
SPACE_RESERVE = 512 * 1024 * 1024
MAX_FILES = 20000

MEDIA_EXTENSIONS = frozenset(
    {
        # images (incl. common camera RAW formats)
        "jpg", "jpeg", "png", "gif", "heic", "heif", "webp", "tif", "tiff", "bmp",
        "dng", "cr2", "cr3", "nef", "arw", "orf", "rw2", "raf", "srw", "pef",
        # video
        "mp4", "mov", "m4v", "avi", "mkv", "mts", "m2ts", "mpg", "mpeg", "3gp",
        "wmv", "webm", "insv", "lrv",
        # audio
        "wav", "mp3", "m4a", "aac", "flac", "ogg", "opus", "wma",
    }
)

# System clutter that a camera card or a Mac/Windows stick tends to carry.
_SKIP_DIRS = frozenset(
    {"System Volume Information", "$RECYCLE.BIN", "lost+found", ".Trashes", ".Spotlight-V100",
     ".fseventsd", ".TemporaryItems"}
)


def is_media_file(name: str) -> bool:
    return name.rsplit(".", 1)[-1].lower() in MEDIA_EXTENSIONS if "." in name else False


def _skip_name(name: str) -> bool:
    return name.startswith(".") or name in _SKIP_DIRS


@dataclass
class ImportJob:
    id: str
    device: str
    folder_id: int
    folder_name: str
    user_id: int
    status: str = "pending"  # pending | running | done | failed | cancelled
    total_files: int = 0
    total_bytes: int = 0
    done_files: int = 0
    done_bytes: int = 0
    imported: int = 0
    skipped: int = 0
    failed: int = 0
    current: str = ""
    error: str = ""
    errors: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


_jobs: dict[str, ImportJob] = {}
_cancel: dict[str, threading.Event] = {}
_lock = threading.Lock()


def list_jobs() -> list[ImportJob]:
    with _lock:
        return sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)


def get_job(job_id: str) -> ImportJob | None:
    with _lock:
        return _jobs.get(job_id)


def is_busy(device: str) -> bool:
    with _lock:
        return any(
            j.device == device and j.status in {"pending", "running"} for j in _jobs.values()
        )


def cancel(job_id: str) -> bool:
    with _lock:
        ev = _cancel.get(job_id)
        job = _jobs.get(job_id)
        if ev is None or job is None or job.status not in {"pending", "running"}:
            return False
        ev.set()
        return True


def clear_finished() -> int:
    with _lock:
        gone = [k for k, j in _jobs.items() if j.status not in {"pending", "running"}]
        for k in gone:
            _jobs.pop(k, None)
            _cancel.pop(k, None)
        return len(gone)


# --- Planning ----------------------------------------------------------------


def collect_files(root: Path, targets: list[Path], media_only: bool) -> list[Path]:
    """Expand selected files/directories into a sorted, de-duplicated file list."""
    seen: set[Path] = set()
    out: list[Path] = []

    def add(p: Path) -> None:
        if p in seen:
            return
        if media_only and not is_media_file(p.name):
            return
        seen.add(p)
        out.append(p)

    for target in targets:
        if target.is_symlink():
            continue
        if target.is_file():
            add(target)
        elif target.is_dir():
            for dirpath, dirnames, filenames in os.walk(target, followlinks=False):
                dirnames[:] = sorted(d for d in dirnames if not _skip_name(d))
                for name in sorted(filenames):
                    if _skip_name(name):
                        continue
                    p = Path(dirpath) / name
                    if p.is_symlink() or not p.is_file():
                        continue
                    add(p)
                    if len(out) > MAX_FILES:
                        raise ValueError(f"Zu viele Dateien (max. {MAX_FILES} je Import)")
    return out


# --- Running -----------------------------------------------------------------


def start(
    *,
    device: str,
    root: str,
    files: list[Path],
    folder_id: int,
    folder_name: str,
    user_id: int,
    tags: list[str],
    skip_duplicates: bool,
    run_async: bool = True,
) -> ImportJob:
    job = ImportJob(
        id=uuid.uuid4().hex,
        device=device,
        folder_id=folder_id,
        folder_name=folder_name,
        user_id=user_id,
        total_files=len(files),
        total_bytes=sum(_size(p) for p in files),
    )
    ev = threading.Event()
    with _lock:
        _jobs[job.id] = job
        _cancel[job.id] = ev
    args = (job, root, files, tags, skip_duplicates, ev)
    if run_async:
        threading.Thread(target=_run, args=args, name=f"import-{job.id[:8]}", daemon=True).start()
    else:
        _run(*args)
    return job


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _unique_name(taken: set[str], name: str) -> str:
    if name not in taken:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    n = 2
    while True:
        candidate = f"{stem} ({n}).{ext}" if ext else f"{stem} ({n})"
        if candidate not in taken:
            return candidate
        n += 1


def _copy(src: Path, dst_tmp: Path, ev: threading.Event, job: ImportJob) -> str:
    digest = hashlib.sha256()
    with open(src, "rb") as fin, open(dst_tmp, "wb") as fout:
        while True:
            if ev.is_set():
                raise InterruptedError
            block = fin.read(CHUNK)
            if not block:
                break
            fout.write(block)
            digest.update(block)
            with _lock:
                job.done_bytes += len(block)
        fout.flush()
        os.fsync(fout.fileno())
    return digest.hexdigest()


def _run(
    job: ImportJob,
    root: str,
    files: list[Path],
    tags: list[str],
    skip_duplicates: bool,
    ev: threading.Event,
) -> None:
    from .db import SessionLocal
    from .models import MediaItem, MediaStatus, MediaTag
    from .tagging import normalise_tags
    from .transfers import enqueue_for_media

    tag_list = normalise_tags(tags)
    with _lock:
        job.status = "running"
    try:
        with SessionLocal() as db:
            existing = db.execute(
                select(MediaItem.filename, MediaItem.size, MediaItem.sha256).where(
                    MediaItem.folder_id == job.folder_id
                )
            ).all()
            taken = {row.filename for row in existing}
            by_name_size = {(row.filename, row.size) for row in existing}
            hashes = {row.sha256 for row in existing if row.sha256}

            for src in files:
                if ev.is_set():
                    break
                rel = str(src.relative_to(root)) if src.is_relative_to(root) else src.name
                size = _size(src)
                with _lock:
                    job.current = rel
                name = safe_filename(src.name)
                if skip_duplicates and (name, size) in by_name_size:
                    with _lock:
                        job.skipped += 1
                        job.done_files += 1
                        job.done_bytes += size
                    continue

                tmp = uploads_tmp_dir() / f"import-{uuid.uuid4().hex}.part"
                before = job.done_bytes
                try:
                    sha = _copy(src, tmp, ev, job)
                except InterruptedError:
                    tmp.unlink(missing_ok=True)
                    break
                except OSError as exc:
                    tmp.unlink(missing_ok=True)
                    with _lock:
                        job.failed += 1
                        job.done_files += 1
                        job.done_bytes = before + size
                        if len(job.errors) < 50:
                            job.errors.append(f"{rel}: {exc.strerror or exc}")
                    continue

                if skip_duplicates and sha in hashes:
                    tmp.unlink(missing_ok=True)
                    with _lock:
                        job.skipped += 1
                        job.done_files += 1
                    continue

                final_name = _unique_name(taken, name)
                final_path = folder_dir(job.folder_id) / f"{uuid.uuid4()}__{final_name}"
                os.replace(tmp, final_path)
                media = MediaItem(
                    folder_id=job.folder_id,
                    filename=final_name,
                    stored_path=str(final_path),
                    size=size,
                    sha256=sha,
                    status=MediaStatus.RECEIVED,
                    uploaded_by=job.user_id,
                )
                db.add(media)
                db.flush()
                for tag in tag_list:
                    db.add(MediaTag(media_id=media.id, tag=tag))
                enqueue_for_media(db, media)
                db.commit()
                taken.add(final_name)
                by_name_size.add((final_name, size))
                hashes.add(sha)
                with _lock:
                    job.imported += 1
                    job.done_files += 1
        with _lock:
            job.status = "cancelled" if ev.is_set() else "done"
    except Exception as exc:  # noqa: BLE001 - surface any failure on the job
        logger.exception("import %s failed", job.id)
        with _lock:
            job.status = "failed"
            job.error = str(exc)
    finally:
        with _lock:
            job.current = ""
            job.finished_at = datetime.now(UTC).isoformat()
        _announce(job)


def _announce(job: ImportJob) -> None:
    """One summary notification per import instead of one per file."""
    if job.imported == 0:
        return
    try:
        from . import notify
        from .db import SessionLocal

        with SessionLocal() as db:
            notify.notify_event(
                db,
                "media.received",
                "Import vom Speichermedium",
                f"{job.imported} Datei(en) von {job.device} in „{job.folder_name}“ "
                f"übernommen — Cloud-Transfer beginnt."
                + (f" {job.skipped} übersprungen." if job.skipped else "")
                + (f" {job.failed} fehlgeschlagen." if job.failed else ""),
                {
                    "event": "storage.import",
                    "folder_id": job.folder_id,
                    "device": job.device,
                    "imported": job.imported,
                    "skipped": job.skipped,
                    "failed": job.failed,
                },
            )
    except Exception:  # noqa: BLE001 - notifications are best-effort
        logger.exception("import notification failed")
