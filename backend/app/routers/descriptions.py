"""Thematic descriptions: group media items and describe what they show.

Each description is stored for the UI *and* rendered into a plain-text ``.txt``
sidecar that is uploaded to every linked cloud target alongside the media (see
:mod:`app.descriptions`). Access mirrors uploads: anyone who may access a folder
may describe media in it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..admin_ops import audit
from ..db import get_db
from ..deps import get_current_user
from ..descriptions import (
    covered_media_ids,
    create_description,
    delete_description,
    update_description,
)
from ..models import MediaDescription, MediaItem, UploadFolder, User
from ..schemas import (
    DescriptionCreate,
    DescriptionDeleteResult,
    DescriptionOut,
    DescriptionUpdate,
)
from ..storage import user_can_access_folder

router = APIRouter(tags=["descriptions"])


def _to_out(db: Session, desc: MediaDescription) -> DescriptionOut:
    txt_filename = ""
    txt_status = None
    if desc.txt_media_id is not None:
        sidecar = db.get(MediaItem, desc.txt_media_id)
        if sidecar is not None:
            txt_filename = sidecar.filename
            txt_status = sidecar.status
        else:
            # Sidecar was deleted directly; keep the note but drop the dangling id.
            desc.txt_media_id = None
    return DescriptionOut(
        id=desc.id,
        folder_id=desc.folder_id,
        title=desc.title,
        body=desc.body,
        created_by=desc.created_by,
        created_at=desc.created_at,
        updated_at=desc.updated_at,
        media_ids=covered_media_ids(db, desc.id),
        txt_media_id=desc.txt_media_id,
        txt_filename=txt_filename,
        txt_status=txt_status,
    )


def _require_folder_access(db: Session, user: User, folder_id: int) -> None:
    if db.get(UploadFolder, folder_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")
    if not user_can_access_folder(db, user, folder_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to folder")


def _get_description(db: Session, user: User, description_id: int) -> MediaDescription:
    desc = db.get(MediaDescription, description_id)
    if desc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Description not found")
    if not user_can_access_folder(db, user, desc.folder_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access")
    return desc


@router.get("/api/folders/{folder_id}/descriptions", response_model=list[DescriptionOut])
def list_descriptions(
    folder_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DescriptionOut]:
    _require_folder_access(db, user, folder_id)
    items = list(
        db.scalars(
            select(MediaDescription)
            .where(MediaDescription.folder_id == folder_id)
            .order_by(MediaDescription.created_at.desc())
        )
    )
    return [_to_out(db, d) for d in items]


@router.post(
    "/api/folders/{folder_id}/descriptions",
    response_model=DescriptionOut,
    status_code=status.HTTP_201_CREATED,
)
def add_description(
    folder_id: int,
    payload: DescriptionCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DescriptionOut:
    _require_folder_access(db, user, folder_id)
    desc = create_description(
        db,
        folder_id=folder_id,
        user=user,
        title=payload.title,
        body=payload.body,
        media_ids=payload.media_ids,
    )
    audit(db, user, "description.create", f"folder={folder_id} files={len(payload.media_ids)}")
    return _to_out(db, desc)


@router.patch("/api/descriptions/{description_id}", response_model=DescriptionOut)
def edit_description(
    description_id: int,
    payload: DescriptionUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DescriptionOut:
    desc = _get_description(db, user, description_id)
    desc = update_description(
        db,
        desc,
        title=payload.title,
        body=payload.body,
        media_ids=payload.media_ids,
    )
    return _to_out(db, desc)


@router.delete("/api/descriptions/{description_id}", response_model=DescriptionDeleteResult)
def remove_description(
    description_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DescriptionDeleteResult:
    desc = _get_description(db, user, description_id)
    folder_id = desc.folder_id
    result = delete_description(db, desc)
    audit(db, user, "description.delete", f"folder={folder_id} id={description_id}")
    return DescriptionDeleteResult(**result)
