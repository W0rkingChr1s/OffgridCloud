"""Media endpoints: thumbnail serving (token via query for <img> usage)."""

from __future__ import annotations

import jwt
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import MediaItem, User
from ..security import decode_access_token
from ..storage import user_can_access_folder
from ..thumbnails import get_or_create_thumb

router = APIRouter(prefix="/api/media", tags=["media"])


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
