"""Auto-import: copy every plugged-in medium to a fixed target, unattended.

The field team plugs a camera card or USB stick into the box and walks away —
no phone, no portal. :func:`watch_loop` notices the new partition, mounts it
through the root helper and starts the ordinary import engine
(:mod:`app.storage_import`): ``import`` mode copies the media into a folder and
queues it for that folder's cloud targets, ``cache`` mode mirrors the card onto
a cache drive. Duplicates are skipped as usual, so re-plugging a card only
copies what is new.

Feedback works on two channels:

* **offline** — the status LED (:mod:`app.status_led`): slow blink while
  copying, steady on when the medium may be pulled, fast flicker on errors;
* **online** — one Telegram/e-mail/webhook message per medium (event
  ``storage.auto_import``), if a channel is configured and reachable.

A medium is handled once per insertion, keyed by filesystem UUID; it is
forgotten when it disappears. After a successful copy the medium is ejected
(optional). A USB reader that powers off on eject vanishes from ``lsblk``
right away, so pulling it can't be observed: its "done" is held for
:data:`DONE_HOLD` or until the next medium is plugged in.

Skipped on purpose: the system and buffer disks, drives marked as cache or
buffer, non-removable disks, and drives carrying an OffgridCloud cache/buffer
directory (e.g. a cache SSD about to be marked).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import drives, status_led, storage_import

logger = logging.getLogger("offgridcloud.auto_import")

WATCH_INTERVAL = 4.0
DONE_HOLD = 30 * 60  # seconds an ejected (vanished) medium keeps the LED on "done"
MODES = ("import", "cache")


@dataclass
class Config:
    enabled: bool = False
    mode: str = "import"
    folder_id: int | None = None
    folder_name: str = ""
    cache_uuid: str = ""
    media_only: bool = True
    skip_duplicates: bool = True
    eject_after: bool = True
    user_id: int | None = None
    led_enabled: bool = True
    led_name: str = ""


@dataclass
class Entry:
    key: str  # partition UUID (or device path without one)
    device: str
    disk: str
    label: str
    state: str = "busy"  # busy | done | error | ignored
    message: str = ""
    job_id: str = ""
    ejected: bool = False
    gone_since: float | None = None
    started_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "device": self.device,
            "label": self.label,
            "state": self.state,
            "message": self.message,
            "job_id": self.job_id,
            "ejected": self.ejected,
        }


_entries: dict[str, Entry] = {}
_lock = threading.Lock()


# --- Configuration -------------------------------------------------------------


def get_row(db):
    from .models import AutoImportSettings

    row = db.get(AutoImportSettings, 1)
    if row is None:
        row = AutoImportSettings(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def load_config() -> Config:
    from .db import SessionLocal
    from .models import UploadFolder

    with SessionLocal() as db:
        row = get_row(db)
        folder = db.get(UploadFolder, row.folder_id) if row.folder_id else None
        return Config(
            enabled=row.enabled,
            mode=row.mode if row.mode in MODES else "import",
            folder_id=folder.id if folder else None,
            folder_name=folder.name if folder else "",
            cache_uuid=row.cache_uuid,
            media_only=row.media_only,
            skip_duplicates=row.skip_duplicates,
            eject_after=row.eject_after,
            user_id=row.user_id,
            led_enabled=row.led_enabled,
            led_name=row.led_name,
        )


def entries() -> list[Entry]:
    with _lock:
        return sorted(_entries.values(), key=lambda e: e.started_at, reverse=True)


def overall_state() -> str:
    """What the LED shows: busy beats error beats done (never pull while busy)."""
    states = {e.state for e in entries()}
    for state in ("busy", "error", "done"):
        if state in states:
            return state
    return "idle"


def reset() -> None:
    """Forget every medium (tests)."""
    with _lock:
        _entries.clear()


# --- One round ---------------------------------------------------------------------


def _candidates() -> dict[str, tuple[drives.Disk, drives.Partition]]:
    out: dict[str, tuple[drives.Disk, drives.Partition]] = {}
    for disk in drives.list_disks():
        if disk.protected or not disk.removable:
            continue
        for part in disk.partitions:
            if part.protected or part.cache or part.buffer_drive or not part.fstype:
                continue
            out[part.uuid or part.path] = (disk, part)
    return out


def tick(cfg: Config | None = None) -> None:
    cfg = cfg or load_config()
    with _lock:
        tracking = bool(_entries)
    if cfg.enabled or tracking:
        try:
            present = _candidates()
        except (drives.StorageError, OSError, ValueError) as exc:
            logger.debug("auto-import: drives unreadable: %s", exc)
            present = None
        if present is not None:
            _forget_gone(present)
            if cfg.enabled:
                for key, (disk, part) in present.items():
                    _maybe_start(key, disk, part, cfg)
        _follow_jobs(cfg)
    _update_led(cfg)


def _forget_gone(present: dict) -> None:
    now = time.monotonic()
    with _lock:
        for key, entry in list(_entries.items()):
            if key in present:
                continue
            if entry.ejected and entry.state == "done":
                # Powered off by the eject: we can't see it being pulled.
                if entry.gone_since is None:
                    entry.gone_since = now
                if now - entry.gone_since < DONE_HOLD:
                    continue
            if entry.state == "busy":
                # Pulled mid-copy: the job notices on its own and fails.
                continue
            _entries.pop(key, None)


def _maybe_start(key: str, disk: drives.Disk, part: drives.Partition, cfg: Config) -> None:
    with _lock:
        entry = _entries.get(key)
        if entry is not None:
            # An ejected medium that stayed visible (built-in reader) is the
            # same insertion; one that was gone and is back is a new one.
            if not (entry.ejected and entry.gone_since is not None):
                return
        if any(storage_import.is_busy(p.path) for p in disk.partitions):
            return  # an admin is importing from it by hand
        entry = Entry(key=key, device=part.path, disk=disk.path,
                      label=part.label or part.name, started_at=time.monotonic())
        _entries[key] = entry
        # A new medium takes over the LED from finished, already pulled ones.
        for other_key, other in list(_entries.items()):
            if other.ejected and other.gone_since is not None:
                _entries.pop(other_key, None)
    try:
        _start(entry, part, cfg)
    except _Ignore as exc:
        _set(entry, "ignored", str(exc))
    except (drives.StorageError, OSError, ValueError) as exc:
        _set(entry, "error", str(exc))
        _notify(entry, cfg, ok=False)


class _Ignore(Exception):
    """This medium is not for auto-import (an OffgridCloud drive)."""


def _set(entry: Entry, state: str, message: str = "") -> None:
    with _lock:
        entry.state = state
        if message:
            entry.message = message


def _start(entry: Entry, part: drives.Partition, cfg: Config) -> None:
    from .admin_ops import disk_usage

    mountpoint = part.mountpoint
    if not mountpoint:
        if not drives.manage_enabled():
            raise drives.StorageError(
                "Medium nicht eingehängt und Speicherverwaltung nicht eingerichtet."
            )
        mountpoint = drives._helper("mount", part.path)
        drives.clear_cache()
        logger.info("auto-import: mounted %s at %s", part.path, mountpoint)
    from .buffers import BUFFER_DIRNAME

    base = Path(mountpoint)
    if (base / storage_import.CACHE_DIRNAME).is_dir() or (base / BUFFER_DIRNAME).is_dir():
        raise _Ignore("OffgridCloud-Laufwerk — nicht automatisch übernommen.")

    files = storage_import.walk_files(base, media_only=cfg.media_only)
    if not files:
        _set(entry, "done", "Keine passenden Dateien auf dem Medium.")
        _after_done(entry, cfg)
        _notify(entry, cfg, ok=True)
        return
    needed = sum(storage_import._size(f) for f in files)

    if cfg.mode == "cache":
        cache_part = _cache_partition(cfg.cache_uuid)
        if needed + storage_import.SPACE_RESERVE > cache_part.free:
            raise drives.StorageError(
                f"Zu wenig Platz im Cache: {needed // 2**20} MiB benötigt."
            )
        snapshot = storage_import.snapshot_dir(drives.cache_root(cache_part), entry.label)
        job = storage_import.start_cache(
            device=part.path, root=str(base), files=files, cache_device=cache_part.path,
            snapshot_dir=snapshot, user_id=cfg.user_id, only_new=cfg.skip_duplicates,
            eject_after=False, auto=True,
        )
    else:
        if cfg.folder_id is None:
            raise drives.StorageError("Kein Zielordner für den Auto-Import gewählt.")
        if needed + storage_import.SPACE_RESERVE > disk_usage()["free"]:
            raise drives.StorageError(
                f"Zu wenig Platz im Puffer: {needed // 2**20} MiB benötigt."
            )
        job = storage_import.start(
            device=part.path, root=str(base), files=files, folder_id=cfg.folder_id,
            folder_name=cfg.folder_name, user_id=cfg.user_id, tags=[],
            skip_duplicates=cfg.skip_duplicates, auto=True,
        )
    with _lock:
        entry.job_id = job.id
    logger.info("auto-import: %d file(s) from %s (%s)", len(files), part.path, cfg.mode)


def _cache_partition(uuid: str) -> drives.Partition:
    parts = [p for d in drives.list_disks() for p in d.partitions if p.cache and p.mountpoint]
    for part in parts:
        if not uuid or part.uuid == uuid:
            return part
    raise drives.StorageError("Cache-Laufwerk ist nicht angeschlossen.")


def _follow_jobs(cfg: Config) -> None:
    for entry in entries():
        if entry.state != "busy" or not entry.job_id:
            continue
        job = storage_import.get_job(entry.job_id)
        if job is None:
            _set(entry, "error", "Vorgang nicht mehr auffindbar.")
            continue
        if job.status in {"pending", "running"}:
            continue
        summary = _summary(job)
        if job.status == "done" and job.failed == 0:
            _set(entry, "done", summary)
            _after_done(entry, cfg)
            _notify(entry, cfg, ok=True)
        else:
            reason = job.error or (job.errors[0] if job.errors else job.status)
            _set(entry, "error", f"{summary} — {reason}")
            _notify(entry, cfg, ok=False)


def _summary(job: storage_import.ImportJob) -> str:
    target = f"„{job.folder_name}“" if job.kind == "import" else "den Cache"
    parts = [f"{job.imported} Datei(en) nach {target} kopiert"]
    if job.skipped:
        parts.append(f"{job.skipped} schon vorhanden")
    if job.failed:
        parts.append(f"{job.failed} fehlgeschlagen")
    return ", ".join(parts)


def _after_done(entry: Entry, cfg: Config) -> None:
    if not cfg.eject_after or not drives.manage_enabled():
        return
    try:
        drives.eject(entry.disk)
    except (drives.StorageError, OSError) as exc:
        logger.warning("auto-import: eject of %s failed: %s", entry.disk, exc)
        with _lock:
            entry.message += f" Auswerfen fehlgeschlagen: {exc}"
        return
    drives.clear_cache()
    with _lock:
        entry.ejected = True
        entry.message += " Ausgeworfen — kann gezogen werden."


def _notify(entry: Entry, cfg: Config, *, ok: bool) -> None:
    """Telegram/e-mail/webhook, off the watch thread (SMTP may hang offline)."""
    title = (
        f"Medium {entry.label} fertig übernommen" if ok
        else f"Auto-Import von {entry.label} fehlgeschlagen"
    )
    payload = {
        "event": "storage.auto_import",
        "device": entry.device,
        "label": entry.label,
        "ok": ok,
        "mode": cfg.mode,
        "ejected": entry.ejected,
        "message": entry.message,
    }

    def send() -> None:
        try:
            from . import notify
            from .db import SessionLocal

            with SessionLocal() as db:
                notify.notify_event(db, "storage.auto_import", title, entry.message, payload)
        except Exception:  # noqa: BLE001 - notifications are best-effort
            logger.exception("auto-import notification failed")

    threading.Thread(target=send, name="auto-import-notify", daemon=True).start()


def _update_led(cfg: Config) -> None:
    preview = status_led.preview_state()
    if preview is not None:
        status_led.set_state(preview, cfg.led_name)
        return
    state = overall_state() if cfg.led_enabled else "idle"
    status_led.set_state(state, cfg.led_name)


async def watch_loop(stop: asyncio.Event) -> None:
    """Look for newly plugged-in media every few seconds."""
    while not stop.is_set():
        try:
            await asyncio.to_thread(tick)
        except Exception:  # pragma: no cover - keep the loop alive
            logger.warning("auto-import round failed", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=WATCH_INTERVAL)
        except TimeoutError:
            pass
    status_led.set_state("idle")
