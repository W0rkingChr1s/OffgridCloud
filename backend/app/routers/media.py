"""Media endpoints: thumbnails/downloads (token via query), tags and search."""

from __future__ import annotations

import os

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..deps import get_current_user
from ..models import MediaItem, MediaStatus, MediaTag, Role, UploadFolder, User
from ..schemas import MediaSearchOut, TagsUpdate
from ..security import decode_access_token
from ..storage import accessible_folder_ids, user_can_access_folder
from ..tagging import all_tags, normalise_tags, set_tags, tags_for
from ..thumbnails import get_or_create_thumb

router = APIRouter(prefix="/api/media", tags=["media"])


def _media_for_access(db: Session, user: User, media_id: int) -> MediaItem:
    media = db.get(MediaItem, media_id)
    if media is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")
    if not user_can_access_folder(db, user, media.folder_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access")
    return media


def _user_from_token(db: Session, token: str) -> User:
    try:
        payload = decode_access_token(token)
        user = db.get(User, int(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        user = None
    if user is None or not user.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return user


@router.get("/{media_id}/thumbnail")
def thumbnail(media_id: int, token: str) -> FileResponse:
    with SessionLocal() as db:
        user = _user_from_token(db, token)
        media = db.get(MediaItem, media_id)
        if media is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        if not user_can_access_folder(db, user, media.folder_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access")
        if media.local_deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Local copy removed")

        thumb = get_or_create_thumb(media.id, media.stored_path, media.filename)
        if thumb is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No thumbnail")
        return FileResponse(thumb, media_type="image/jpeg")


@router.get("/{media_id}/download")
def download(media_id: int, token: str) -> FileResponse:
    """Stream the original file as an attachment.

    The token is taken from the query string (not a header) so it can be used
    directly in a browser download link / ``<a href>``, mirroring the thumbnail
    endpoint. Access is scoped to folders the user may see.
    """
    with SessionLocal() as db:
        user = _user_from_token(db, token)
        media = db.get(MediaItem, media_id)
        if media is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        if not user_can_access_folder(db, user, media.folder_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access")
        if media.local_deleted:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Lokale Kopie wurde entfernt — Download nicht möglich",
            )
        if not os.path.exists(media.stored_path):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Datei fehlt")
        return FileResponse(
            media.stored_path,
            media_type="application/octet-stream",
            filename=media.filename,
        )


# --- Tags -----------------------------------------------------------------


@router.get("/tags", response_model=list[str])
def list_all_tags(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[str]:
    """Distinct tags across media the caller can see — for filter dropdowns."""
    if user.role == Role.ADMIN:
        return all_tags(db)
    folder_ids = accessible_folder_ids(db, user)
    if not folder_ids:
        return []
    media_ids = list(
        db.scalars(select(MediaItem.id).where(MediaItem.folder_id.in_(folder_ids)))
    )
    return all_tags(db, media_ids)


@router.get("/{media_id}/tags", response_model=list[str])
def get_tags(
    media_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[str]:
    _media_for_access(db, user, media_id)
    return tags_for(db, [media_id]).get(media_id, [])


@router.put("/{media_id}/tags", response_model=list[str])
def put_tags(
    media_id: int,
    payload: TagsUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[str]:
    """Replace a media item's full tag set. Scoped to folder access (like upload)."""
    _media_for_access(db, user, media_id)
    return set_tags(db, media_id, payload.tags)


# --- Search ---------------------------------------------------------------


@router.get("/search", response_model=list[MediaSearchOut])
def search_media(
    q: str = Query(default="", description="Substring match on filename"),
    tag: str = Query(default="", description="Filter to items carrying this tag"),
    status_filter: MediaStatus | None = Query(default=None, alias="status"),
    folder_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MediaSearchOut]:
    """Search/filter media across every folder the caller may access.

    Combines a filename substring, an exact tag, a status and a folder filter.
    Admins search everything; users are scoped to their accessible folders.
    """
    stmt = select(MediaItem)

    if user.role != Role.ADMIN:
        folder_ids = accessible_folder_ids(db, user)
        if not folder_ids:
            return []
        stmt = stmt.where(MediaItem.folder_id.in_(folder_ids))

    if folder_id is not None:
        stmt = stmt.where(MediaItem.folder_id == folder_id)
    if status_filter is not None:
        stmt = stmt.where(MediaItem.status == status_filter)
    if q.strip():
        stmt = stmt.where(MediaItem.filename.ilike(f"%{q.strip()}%"))
    if tag.strip():
        normalised = normalise_tags([tag])
        if normalised:
            stmt = stmt.where(
                MediaItem.id.in_(
                    select(MediaTag.media_id).where(MediaTag.tag == normalised[0])
                )
            )

    stmt = stmt.order_by(MediaItem.created_at.desc()).limit(limit).offset(offset)
    items = list(db.scalars(stmt))

    tag_map = tags_for(db, [m.id for m in items])
    folder_names = dict(db.execute(select(UploadFolder.id, UploadFolder.name)).all())
    return [
        MediaSearchOut(
            id=m.id,
            folder_id=m.folder_id,
            filename=m.filename,
            size=m.size,
            sha256=m.sha256,
            status=m.status,
            local_deleted=m.local_deleted,
            uploaded_by=m.uploaded_by,
            created_at=m.created_at,
            tags=tag_map.get(m.id, []),
            folder_name=folder_names.get(m.folder_id, ""),
        )
        for m in items
    ]
