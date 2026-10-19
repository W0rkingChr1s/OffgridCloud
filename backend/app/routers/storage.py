"""Attached storage media: list, mount/unmount/eject, format, browse, import (admin)."""

from __future__ import annotations

import os
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from .. import drives, storage_import
from ..admin_ops import audit, disk_usage
from ..db import get_db
from ..deps import require_admin
from ..models import UploadFolder, User
from ..schemas import (
    DiskUsageOut,
    StorageBrowseOut,
    StorageDeviceRequest,
    StorageDiskOut,
    StorageEntryOut,
    StorageFormatRequest,
    StorageImportJobOut,
    StorageImportRequest,
    StorageOverviewOut,
    StoragePartitionOut,
)

router = APIRouter(prefix="/api/storage", tags=["storage"], dependencies=[Depends(require_admin)])

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
            )
            for p in disk.partitions
        ],
    )


@router.get("", response_model=StorageOverviewOut)
def overview() -> StorageOverviewOut:
    error = ""
    try:
        disks = drives.list_disks()
    except (drives.StorageError, OSError, ValueError) as exc:
        disks, error = [], f"Laufwerke konnten nicht gelesen werden: {exc}"
    return StorageOverviewOut(
        manage_enabled=drives.manage_enabled(),
        filesystems=list(drives.FILESYSTEMS),
        label_max=drives.LABEL_MAX,
        disks=[_disk_out(d) for d in disks],
        buffer=DiskUsageOut(**disk_usage()),
        error=error,
    )


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
        if entry.is_symlink():
            continue
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
