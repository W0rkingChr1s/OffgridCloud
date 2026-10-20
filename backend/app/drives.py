"""Attached storage media: discovery, safety classification and privileged ops.

The box often gets a camera SD card, a USB stick or an external SSD plugged in
in the field. This module lists those block devices (via ``lsblk``), works out
which ones are off-limits — the disk the OS boots from and the disk holding the
media buffer — and drives the privileged actions (mount, unmount, eject,
format) through a single root helper (``deploy/storage/storage.sh``) wired up
with a NOPASSWD sudoers rule by the installer.

Safety is layered: the API refuses actions on protected devices, and the root
helper re-checks the same invariants on its own, so a bug (or a compromised
service account) still can't format the system disk through it.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

from .config import get_settings

# Only real disks/partitions are offered — no loop/zram/ram/optical devices.
DEVICE_RE = re.compile(
    r"^/dev/(sd[a-z]+\d*|vd[a-z]+\d*|xvd[a-z]+\d*|mmcblk\d+(p\d+)?|nvme\d+n\d+(p\d+)?)$"
)

FILESYSTEMS = ("exfat", "vfat", "ext4")
# Max label length per filesystem (vfat is the strictest at 11 chars).
LABEL_MAX = {"exfat": 15, "vfat": 11, "ext4": 16}
LABEL_RE = re.compile(r"^[A-Za-z0-9_-]*$")

# Removable media live under these roots. A filesystem mounted anywhere else
# (/, /boot/firmware, /srv, swap …) marks its whole disk as a system disk — the
# same rule the root helper enforces.
_MEDIA_ROOTS = ("/media/", "/mnt/", "/run/media/")

_LSBLK_COLUMNS = "NAME,PATH,TYPE,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINT,RM,HOTPLUG,TRAN,MODEL,VENDOR,RO"

COMMAND_TIMEOUT = 900  # seconds; formatting a large ext4 disk takes a while


class StorageError(RuntimeError):
    """A storage action failed or was refused (message is user-facing)."""


@dataclass
class Partition:
    name: str
    path: str
    size: int
    fstype: str
    label: str
    uuid: str
    mountpoint: str
    read_only: bool
    total: int = 0
    used: int = 0
    free: int = 0
    system: bool = False
    buffer: bool = False
    cache: bool = False  # marked as cache for offloading cards (by UUID)
    buffer_drive: bool = False  # marked as additional media buffer (by UUID)

    @property
    def protected(self) -> bool:
        return self.system or self.buffer

    @property
    def browsable(self) -> bool:
        return bool(self.mountpoint) and not self.protected


@dataclass
class Disk:
    name: str
    path: str
    size: int
    model: str
    vendor: str
    transport: str
    removable: bool
    read_only: bool
    # A filesystem written straight onto the disk (no partition table) is
    # modelled as a single pseudo-partition with the disk's own path.
    partitions: list[Partition] = field(default_factory=list)
    system: bool = False
    buffer: bool = False

    @property
    def protected(self) -> bool:
        return self.system or self.buffer


# --- Discovery -------------------------------------------------------------


def _bool(value) -> bool:
    # lsblk < 2.33 emits "0"/"1" strings in JSON mode; newer emits booleans.
    if isinstance(value, str):
        return value.strip() in {"1", "true"}
    return bool(value)


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _str(value) -> str:
    return (value or "").strip() if isinstance(value, str) else ""


def _run_lsblk() -> dict:
    proc = subprocess.run(
        ["lsblk", "-J", "-b", "-o", _LSBLK_COLUMNS],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if proc.returncode != 0:
        raise StorageError(proc.stderr.strip() or "lsblk fehlgeschlagen")
    return json.loads(proc.stdout or "{}")


def _mount_of(path: Path, mountpoints: list[str]) -> str:
    """Longest mountpoint that contains ``path`` (resolved)."""
    try:
        target = str(path.resolve())
    except OSError:
        target = str(path)
    best = ""
    for mp in mountpoints:
        if not mp:
            continue
        if (target == mp or target.startswith(mp.rstrip("/") + "/")) and len(mp) > len(best):
            best = mp
    return best


def _partition_from(node: dict, parent: dict | None = None) -> Partition:
    part = Partition(
        name=_str(node.get("name")),
        path=_str(node.get("path")) or f"/dev/{_str(node.get('name'))}",
        size=_int(node.get("size")),
        fstype=_str(node.get("fstype")),
        label=_str(node.get("label")),
        uuid=_str(node.get("uuid")),
        mountpoint=_str(node.get("mountpoint")),
        read_only=_bool(node.get("ro")) or bool(parent and _bool(parent.get("ro"))),
    )
    if part.mountpoint and part.mountpoint != "[SWAP]":
        try:
            st = os.statvfs(part.mountpoint)
            part.total = st.f_blocks * st.f_frsize
            part.free = st.f_bavail * st.f_frsize
            part.used = part.total - st.f_bfree * st.f_frsize
        except OSError:
            pass
    return part


def parse_lsblk(data: dict, buffer_paths: list[Path] | None = None) -> list[Disk]:
    """Turn ``lsblk -J`` output into disks with protection flags set."""
    disks: list[Disk] = []
    for node in data.get("blockdevices", []) or []:
        path = _str(node.get("path")) or f"/dev/{_str(node.get('name'))}"
        if _str(node.get("type")) != "disk" or not DEVICE_RE.match(path):
            continue
        if _int(node.get("size")) == 0:
            continue  # empty card reader slot
        disk = Disk(
            name=_str(node.get("name")),
            path=path,
            size=_int(node.get("size")),
            model=_str(node.get("model")),
            vendor=_str(node.get("vendor")),
            transport=_str(node.get("tran")),
            removable=_bool(node.get("rm")) or _bool(node.get("hotplug")),
            read_only=_bool(node.get("ro")),
        )
        children = [c for c in node.get("children", []) or [] if _str(c.get("type")) == "part"]
        if children:
            disk.partitions = [_partition_from(c, node) for c in children]
        elif _str(node.get("fstype")) or _str(node.get("mountpoint")):
            disk.partitions = [_partition_from(node)]
        disks.append(disk)

    mountpoints = [p.mountpoint for d in disks for p in d.partitions if p.mountpoint]
    buffer_mounts = {_mount_of(p, mountpoints) for p in (buffer_paths or [])} - {""}
    for disk in disks:
        for part in disk.partitions:
            mp = part.mountpoint
            part.system = bool(mp) and not mp.startswith(_MEDIA_ROOTS)
            part.buffer = mp in buffer_mounts
        disk.system = any(p.system for p in disk.partitions)
        disk.buffer = any(p.buffer for p in disk.partitions)
    return disks


def cache_uuids() -> set[str]:
    from .db import SessionLocal
    from .models import StorageCache

    with SessionLocal() as db:
        return set(db.scalars(select(StorageCache.uuid)))


def buffer_uuids() -> set[str]:
    from .db import SessionLocal
    from .models import StorageBuffer

    with SessionLocal() as db:
        return set(db.scalars(select(StorageBuffer.uuid)))


def list_disks() -> list[Disk]:
    settings = get_settings()
    disks = parse_lsblk(_run_lsblk(), [settings.buffer_dir, settings.data_dir])
    caches = cache_uuids()
    buffers = buffer_uuids()
    for disk in disks:
        for part in disk.partitions:
            part.cache = bool(part.uuid) and part.uuid in caches and not part.protected
            part.buffer_drive = bool(part.uuid) and part.uuid in buffers and not part.protected
    return disks


def _check_buffer_pending(parts: list[Partition]) -> None:
    from . import buffers
    from .db import SessionLocal

    with SessionLocal() as db:
        for part in parts:
            buffers.check_removable(db, part)


_cached: tuple[float, list[Disk]] | None = None
_CACHE_TTL = 3.0


def list_disks_cached() -> list[Disk]:
    """``list_disks`` memoised for a few seconds.

    The gallery fires hundreds of thumbnail requests at once; re-running lsblk
    and a DB query for each would cost more than the thumbnails themselves.
    """
    global _cached
    now = time.monotonic()
    if _cached is None or now - _cached[0] > _CACHE_TTL:
        _cached = (now, list_disks())
    return _cached[1]


def clear_cache() -> None:
    global _cached
    _cached = None


def find_disk(device: str) -> Disk:
    for disk in list_disks():
        if disk.path == device:
            return disk
    raise StorageError(f"Laufwerk {device} nicht gefunden")


def find_partition(device: str) -> tuple[Disk, Partition]:
    for disk in list_disks():
        for part in disk.partitions:
            if part.path == device:
                return disk, part
    raise StorageError(f"Partition {device} nicht gefunden")


# --- Privileged actions ------------------------------------------------------


def manage_enabled() -> bool:
    return bool(get_settings().storage_command.strip())


def _helper(*args: str) -> str:
    command = get_settings().storage_command.strip()
    if not command:
        raise StorageError(
            "Speicherverwaltung ist nicht eingerichtet (OGC_STORAGE_COMMAND leer)."
        )
    try:
        proc = subprocess.run(
            [*shlex.split(command), *args],
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as exc:
        raise StorageError(f"Speicher-Helfer nicht gefunden: {exc.filename}") from None
    except subprocess.TimeoutExpired:
        raise StorageError("Zeitüberschreitung beim Speicher-Helfer") from None
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise StorageError(msg[-1] if msg else f"Speicher-Helfer: Exit {proc.returncode}")
    return (proc.stdout or "").strip()


def mount(device: str) -> str:
    disk, part = find_partition(device)
    if part.protected:
        raise StorageError("System- oder Pufferlaufwerk kann hier nicht verwaltet werden.")
    if part.mountpoint:
        return part.mountpoint
    if not part.fstype:
        raise StorageError("Kein Dateisystem erkannt — erst formatieren.")
    mountpoint = _helper("mount", part.path)
    if part.buffer_drive:
        from . import buffers

        buffers.resume(part.uuid)
    return mountpoint


def unmount(device: str) -> None:
    _disk, part = find_partition(device)
    if part.protected:
        raise StorageError("System- oder Pufferlaufwerk kann nicht ausgehängt werden.")
    if not part.mountpoint:
        return
    from . import storage_import

    if storage_import.is_busy(part.path):
        raise StorageError("Auf diesem Medium läuft gerade ein Import.")
    _check_buffer_pending([part])
    _helper("unmount", part.path)
    if part.buffer_drive:
        from . import buffers

        buffers.suspend(part.uuid)


def eject(device: str) -> None:
    disk = find_disk(device)
    if disk.protected:
        raise StorageError("System- oder Pufferlaufwerk kann nicht ausgeworfen werden.")
    from . import storage_import

    if any(storage_import.is_busy(p.path) for p in disk.partitions):
        raise StorageError("Auf diesem Medium läuft gerade ein Import.")
    _check_buffer_pending(disk.partitions)
    _helper("eject", disk.path)
    from . import buffers

    for part in disk.partitions:
        if part.buffer_drive:
            buffers.suspend(part.uuid)


def format_device(device: str, fstype: str, label: str) -> None:
    if fstype not in FILESYSTEMS:
        raise StorageError(f"Dateisystem muss eines von {', '.join(FILESYSTEMS)} sein.")
    if not LABEL_RE.match(label) or len(label) > LABEL_MAX[fstype]:
        raise StorageError(
            f"Bezeichnung: nur A–Z, 0–9, _ und -, max. {LABEL_MAX[fstype]} Zeichen."
        )
    target_disk = next((d for d in list_disks() if d.path == device), None)
    if target_disk is not None:
        disk = target_disk
    else:
        disk, _part = find_partition(device)
    if disk.protected:
        raise StorageError("System- oder Pufferlaufwerk kann nicht formatiert werden.")
    if any(p.cache for p in disk.partitions):
        raise StorageError("Laufwerk ist als Cache markiert — erst die Markierung entfernen.")
    if any(p.buffer_drive for p in disk.partitions):
        raise StorageError("Laufwerk ist als Puffer markiert — erst die Markierung entfernen.")
    if disk.read_only:
        raise StorageError("Laufwerk ist schreibgeschützt.")
    from . import storage_import

    if any(storage_import.is_busy(p.path) for p in disk.partitions):
        raise StorageError("Auf diesem Medium läuft gerade ein Import.")
    _helper("format", device, fstype, label)


# --- Browsing ----------------------------------------------------------------


def resolve_inside(root: str, rel: str) -> Path:
    """Resolve ``rel`` under mountpoint ``root``; refuse anything escaping it."""
    base = Path(root).resolve()
    target = (base / rel.lstrip("/")).resolve()
    if target != base and base not in target.parents:
        raise StorageError("Pfad liegt außerhalb des Mediums.")
    return target


def browsable_root(device: str) -> str:
    _disk, part = find_partition(device)
    if not part.browsable:
        raise StorageError("Medium ist nicht eingehängt oder geschützt.")
    return part.mountpoint


# --- Cache drives ---------------------------------------------------------------


def cache_root(part: Partition) -> Path:
    """The card-copy directory on a cache partition, created if missing.

    Media mounted by our helper as exFAT/FAT already belong to the service; an
    ext4 drive formatted elsewhere is root-owned, so the helper creates the
    directory and hands it over. Without the helper we try directly.
    """
    from .storage_import import CACHE_DIRNAME

    if not part.mountpoint:
        raise StorageError("Cache-Laufwerk ist nicht eingehängt.")
    root = Path(part.mountpoint) / CACHE_DIRNAME
    if not root.is_dir() or not os.access(root, os.W_OK):
        if manage_enabled():
            _helper("prepare-cache", part.path)
        else:
            try:
                root.mkdir(exist_ok=True)
            except OSError as exc:
                raise StorageError(f"Cache-Ordner nicht anlegbar: {exc.strerror}") from None
    if not os.access(root, os.W_OK):
        raise StorageError("Cache-Ordner ist nicht beschreibbar.")
    return root
