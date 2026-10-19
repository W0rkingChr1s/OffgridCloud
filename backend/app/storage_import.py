"""Import files from an attached medium — into the upload pipeline or a cache.

Two job kinds share one engine:

* ``import`` — "für den Upload übernehmen": files are copied into the box's
  media buffer as regular :class:`MediaItem` rows and queued for every cloud
  target linked to the folder, exactly like a browser upload. We copy rather
  than reference the medium in place: the card goes back into the camera long
  before a slow uplink has finished sending.
* ``cache`` — "auf Cache sichern": the card is mirrored onto a drive marked as
  cache (keeping folder structure and timestamps), so the card can be reused
  right away and the photos picked for upload later, from the cache copy.

Duplicates are recognised box-wide. A file whose name, size and mtime match a
previous import is skipped without reading it; anything else is copied and its
SHA-256 compared — the hash is the final word. (Name + size alone is not
enough: some cameras write RAW files of constant size, which would silently
drop new photos.)

Jobs run in a background thread, one file at a time, streaming 1 MiB blocks
(fsync before the rename) so a 1 GB Pi never holds a video in RAM and a power
cut leaves at most an orphan temp file. Job state is in memory only: after a
restart the admin simply re-runs the job — finished files are skipped.
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
from sqlalchemy.orm import Session

from .storage import folder_dir, safe_filename, uploads_tmp_dir

logger = logging.getLogger("offgridcloud.storage_import")

CHUNK = 1024 * 1024
# Leave this much headroom on the target so the box never fills to the brim.
SPACE_RESERVE = 512 * 1024 * 1024
MAX_FILES = 20000
# Directory on a cache drive that holds the card copies ("snapshots").
CACHE_DIRNAME = "OffgridCloud-Cache"

PHOTO_EXTENSIONS = frozenset(
    {"jpg", "jpeg", "png", "gif", "heic", "heif", "webp", "tif", "tiff", "bmp"}
)
RAW_EXTENSIONS = frozenset(
    {"dng", "cr2", "cr3", "crw", "nef", "nrw", "arw", "srf", "sr2", "orf", "rw2", "raf",
     "srw", "pef", "x3f", "3fr", "iiq", "erf", "mef", "mos", "rwl"}
)
VIDEO_EXTENSIONS = frozenset(
    {"mp4", "mov", "m4v", "avi", "mkv", "mts", "m2ts", "mpg", "mpeg", "3gp", "wmv", "webm",
     "insv", "lrv"}
)
AUDIO_EXTENSIONS = frozenset({"wav", "mp3", "m4a", "aac", "flac", "ogg", "opus", "wma"})
MEDIA_EXTENSIONS = PHOTO_EXTENSIONS | RAW_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS

# System clutter that a camera card or a Mac/Windows stick tends to carry.
_SKIP_DIRS = frozenset(
    {"System Volume Information", "$RECYCLE.BIN", "lost+found", ".Trashes", ".Spotlight-V100",
     ".fseventsd", ".TemporaryItems"}
)


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def media_kind(name: str) -> str:
    """``photo`` | ``raw`` | ``video`` | ``audio`` | ``other``."""
    ext = _ext(name)
    if ext in PHOTO_EXTENSIONS:
        return "photo"
    if ext in RAW_EXTENSIONS:
        return "raw"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    return "other"


def is_media_file(name: str) -> bool:
    return _ext(name) in MEDIA_EXTENSIONS


def _skip_name(name: str) -> bool:
    return name.startswith(".") or name in _SKIP_DIRS


# --- Duplicate detection -----------------------------------------------------


def _mtime_key(mtime: float) -> int:
    # FAT/exFAT store 2 s / 10 ms granularity; whole seconds are what survives a
    # copy between filesystems.
    return int(mtime)


def known_files(db: Session, files: list[tuple[str, int, float]]) -> dict[tuple[str, int], dict]:
    """Which of ``(name, size, mtime)`` are already on the box?

    Returns ``{(name, size): info}`` where ``info["match"]`` is ``exact`` (same
    name, size and source mtime — imported from a medium before) or ``likely``
    (same name and size from a browser upload, whose mtime is unknown).
    """
    from .models import MediaItem, UploadFolder

    by_key: dict[tuple[str, int], float] = {}
    for name, size, mtime in files:
        by_key[(safe_filename(name), size)] = mtime
    names = sorted({k[0] for k in by_key})
    out: dict[tuple[str, int], dict] = {}
    for i in range(0, len(names), 500):
        rows = db.execute(
            select(
                MediaItem.id,
                MediaItem.filename,
                MediaItem.size,
                MediaItem.source_mtime,
                MediaItem.status,
                MediaItem.folder_id,
                UploadFolder.name,
            )
            .join(UploadFolder, UploadFolder.id == MediaItem.folder_id)
            .where(MediaItem.filename.in_(names[i : i + 500]))
        ).all()
        for row in rows:
            key = (row.filename, row.size)
            if key not in by_key:
                continue
            want = by_key[key]
            if row.source_mtime is not None:
                if _mtime_key(row.source_mtime) != _mtime_key(want):
                    continue
                match = "exact"
            else:
                match = "likely"
            prev = out.get(key)
            if prev is not None and (prev["match"] == "exact" or match == "likely"):
                continue
            out[key] = {
                "match": match,
                "media_id": row.id,
                "status": row.status.value,
                "folder_id": row.folder_id,
                "folder_name": row.name,
            }
    return out


# --- Jobs --------------------------------------------------------------------


@dataclass
class ImportJob:
    id: str
    kind: str  # import | cache
    device: str
    user_id: int
    folder_id: int | None = None
    folder_name: str = ""
    target: str = ""  # cache: snapshot path on the cache drive
    eject_after: bool = False
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
    """Is a job reading from — or writing to — ``device`` right now?"""
    with _lock:
        return any(
            device in (j.device, j.target.split("::", 1)[0])
            and j.status in {"pending", "running"}
            for j in _jobs.values()
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


def walk_files(root: Path, *, media_only: bool) -> list[Path]:
    """All regular files under ``root`` (sorted), skipping system clutter."""
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not _skip_name(d))
        for name in sorted(filenames):
            if _skip_name(name) or (media_only and not is_media_file(name)):
                continue
            p = Path(dirpath) / name
            if p.is_symlink() or not p.is_file():
                continue
            out.append(p)
            if len(out) > MAX_FILES:
                raise ValueError(f"Zu viele Dateien (max. {MAX_FILES} je Vorgang)")
    return out


def collect_files(root: Path, targets: list[Path], media_only: bool) -> list[Path]:
    """Expand selected files/directories into a de-duplicated file list."""
    seen: set[Path] = set()
    out: list[Path] = []
    for target in targets:
        if target.is_symlink():
            continue
        if target.is_file():
            candidates = [target] if (not media_only or is_media_file(target.name)) else []
        elif target.is_dir():
            candidates = walk_files(target, media_only=media_only)
        else:
            continue
        for p in candidates:
            if p not in seen:
                seen.add(p)
                out.append(p)
        if len(out) > MAX_FILES:
            raise ValueError(f"Zu viele Dateien (max. {MAX_FILES} je Vorgang)")
    return out


def _stat(p: Path) -> tuple[int, float]:
    try:
        st = p.stat()
        return st.st_size, st.st_mtime
    except OSError:
        return 0, 0.0


def _size(p: Path) -> int:
    return _stat(p)[0]


def _register(job: ImportJob, run, run_async: bool) -> ImportJob:
    ev = threading.Event()
    with _lock:
        _jobs[job.id] = job
        _cancel[job.id] = ev
    if run_async:
        threading.Thread(target=run, args=(job, ev), name=f"{job.kind}-{job.id[:8]}",
                         daemon=True).start()
    else:
        run(job, ev)
    return job


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
    """Start an upload import (see module docstring)."""
    job = ImportJob(
        id=uuid.uuid4().hex,
        kind="import",
        device=device,
        user_id=user_id,
        folder_id=folder_id,
        folder_name=folder_name,
        total_files=len(files),
        total_bytes=sum(_size(p) for p in files),
    )

    def run(j: ImportJob, ev: threading.Event) -> None:
        _run_import(j, root, files, tags, skip_duplicates, ev)

    return _register(job, run, run_async)


def start_cache(
    *,
    device: str,
    root: str,
    files: list[Path],
    cache_device: str,
    snapshot_dir: Path,
    user_id: int,
    only_new: bool,
    eject_after: bool,
    run_async: bool = True,
) -> ImportJob:
    """Start mirroring ``files`` (under ``root``) into ``snapshot_dir``."""
    job = ImportJob(
        id=uuid.uuid4().hex,
        kind="cache",
        device=device,
        user_id=user_id,
        target=f"{cache_device}::{snapshot_dir.name}",
        eject_after=eject_after,
        total_files=len(files),
        total_bytes=sum(_size(p) for p in files),
    )

    def run(j: ImportJob, ev: threading.Event) -> None:
        _run_cache(j, root, files, snapshot_dir, only_new, ev)

    return _register(job, run, run_async)


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


def _rel(src: Path, root: str) -> str:
    return str(src.relative_to(root)) if src.is_relative_to(root) else src.name


def _fail(job: ImportJob, rel: str, exc: OSError, before: int, size: int) -> None:
    with _lock:
        job.failed += 1
        job.done_files += 1
        job.done_bytes = before + size
        if len(job.errors) < 50:
            job.errors.append(f"{rel}: {exc.strerror or exc}")


def _skip(job: ImportJob, size: int = 0) -> None:
    with _lock:
        job.skipped += 1
        job.done_files += 1
        job.done_bytes += size


def _run_import(
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
            taken = set(
                db.scalars(select(MediaItem.filename).where(MediaItem.folder_id == job.folder_id))
            )
            # Box-wide, not just the target folder: a photo shouldn't go up twice.
            hashes = set(db.scalars(select(MediaItem.sha256).where(MediaItem.sha256 != "")))
            stats = {p: _stat(p) for p in files}
            known = (
                known_files(db, [(p.name, s, m) for p, (s, m) in stats.items()])
                if skip_duplicates
                else {}
            )

            for src in files:
                if ev.is_set():
                    break
                rel = _rel(src, root)
                size, mtime = stats[src]
                with _lock:
                    job.current = rel
                name = safe_filename(src.name)
                hit = known.get((name, size))
                if hit and hit["match"] == "exact":
                    _skip(job, size)
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
                    _fail(job, rel, exc, before, size)
                    continue

                if skip_duplicates and sha in hashes:
                    tmp.unlink(missing_ok=True)
                    _skip(job)
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
                    source_mtime=mtime or None,
                )
                db.add(media)
                db.flush()
                for tag in tag_list:
                    db.add(MediaTag(media_id=media.id, tag=tag))
                enqueue_for_media(db, media)
                db.commit()
                taken.add(final_name)
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


def _run_cache(
    job: ImportJob,
    root: str,
    files: list[Path],
    snapshot_dir: Path,
    only_new: bool,
    ev: threading.Event,
) -> None:
    from .db import SessionLocal

    with _lock:
        job.status = "running"
    try:
        stats = {p: _stat(p) for p in files}
        known: dict = {}
        if only_new:
            with SessionLocal() as db:
                known = known_files(db, [(p.name, s, m) for p, (s, m) in stats.items()])
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        for src in files:
            if ev.is_set():
                break
            rel = _rel(src, root)
            size, mtime = stats[src]
            with _lock:
                job.current = rel
            hit = known.get((safe_filename(src.name), size))
            if hit and hit["match"] == "exact":
                _skip(job, size)
                continue
            dst = snapshot_dir / rel
            dsize, dmtime = _stat(dst) if dst.exists() else (-1, 0.0)
            if dsize == size and _mtime_key(dmtime) == _mtime_key(mtime):
                _skip(job, size)  # already cached by an earlier run
                continue
            before = job.done_bytes
            tmp = dst.with_name(f".{dst.name}.ogc-part")
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                _copy(src, tmp, ev, job)
                # Keep the capture time: the gallery groups by it and the later
                # upload import recognises the file by it.
                os.utime(tmp, (mtime, mtime))
                os.replace(tmp, dst)
            except InterruptedError:
                tmp.unlink(missing_ok=True)
                break
            except OSError as exc:
                tmp.unlink(missing_ok=True)
                _fail(job, rel, exc, before, size)
                continue
            with _lock:
                job.imported += 1
                job.done_files += 1
        with _lock:
            job.status = "cancelled" if ev.is_set() else "done"
    except Exception as exc:  # noqa: BLE001
        logger.exception("cache %s failed", job.id)
        with _lock:
            job.status = "failed"
            job.error = str(exc)
    finally:
        with _lock:
            job.current = ""
            job.finished_at = datetime.now(UTC).isoformat()
        if job.status == "done" and job.eject_after and job.failed == 0:
            try:
                from . import drives

                disk = next(
                    d for d in drives.list_disks()
                    if any(p.path == job.device for p in d.partitions)
                )
                drives.eject(disk.path)
            except Exception as exc:  # noqa: BLE001 - the copy itself succeeded
                with _lock:
                    job.error = f"Gesichert, aber Auswerfen fehlgeschlagen: {exc}"


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
