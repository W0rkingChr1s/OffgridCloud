"""Folders: admin management + access-scoped listing for users."""

from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..admin_ops import audit
from ..db import get_db
from ..deps import get_current_user, require_admin
from ..models import (
    CloudProvider,
    FolderAccess,
    FolderGroupAccess,
    FolderProviderLink,
    Group,
    MediaItem,
    Role,
    TransferJob,
    TransferStatus,
    UploadFolder,
    User,
)
from ..schemas import (
    FolderAccessUpdate,
    FolderCreate,
    FolderGroupsUpdate,
    FolderOut,
    FolderProviderLinkCreate,
    FolderProviderLinkOut,
    FolderProviderLinkUpdate,
    FolderUpdate,
    MediaItemOut,
)
from ..storage import accessible_folder_ids, folder_dir, user_can_access_folder
from ..transfers import enqueue_for_link

router = APIRouter(prefix="/api/folders", tags=["folders"])


def _to_out(db: Session, folder: UploadFolder) -> FolderOut:
    user_ids = list(
        db.scalars(select(FolderAccess.user_id).where(FolderAccess.folder_id == folder.id))
    )
    group_ids = list(
        db.scalars(
            select(FolderGroupAccess.group_id).where(FolderGroupAccess.folder_id == folder.id)
        )
    )
    count = db.scalar(
        select(func.count(MediaItem.id)).where(MediaItem.folder_id == folder.id)
    )
    return FolderOut(
        id=folder.id,
        name=folder.name,
        description=folder.description,
        created_at=folder.created_at,
        user_ids=user_ids,
        group_ids=group_ids,
        media_count=count or 0,
    )


@router.get("", response_model=list[FolderOut])
def list_folders(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[FolderOut]:
    """Admins see all folders; users see only the ones shared with them."""
    if user.role == Role.ADMIN:
        folders = db.scalars(select(UploadFolder).order_by(UploadFolder.name)).all()
    else:
        ids = accessible_folder_ids(db, user)
        folders = (
            db.scalars(
                select(UploadFolder)
                .where(UploadFolder.id.in_(ids))
                .order_by(UploadFolder.name)
            ).all()
            if ids
            else []
        )
    return [_to_out(db, f) for f in folders]


@router.post("", response_model=FolderOut, status_code=status.HTTP_201_CREATED)
def create_folder(
    payload: FolderCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> FolderOut:
    folder = UploadFolder(name=payload.name, description=payload.description)
    db.add(folder)
    db.commit()
    db.refresh(folder)
    folder_dir(folder.id)  # create on disk
    audit(db, admin, "folder.create", folder.name)
    return _to_out(db, folder)


def _get_folder(db: Session, folder_id: int) -> UploadFolder:
    folder = db.get(UploadFolder, folder_id)
    if folder is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")
    return folder


@router.patch("/{folder_id}", response_model=FolderOut)
def update_folder(
    folder_id: int,
    payload: FolderUpdate,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> FolderOut:
    folder = _get_folder(db, folder_id)
    if payload.name is not None:
        folder.name = payload.name
    if payload.description is not None:
        folder.description = payload.description
    db.commit()
    db.refresh(folder)
    return _to_out(db, folder)


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_folder(
    folder_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    folder = _get_folder(db, folder_id)
    name = folder.name
    db.delete(folder)  # cascades access + media rows
    db.commit()
    shutil.rmtree(folder_dir(folder_id), ignore_errors=True)
    audit(db, admin, "folder.delete", name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{folder_id}/access", response_model=FolderOut)
def set_access(
    folder_id: int,
    payload: FolderAccessUpdate,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> FolderOut:
    folder = _get_folder(db, folder_id)

    # Validate target users exist.
    valid_ids = set(
        db.scalars(select(User.id).where(User.id.in_(payload.user_ids))).all()
    )
    unknown = set(payload.user_ids) - valid_ids
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown user ids: {sorted(unknown)}")

    # Replace the full access set.
    db.query(FolderAccess).filter(FolderAccess.folder_id == folder.id).delete()
    for uid in valid_ids:
        db.add(FolderAccess(folder_id=folder.id, user_id=uid))
    db.commit()
    db.refresh(folder)
    return _to_out(db, folder)


@router.put("/{folder_id}/groups", response_model=FolderOut)
def set_group_access(
    folder_id: int,
    payload: FolderGroupsUpdate,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> FolderOut:
    folder = _get_folder(db, folder_id)
    valid_ids = set(db.scalars(select(Group.id).where(Group.id.in_(payload.group_ids))))
    unknown = set(payload.group_ids) - valid_ids
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unbekannte Gruppen: {sorted(unknown)}")
    db.query(FolderGroupAccess).filter(FolderGroupAccess.folder_id == folder.id).delete()
    for gid in valid_ids:
        db.add(FolderGroupAccess(folder_id=folder.id, group_id=gid))
    db.commit()
    db.refresh(folder)
    return _to_out(db, folder)


@router.get("/{folder_id}/providers", response_model=list[FolderProviderLinkOut])
def list_links(
    folder_id: int,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[FolderProviderLinkOut]:
    _get_folder(db, folder_id)
    links = db.scalars(
        select(FolderProviderLink).where(FolderProviderLink.folder_id == folder_id)
    ).all()
    names = dict(db.execute(select(CloudProvider.id, CloudProvider.name)).all())
    return [
        FolderProviderLinkOut(
            id=link.id,
            folder_id=link.folder_id,
            provider_id=link.provider_id,
            provider_name=names.get(link.provider_id, ""),
            dest_path=link.dest_path,
            priority=link.priority,
            enabled=link.enabled,
        )
        for link in links
    ]


@router.post(
    "/{folder_id}/providers",
    response_model=FolderProviderLinkOut,
    status_code=status.HTTP_201_CREATED,
)
def add_link(
    folder_id: int,
    payload: FolderProviderLinkCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> FolderProviderLinkOut:
    _get_folder(db, folder_id)
    provider = db.get(CloudProvider, payload.provider_id)
    if provider is None:
        raise HTTPException(status_code=400, detail="Provider not found")
    existing = db.scalar(
        select(FolderProviderLink).where(
            FolderProviderLink.folder_id == folder_id,
            FolderProviderLink.provider_id == payload.provider_id,
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Provider bereits verknüpft")

    link = FolderProviderLink(
        folder_id=folder_id,
        provider_id=payload.provider_id,
        dest_path=payload.dest_path,
        priority=payload.priority,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    # Backfill transfer jobs for media already in the folder.
    enqueue_for_link(db, link)
    db.commit()
    audit(db, admin, "folder.link_add", f"folder={folder_id} provider={provider.name}")
    return FolderProviderLinkOut(
        id=link.id,
        folder_id=link.folder_id,
        provider_id=link.provider_id,
        provider_name=provider.name,
        dest_path=link.dest_path,
        priority=link.priority,
        enabled=link.enabled,
    )


@router.patch(
    "/{folder_id}/providers/{provider_id}", response_model=FolderProviderLinkOut
)
def update_link(
    folder_id: int,
    provider_id: int,
    payload: FolderProviderLinkUpdate,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> FolderProviderLinkOut:
    link = db.scalar(
        select(FolderProviderLink).where(
            FolderProviderLink.folder_id == folder_id,
            FolderProviderLink.provider_id == provider_id,
        )
    )
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found")
    if payload.dest_path is not None:
        link.dest_path = payload.dest_path
    if payload.enabled is not None:
        link.enabled = payload.enabled
    if payload.priority is not None:
        link.priority = payload.priority
        # Propagate to still-queued jobs so re-prioritisation takes effect.
        media_ids = select(MediaItem.id).where(MediaItem.folder_id == folder_id)
        db.query(TransferJob).filter(
            TransferJob.provider_id == provider_id,
            TransferJob.media_id.in_(media_ids),
            TransferJob.status == TransferStatus.QUEUED,
        ).update({TransferJob.priority: payload.priority}, synchronize_session=False)
    db.commit()
    db.refresh(link)
    provider = db.get(CloudProvider, provider_id)
    return FolderProviderLinkOut(
        id=link.id,
        folder_id=link.folder_id,
        provider_id=link.provider_id,
        provider_name=provider.name if provider else "",
        dest_path=link.dest_path,
        priority=link.priority,
        enabled=link.enabled,
    )


@router.delete(
    "/{folder_id}/providers/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def remove_link(
    folder_id: int,
    provider_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    link = db.scalar(
        select(FolderProviderLink).where(
            FolderProviderLink.folder_id == folder_id,
            FolderProviderLink.provider_id == provider_id,
        )
    )
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found")
    db.delete(link)
    db.commit()
    audit(db, admin, "folder.link_remove", f"folder={folder_id} provider={provider_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{folder_id}/media", response_model=list[MediaItemOut])
def list_media(
    folder_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MediaItem]:
    _get_folder(db, folder_id)
    if not user_can_access_folder(db, user, folder_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to folder")
    return list(
        db.scalars(
            select(MediaItem)
            .where(MediaItem.folder_id == folder_id)
            .order_by(MediaItem.created_at.desc())
        )
    )
