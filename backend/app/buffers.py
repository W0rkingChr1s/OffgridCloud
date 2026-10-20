"""Media buffer targets: the box's own buffer plus removable buffer drives.

The box buffers every upload and import locally until it reached the cloud.
Its own buffer (``OGC_BUFFER_DIR``) usually sits on the system SD card, which
is small and wears out. An admin can mark an attached drive (USB stick, SSD)
as additional buffer: while it is plugged in and has room, new files land
there first; otherwise the box's own buffer takes over again.

Files keep their absolute ``stored_path``, so a buffer drive that is pulled
simply makes its pending files unavailable until it is back — transfers wait
instead of failing (see :func:`is_offline`), and the drive is mounted again
automatically when it reappears (:func:`sync`, run by :func:`watch_loop`).

A drive is recognised by filesystem UUID (like cache drives). The buffer
directory on it carries a marker file holding that UUID, so the hot paths
(picking a target, checking a path) never need ``lsblk``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, literal, select, update
from sqlalchemy.orm import Session

from . import drives
from .config import get_settings

logger = logging.getLogger("offgridcloud.buffers")

# Directory on a buffer drive that holds the buffered media.
BUFFER_DIRNAME = "OffgridCloud-Puffer"
MARKER = ".ogc-buffer"
# Leave this much headroom on a target so it never fills to the brim.
SPACE_RESERVE = 512 * 1024 * 1024
WATCH_INTERVAL = 10.0

# UUIDs the admin unmounted on purpose — not re-mounted until unplugged.
_suspended: set[str] = set()


@dataclass
class Target:
    root: Path
    total: int
    used: int
    free: int
    uuid: str = ""  # empty = the box's own buffer


def internal_root() -> Path:
    path = get_settings().buffer_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def _usage(path: Path) -> tuple[int, int, int]:
    total, used, free = shutil.disk_usage(path)
    return total, used, free


def _marked(db: Session | None = None):
    from .db import SessionLocal
    from .models import StorageBuffer

    if db is not None:
        return list(db.scalars(select(StorageBuffer).order_by(StorageBuffer.created_at)))
    with SessionLocal() as own:
        return list(own.scalars(select(StorageBuffer).order_by(StorageBuffer.created_at)))


def root_of(mountpoint: str) -> Path:
    return Path(mountpoint) / BUFFER_DIRNAME


def _online_root(uuid: str, mountpoint: str) -> Path | None:
    """The buffer dir on ``mountpoint`` if it is there and really ``uuid``'s."""
    if not mountpoint:
        return None
    root = root_of(mountpoint)
    try:
        if (root / MARKER).read_text("utf-8").strip() != uuid:
            return None
    except OSError:
        return None
    return root if os.access(root, os.W_OK) else None


def drive_targets(db: Session | None = None) -> list[Target]:
    """Mounted, writable buffer drives (most free space first)."""
    out: list[Target] = []
    for row in _marked(db):
        root = _online_root(row.uuid, row.mountpoint)
        if root is None:
            continue
        try:
            total, used, free = _usage(root)
        except OSError:
            continue
        out.append(Target(root=root, total=total, used=used, free=free, uuid=row.uuid))
    out.sort(key=lambda t: t.free, reverse=True)
    return out


def targets(db: Session | None = None) -> list[Target]:
    """Every place new files may go, in order of preference."""
    root = internal_root()
    total, used, free = _usage(root)
    return [*drive_targets(db), Target(root=root, total=total, used=used, free=free)]


def pick(needed: int = 0, db: Session | None = None) -> Path:
    """Buffer root for a new file of ``needed`` bytes.

    A buffer drive with room wins; the box's own buffer is the fallback (also
    when nothing has room — the write then fails there as it always did).
    """
    for target in drive_targets(db):
        if target.free - needed >= SPACE_RESERVE:
            return target.root
    return internal_root()


def roots(db: Session | None = None) -> list[Path]:
    """Every buffer root currently reachable (for cleanup passes)."""
    return [t.root for t in targets(db)]


def usage(db: Session | None = None) -> tuple[int, int, int]:
    """Combined ``(total, used, free)`` of all reachable buffer targets."""
    total = used = free = 0
    for t in targets(db):
        total += t.total
        used += t.used
        free += t.free
    return total, used, free


def under(path: str, mountpoint: str) -> bool:
    mp = mountpoint.rstrip("/")
    return bool(mp) and (path == mp or path.startswith(mp + "/"))


def is_offline(path: str, db: Session | None = None) -> bool:
    """True if ``path`` lives on a buffer drive that is not attached right now."""
    for row in _marked(db):
        if under(path, row.mountpoint) and _online_root(row.uuid, row.mountpoint) is None:
            return True
    return False


def offline_mountpoints(db: Session | None = None) -> list[str]:
    return [r.mountpoint for r in _marked(db)
            if r.mountpoint and _online_root(r.uuid, r.mountpoint) is None]


def pending_on(db: Session, mountpoint: str) -> int:
    """Files under ``mountpoint`` that would be lost if the drive went away now:
    media not yet at every provider plus unfinished uploads."""
    from .models import MediaItem, MediaStatus, UploadSession

    if not mountpoint:
        return 0
    prefix = mountpoint.rstrip("/") + "/"
    media = db.scalars(
        select(MediaItem.id).where(
            MediaItem.stored_path.startswith(prefix, autoescape=True),
            MediaItem.local_deleted.is_(False),
            MediaItem.status != MediaStatus.DONE,
        )
    ).all()
    uploads = db.scalars(
        select(UploadSession.id).where(UploadSession.temp_path.startswith(prefix, autoescape=True))
    ).all()
    return len(media) + len(uploads)


def check_removable(db: Session, part: drives.Partition) -> None:
    """Refuse taking a buffer drive away while it still holds pending files."""
    if not part.buffer_drive or not part.mountpoint:
        return
    n = pending_on(db, part.mountpoint)
    if n:
        raise drives.StorageError(
            f"Auf diesem Puffer-Laufwerk warten noch {n} Datei(en) auf den Upload."
        )


# --- Marking -------------------------------------------------------------------


def prepare(part: drives.Partition) -> Path:
    """Create the buffer directory (+ UUID marker) on a mounted partition."""
    if not part.mountpoint:
        raise drives.StorageError("Laufwerk ist nicht eingehängt.")
    root = root_of(part.mountpoint)
    if not root.is_dir() or not os.access(root, os.W_OK):
        if drives.manage_enabled():
            drives._helper("prepare-buffer", part.path)
        else:
            try:
                root.mkdir(exist_ok=True)
            except OSError as exc:
                raise drives.StorageError(
                    f"Puffer-Ordner nicht anlegbar: {exc.strerror}"
                ) from None
    if not os.access(root, os.W_OK):
        raise drives.StorageError("Puffer-Ordner ist nicht beschreibbar.")
    try:
        (root / MARKER).write_text(part.uuid + "\n", "utf-8")
    except OSError as exc:
        raise drives.StorageError(f"Puffer-Ordner nicht beschreibbar: {exc.strerror}") from None
    return root


def _rewrite_paths(db: Session, old: str, new: str) -> None:
    """Move stored paths from mountpoint ``old`` to ``new`` (drive re-mounted
    elsewhere, e.g. because another stick took its usual name)."""
    from .models import MediaItem, UploadSession

    old_p, new_p = old.rstrip("/") + "/", new.rstrip("/") + "/"
    for model, col in ((MediaItem, MediaItem.stored_path),
                       (UploadSession, UploadSession.temp_path)):
        db.execute(
            update(model)
            .where(col.startswith(old_p, autoescape=True))
            .values({col: literal(new_p) + func.substr(col, len(old_p) + 1)})
            .execution_options(synchronize_session=False)
        )
    logger.info("Buffer drive moved from %s to %s — stored paths updated", old, new)


def sync(db: Session, disks: list[drives.Disk] | None = None) -> None:
    """Mount marked buffer drives that are plugged in and track mountpoints."""
    from .models import StorageBuffer

    rows = list(db.scalars(select(StorageBuffer)))
    if not rows:
        return
    if disks is None:
        disks = drives.list_disks()
    parts = {p.uuid: p for d in disks for p in d.partitions if p.uuid}
    changed = False
    for row in rows:
        part = parts.get(row.uuid)
        if part is None:
            _suspended.discard(row.uuid)  # unplugged: mount again next time
            continue
        if part.protected or part.read_only:
            continue
        mountpoint = part.mountpoint
        if not mountpoint:
            if row.uuid in _suspended or not drives.manage_enabled() or not part.fstype:
                continue
            try:
                mountpoint = drives._helper("mount", part.path)
            except drives.StorageError as exc:
                logger.warning("Could not mount buffer drive %s: %s", part.path, exc)
                continue
            logger.info("Mounted buffer drive %s at %s", part.path, mountpoint)
            drives.clear_cache()
        if row.mountpoint and row.mountpoint != mountpoint:
            if _online_root(row.uuid, row.mountpoint) is None:
                _rewrite_paths(db, row.mountpoint, mountpoint)
        if row.mountpoint != mountpoint:
            row.mountpoint = mountpoint
            changed = True
    if changed:
        db.commit()


def suspend(uuid: str) -> None:
    """The admin unmounted this drive — leave it alone until it is unplugged."""
    if uuid:
        _suspended.add(uuid)


def resume(uuid: str) -> None:
    _suspended.discard(uuid)


def sync_startup() -> None:
    """Best-effort :func:`sync` at boot — never blocks startup."""
    try:
        _sync_tick()
    except Exception:  # noqa: BLE001
        logger.warning("Buffer-drive sync at startup failed", exc_info=True)


def _sync_tick() -> None:
    from .db import SessionLocal

    with SessionLocal() as db:
        sync(db)


async def watch_loop(stop: asyncio.Event) -> None:
    """Re-mount buffer drives shortly after they are plugged back in."""
    while not stop.is_set():
        try:
            await asyncio.to_thread(_sync_tick)
        except Exception:  # pragma: no cover - keep the loop alive
            logger.debug("Buffer-drive sync skipped", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=WATCH_INTERVAL)
        except TimeoutError:
            pass

