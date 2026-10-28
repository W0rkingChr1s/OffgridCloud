"""Attached storage media: list, mount/unmount/eject, format, browse, import (admin)."""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .. import auto_import, buffers, drives, status_led, storage_import, thumbnails
from ..admin_ops import audit, disk_usage
from ..db import SessionLocal, get_db
from ..deps import require_admin, user_from_query_token
from ..models import Role, StorageBuffer, StorageCache, UploadFolder, User
from ..schemas import (
    AutoImportOut,
    AutoImportUpdate,
    DiskUsageOut,
    LedTestRequest,
    StorageBrowseOut,
    StorageBufferOut,
    StorageCacheBackupRequest,
    StorageCacheOut,
    StorageCacheSnapshotOut,
    StorageDeviceRequest,
    StorageDiskOut,
    StorageEntryOut,
    StorageFormatRequest,
    StorageGalleryItem,
    StorageGalleryOut,
    StorageImportJobOut,
    StorageImportRequest,
    StorageKnownOut,
    StorageOverviewOut,
    StoragePartitionOut,
)
from ..storage import safe_filename

router = APIRouter(prefix="/api/storage", tags=["storage"], dependencies=[Depends(require_admin)])
# Thumbnails are loaded by <img src>, which can't send a bearer header — they
# authenticate with ?token= instead (see thumb()).
public_router = APIRouter(prefix="/api/storage", tags=["storage"])

MAX_ENTRIES = 2000


def _bad(exc: drives.StorageError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _disk_out(disk: drives.Disk) -> StorageDiskOut:
    return StorageDiskOut(
        name=disk.name,
        path=disk.path,
        size=disk.size,
        model=disk.model,
        vendor=disk.vendor,
        transport=disk.transport,
        removable=disk.removable,
        read_only=disk.read_only,
        system=disk.system,
        buffer=disk.buffer,
        protected=disk.protected,
        partitions=[
            StoragePartitionOut(
                name=p.name,
                path=p.path,
                size=p.size,
                fstype=p.fstype,
                label=p.label,
                uuid=p.uuid,
                mountpoint=p.mountpoint,
                read_only=p.read_only,
                total=p.total,
                used=p.used,
                free=p.free,
                system=p.system,
                buffer=p.buffer,
                protected=p.protected,
                browsable=p.browsable,
                busy=storage_import.is_busy(p.path),
                cache=p.cache,
                buffer_drive=p.buffer_drive,
            )
            for p in disk.partitions
        ],
    )


@router.get("", response_model=StorageOverviewOut)
def overview(db: Session = Depends(get_db)) -> StorageOverviewOut:
    error = ""
    try:
        disks = drives.list_disks()
    except (drives.StorageError, OSError, ValueError) as exc:
        disks, error = [], f"Laufwerke konnten nicht gelesen werden: {exc}"
    if disks:
        try:
            buffers.sync(db, disks)
        except (drives.StorageError, OSError):
            pass  # the watch loop retries; the page must still load
    return StorageOverviewOut(
        manage_enabled=drives.manage_enabled(),
        filesystems=list(drives.FILESYSTEMS),
        label_max=drives.LABEL_MAX,
        disks=[_disk_out(d) for d in disks],
        buffer=DiskUsageOut(**disk_usage()),
        buffers=_buffers_out(db, disks),
        error=error,
    )


# --- Buffer drives ----------------------------------------------------------------


def _buffers_out(db: Session, disks: list[drives.Disk]) -> list[StorageBufferOut]:
    parts = {p.uuid: p for d in disks for p in d.partitions if p.uuid}
    online = {t.uuid: t for t in buffers.drive_targets(db)}
    out: list[StorageBufferOut] = []
    for row in db.scalars(select(StorageBuffer).order_by(StorageBuffer.created_at)):
        part = parts.get(row.uuid)
        target = online.get(row.uuid)
        entry = StorageBufferOut(
            uuid=row.uuid, label=row.label, device=part.path if part else None,
            mountpoint=row.mountpoint, online=target is not None,
            total=target.total if target else 0, used=target.used if target else 0,
            free=target.free if target else 0, pending=buffers.pending_on(db, row.mountpoint),
        )
        if part is None:
            entry.error = "nicht angeschlossen"
        elif not part.mountpoint:
            entry.error = "nicht eingehängt"
        elif target is None:
            entry.error = "Puffer-Ordner fehlt oder ist nicht beschreibbar"
        out.append(entry)
    return out


@router.post("/buffers", status_code=status.HTTP_201_CREATED,
             response_class=Response)
def mark_buffer(
    payload: StorageDeviceRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    try:
        _disk, part = drives.find_partition(payload.device)
        if part.protected:
            raise drives.StorageError("System- oder Pufferlaufwerk ist schon Teil der Box.")
        if not part.uuid:
            raise drives.StorageError("Partition hat keine UUID — erst formatieren.")
        if part.read_only:
            raise drives.StorageError("Laufwerk ist schreibgeschützt.")
        if part.fstype == "vfat":
            raise drives.StorageError(
                "FAT32 kann keine Dateien über 4 GB speichern — als Puffer erst mit "
                "exFAT oder ext4 formatieren."
            )
        buffers.prepare(part)
    except drives.StorageError as exc:
        raise _bad(exc) from None
    row = db.scalar(select(StorageBuffer).where(StorageBuffer.uuid == part.uuid))
    if row is None:
        db.add(StorageBuffer(uuid=part.uuid, label=part.label or part.name,
                             mountpoint=part.mountpoint))
    else:
        row.mountpoint = part.mountpoint
    db.commit()
    drives.clear_cache()
    audit(db, admin, "storage.buffer.mark", f"{part.path} ({part.uuid})")
    return Response(status_code=status.HTTP_201_CREATED)


@router.delete("/buffers/{buffer_uuid}", status_code=status.HTTP_204_NO_CONTENT,
               response_class=Response)
def unmark_buffer(
    buffer_uuid: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> Response:
    row = db.scalar(select(StorageBuffer).where(StorageBuffer.uuid == buffer_uuid))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kein Puffer-Laufwerk")
    # Attached: it must be empty of pending files first. Unplugged (lost stick):
    # the admin may give up on it — its pending files then show up as failed.
    if any(t.uuid == row.uuid for t in buffers.drive_targets(db)):
        pending = buffers.pending_on(db, row.mountpoint)
        if pending:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Auf dem Laufwerk warten noch {pending} Datei(en) auf den Upload.",
            )
    db.delete(row)
    db.commit()
    drives.clear_cache()
    audit(db, admin, "storage.buffer.unmark", buffer_uuid)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/mount")
def mount(
    payload: StorageDeviceRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    try:
        mountpoint = drives.mount(payload.device)
    except drives.StorageError as exc:
        raise _bad(exc) from None
    audit(db, admin, "storage.mount", f"{payload.device} -> {mountpoint}")
    return {"mountpoint": mountpoint}


@router.post("/unmount", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def unmount(
    payload: StorageDeviceRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    try:
        drives.unmount(payload.device)
    except drives.StorageError as exc:
        raise _bad(exc) from None
    audit(db, admin, "storage.unmount", payload.device)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/eject", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def eject(
    payload: StorageDeviceRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    try:
        drives.eject(payload.device)
    except drives.StorageError as exc:
        raise _bad(exc) from None
    audit(db, admin, "storage.eject", payload.device)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/format", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def format_device(
    payload: StorageFormatRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    if payload.confirm.strip() != payload.device.rsplit("/", 1)[-1]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bestätigung stimmt nicht mit dem Gerätenamen überein.",
        )
    try:
        drives.format_device(payload.device, payload.fstype, payload.label)
    except drives.StorageError as exc:
        raise _bad(exc) from None
    audit(db, admin, "storage.format", f"{payload.device} as {payload.fstype} '{payload.label}'")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/browse", response_model=StorageBrowseOut)
def browse(device: str, path: str = Query("", max_length=4096)) -> StorageBrowseOut:
    try:
        root = drives.browsable_root(device)
        target = drives.resolve_inside(root, path)
    except drives.StorageError as exc:
        raise _bad(exc) from None
    if not target.is_dir():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ordner nicht gefunden")

    entries: list[StorageEntryOut] = []
    truncated = False
    try:
        with os.scandir(target) as it:
            items = sorted(it, key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Kein Zugriff") from None
    for entry in items:
        if entry.is_symlink() or entry.name == buffers.BUFFER_DIRNAME:
            continue  # the box's own buffer on a buffer drive is not for browsing
        if len(entries) >= MAX_ENTRIES:
            truncated = True
            break
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        is_dir = entry.is_dir(follow_symlinks=False)
        rel = os.path.relpath(entry.path, root)
        entries.append(
            StorageEntryOut(
                name=entry.name,
                path=rel,
                is_dir=is_dir,
                size=0 if is_dir else st.st_size,
                modified=datetime.fromtimestamp(st.st_mtime, UTC),
                is_media=not is_dir and storage_import.is_media_file(entry.name),
            )
        )

    files = [e for e in entries if not e.is_dir]
    if files:
        with SessionLocal() as db:
            known = storage_import.known_files(
                db, [(e.name, e.size, e.modified.timestamp()) for e in files if e.modified]
            )
        for e in files:
            hit = known.get((safe_filename(e.name), e.size))
            if hit:
                e.known = StorageKnownOut(**hit)

    rel_here = os.path.relpath(target, root)
    rel_here = "" if rel_here == "." else rel_here
    parent = None
    if rel_here:
        parent = os.path.dirname(rel_here)
    return StorageBrowseOut(
        device=device,
        mountpoint=root,
        path=rel_here,
        parent=parent,
        entries=entries,
        truncated=truncated,
    )


@router.get("/imports", response_model=list[StorageImportJobOut])
def list_imports() -> list[dict]:
    return [j.to_dict() for j in storage_import.list_jobs()]


@router.post(
    "/imports", response_model=StorageImportJobOut, status_code=status.HTTP_202_ACCEPTED
)
def start_import(
    payload: StorageImportRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    folder = db.get(UploadFolder, payload.folder_id)
    if folder is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")
    try:
        root = drives.browsable_root(payload.device)
        targets = [drives.resolve_inside(root, p) for p in payload.paths]
        files = storage_import.collect_files(
            drives.resolve_inside(root, ""), targets, payload.media_only
        )
    except drives.StorageError as exc:
        raise _bad(exc) from None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Keine passenden Dateien in der Auswahl.",
        )

    needed = sum(storage_import._size(f) for f in files)
    free = disk_usage()["free"]
    if needed + storage_import.SPACE_RESERVE > free:
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail=(
                f"Zu wenig Platz im Puffer: {needed // 2**20} MiB benötigt, "
                f"{max(free - storage_import.SPACE_RESERVE, 0) // 2**20} MiB verfügbar."
            ),
        )

    job = storage_import.start(
        device=payload.device,
        root=str(drives.resolve_inside(root, "")),
        files=files,
        folder_id=folder.id,
        folder_name=folder.name,
        user_id=admin.id,
        tags=payload.tags,
        skip_duplicates=payload.skip_duplicates,
    )
    audit(
        db,
        admin,
        "storage.import",
        f"{len(files)} file(s) from {payload.device} into folder {folder.id}",
    )
    return job.to_dict()


@router.post("/imports/{job_id}/cancel", status_code=status.HTTP_204_NO_CONTENT,
             response_class=Response)
def cancel_import(job_id: str) -> Response:
    if not storage_import.cancel(job_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kein laufender Import")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/imports", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def clear_imports() -> Response:
    storage_import.clear_finished()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Gallery ---------------------------------------------------------------------


@router.get("/gallery", response_model=StorageGalleryOut)
def gallery(device: str, path: str = Query("", max_length=4096)) -> StorageGalleryOut:
    """Every media file below ``path`` (recursively), for the photo grid.

    Camera cards spread one shoot over DCIM/100CANON, 101CANON …; the gallery
    flattens that and the UI groups by capture day instead.
    """
    try:
        root = drives.browsable_root(device)
        target = drives.resolve_inside(root, path)
        files = storage_import.walk_files(target, media_only=True)
    except drives.StorageError as exc:
        raise _bad(exc) from None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    base = drives.resolve_inside(root, "")
    items: list[StorageGalleryItem] = []
    triples = []
    for f in files:
        size, mtime = storage_import._stat(f)
        rel = str(f.relative_to(base))
        items.append(
            StorageGalleryItem(
                path=rel,
                name=f.name,
                dir=os.path.dirname(rel),
                size=size,
                mtime=mtime,
                kind=storage_import.media_kind(f.name),
            )
        )
        triples.append((f.name, size, mtime))
    with SessionLocal() as db:
        known = storage_import.known_files(db, triples)
    for it in items:
        hit = known.get((safe_filename(it.name), it.size))
        if hit:
            it.known = StorageKnownOut(**hit)
    items.sort(key=lambda i: (i.mtime, i.name))
    rel_here = os.path.relpath(target, base)
    return StorageGalleryOut(
        device=device, mountpoint=root, path="" if rel_here == "." else rel_here, items=items
    )


@public_router.get("/thumb")
async def thumb(
    device: str,
    path: str = Query(..., max_length=4096),
    token: str = Query(...),
    size: int = Query(320, ge=64, le=2048),
) -> FileResponse:
    # async on purpose: see thumbnails._EXECUTOR.
    src, key = await run_in_threadpool(_thumb_source, device, path, token)
    out = thumbnails.source_thumb_path(size, key)
    if not await run_in_threadpool(out.exists):
        out = await thumbnails.render(
            thumbnails.source_thumb, src, storage_import.media_kind(src.name), size, key
        )
    if out is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No thumbnail")
    return FileResponse(
        out, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"}
    )


def _thumb_source(device: str, path: str, token: str) -> tuple[Path, str]:
    """Check access, resolve the file and its cache key (quick, no rendering)."""
    with SessionLocal() as db:
        user = user_from_query_token(db, token)
        if user.role != Role.ADMIN:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    try:
        part = next(
            (p for d in drives.list_disks_cached() for p in d.partitions if p.path == device),
            None,
        )
        if part is None or not part.browsable:
            raise drives.StorageError("Medium ist nicht eingehängt oder geschützt.")
        src = drives.resolve_inside(part.mountpoint, path)
    except drives.StorageError as exc:
        raise _bad(exc) from None
    if not src.is_file() or src.is_symlink():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    fsize, mtime = storage_import._stat(src)
    return src, f"{part.uuid or device}|{path}|{fsize}|{int(mtime)}"


# --- Cache drives ----------------------------------------------------------------


def _partitions_by_uuid() -> dict[str, drives.Partition]:
    try:
        disks = drives.list_disks()
    except (drives.StorageError, OSError, ValueError):
        return {}
    return {p.uuid: p for d in disks for p in d.partitions if p.uuid}


def _snapshots(root: Path, mountpoint: str) -> list[StorageCacheSnapshotOut]:
    out: list[StorageCacheSnapshotOut] = []
    try:
        dirs = sorted((d for d in root.iterdir() if d.is_dir() and not d.is_symlink()),
                      key=lambda d: d.name, reverse=True)
    except OSError:
        return out
    for d in dirs:
        files = size = 0
        for dirpath, _dirs, names in os.walk(d):
            for n in names:
                if n.startswith("."):
                    continue
                try:
                    size += os.stat(os.path.join(dirpath, n)).st_size
                    files += 1
                except OSError:
                    pass
        try:
            modified = datetime.fromtimestamp(d.stat().st_mtime, UTC)
        except OSError:
            modified = None
        out.append(StorageCacheSnapshotOut(
            name=d.name, path=os.path.relpath(d, mountpoint), files=files, size=size,
            modified=modified,
        ))
    return out


@router.get("/caches", response_model=list[StorageCacheOut])
def list_caches(db: Session = Depends(get_db)) -> list[StorageCacheOut]:
    parts = _partitions_by_uuid()
    result: list[StorageCacheOut] = []
    for row in db.scalars(select(StorageCache).order_by(StorageCache.created_at)):
        part = parts.get(row.uuid)
        entry = StorageCacheOut(
            uuid=row.uuid, label=row.label, device=part.path if part else None,
            mountpoint=part.mountpoint if part else "", total=part.total if part else 0,
            free=part.free if part else 0, snapshots=[],
        )
        if part and part.mountpoint:
            root = Path(part.mountpoint) / storage_import.CACHE_DIRNAME
            if root.is_dir():
                entry.snapshots = _snapshots(root, part.mountpoint)
        elif part:
            entry.error = "nicht eingehängt"
        else:
            entry.error = "nicht angeschlossen"
        result.append(entry)
    return result


@router.post("/caches", response_model=StorageCacheOut, status_code=status.HTTP_201_CREATED)
def mark_cache(
    payload: StorageDeviceRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> StorageCacheOut:
    try:
        _disk, part = drives.find_partition(payload.device)
        if part.protected:
            raise drives.StorageError("System- oder Pufferlaufwerk kann kein Cache sein.")
        if not part.uuid:
            raise drives.StorageError("Partition hat keine UUID — erst formatieren.")
        if part.read_only:
            raise drives.StorageError("Laufwerk ist schreibgeschützt.")
        drives.cache_root(part)
    except drives.StorageError as exc:
        raise _bad(exc) from None
    if db.scalar(select(StorageCache).where(StorageCache.uuid == part.uuid)) is None:
        db.add(StorageCache(uuid=part.uuid, label=part.label or part.name))
        db.commit()
    audit(db, admin, "storage.cache.mark", f"{part.path} ({part.uuid})")
    return StorageCacheOut(
        uuid=part.uuid, label=part.label or part.name, device=part.path,
        mountpoint=part.mountpoint, total=part.total, free=part.free, snapshots=[],
    )


@router.delete("/caches/{cache_uuid}", status_code=status.HTTP_204_NO_CONTENT,
               response_class=Response)
def unmark_cache(
    cache_uuid: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> Response:
    row = db.scalar(select(StorageCache).where(StorageCache.uuid == cache_uuid))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kein Cache")
    db.delete(row)
    db.commit()
    audit(db, admin, "storage.cache.unmark", cache_uuid)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _cache_root_for(uuid: str | None, db: Session) -> tuple[drives.Partition, Path]:
    rows = list(db.scalars(select(StorageCache)))
    if not rows:
        raise drives.StorageError("Kein Laufwerk als Cache markiert.")
    parts = _partitions_by_uuid()
    candidates = [r for r in rows if (uuid is None or r.uuid == uuid) and r.uuid in parts]
    if not candidates:
        raise drives.StorageError("Cache-Laufwerk ist nicht angeschlossen.")
    part = parts[candidates[0].uuid]
    return part, drives.cache_root(part)


@router.post("/caches/backup", response_model=StorageImportJobOut,
             status_code=status.HTTP_202_ACCEPTED)
def backup_to_cache(
    payload: StorageCacheBackupRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    try:
        _disk, src_part = drives.find_partition(payload.device)
        if src_part.cache:
            raise drives.StorageError("Das ist selbst das Cache-Laufwerk.")
        root = drives.browsable_root(payload.device)
        base = drives.resolve_inside(root, "")
        if payload.paths:
            targets = [drives.resolve_inside(root, p) for p in payload.paths]
            files = storage_import.collect_files(base, targets, payload.media_only)
        else:
            files = storage_import.walk_files(base, media_only=payload.media_only)
        cache_part, cache_root = _cache_root_for(payload.cache_uuid, db)
    except drives.StorageError as exc:
        raise _bad(exc) from None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Keine passenden Dateien auf dem Medium.")
    needed = sum(storage_import._size(f) for f in files)
    if needed + storage_import.SPACE_RESERVE > cache_part.free:
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail=(f"Zu wenig Platz im Cache: {needed // 2**20} MiB benötigt, "
                    f"{max(cache_part.free - storage_import.SPACE_RESERVE, 0) // 2**20} MiB frei."),
        )
    snapshot = storage_import.snapshot_dir(cache_root, src_part.label or src_part.name)
    job = storage_import.start_cache(
        device=payload.device, root=str(base), files=files, cache_device=cache_part.path,
        snapshot_dir=snapshot, user_id=admin.id, only_new=payload.only_new,
        eject_after=payload.eject_after,
    )
    audit(db, admin, "storage.cache.backup",
          f"{len(files)} file(s) from {payload.device} to {cache_part.path}:{snapshot.name}")
    return job.to_dict()


@router.delete("/caches/{cache_uuid}/snapshots/{name}", status_code=status.HTTP_204_NO_CONTENT,
               response_class=Response)
def delete_snapshot(
    cache_uuid: str, name: str, admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    try:
        part, root = _cache_root_for(cache_uuid, db)
    except drives.StorageError as exc:
        raise _bad(exc) from None
    target = (root / name).resolve()
    if target.parent != root.resolve() or not target.is_dir() or (root / name).is_symlink():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nicht gefunden")
    if storage_import.is_busy(part.path):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Auf dem Cache läuft gerade ein Vorgang.")
    shutil.rmtree(target, ignore_errors=True)
    audit(db, admin, "storage.cache.delete", f"{part.path}:{name}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Auto-import + status LED ------------------------------------------------------


def _auto_import_out(db: Session) -> AutoImportOut:
    row = auto_import.get_row(db)
    return AutoImportOut(
        enabled=row.enabled,
        mode=row.mode,
        folder_id=row.folder_id,
        cache_uuid=row.cache_uuid,
        media_only=row.media_only,
        skip_duplicates=row.skip_duplicates,
        eject_after=row.eject_after,
        led_enabled=row.led_enabled,
        led_name=row.led_name,
        manage_enabled=drives.manage_enabled(),
        leds=status_led.available(),
        led_active=status_led.resolve(row.led_name),
        led_state=status_led.current(),
        media=[e.to_dict() for e in auto_import.entries() if e.state != "ignored"],
    )


@router.get("/auto-import", response_model=AutoImportOut)
def get_auto_import(db: Session = Depends(get_db)) -> AutoImportOut:
    return _auto_import_out(db)


@router.put("/auto-import", response_model=AutoImportOut)
def update_auto_import(
    payload: AutoImportUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AutoImportOut:
    row = auto_import.get_row(db)
    changes = payload.model_dump(exclude_unset=True)
    if "folder_id" in changes and changes["folder_id"] is not None:
        if db.get(UploadFolder, changes["folder_id"]) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="Folder not found")
    for key, value in changes.items():
        if value is None and key != "folder_id":
            continue
        setattr(row, key, value)
    enabling = changes.get("enabled") is True
    if enabling and row.mode == "import" and row.folder_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Erst einen Zielordner wählen.")
    if enabling and row.mode == "cache" and db.scalar(select(StorageCache)) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Erst ein Laufwerk als Cache markieren.")
    if changes:
        # Imported files are attributed to whoever set this up last.
        row.user_id = admin.id
    db.commit()
    audit(db, admin, "storage.auto_import",
          ", ".join(f"{k}={v}" for k, v in sorted(changes.items())) or "-")
    return _auto_import_out(db)


@router.post("/auto-import/led-test", status_code=status.HTTP_204_NO_CONTENT,
             response_class=Response)
def led_test(payload: LedTestRequest, db: Session = Depends(get_db)) -> Response:
    row = auto_import.get_row(db)
    if not status_led.resolve(row.led_name):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Keine Status-LED gefunden.")
    if not drives.manage_enabled():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Speicher-Helfer nicht eingerichtet (OGC_STORAGE_COMMAND).")
    status_led.preview(payload.state)
    # Show it right away instead of on the next watch round.
    auto_import._update_led(auto_import.load_config())
    return Response(status_code=status.HTTP_204_NO_CONTENT)
