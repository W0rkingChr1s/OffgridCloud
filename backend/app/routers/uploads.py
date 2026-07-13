"""Resumable, chunked uploads.

Flow:
  1. POST /api/folders/{id}/uploads      -> open a session (returns upload_id)
  2. PUT  /api/uploads/{upload_id}       -> append a chunk at header X-Offset
  3. POST /api/uploads/{upload_id}/complete -> hash, finalize, create MediaItem
  (GET for resume status, DELETE to abort.)

Chunks are streamed straight to disk — the whole media file is never held in
RAM, which keeps large video uploads safe on a 1 GB Raspberry Pi.
"""

from __future__ import annotations

import hashlib
import os
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import MediaItem, MediaStatus, Role, UploadFolder, UploadSession, User
from ..schemas import MediaItemOut, UploadCreate, UploadSessionOut
from ..storage import folder_dir, safe_filename, uploads_tmp_dir, user_can_access_folder

router = APIRouter(tags=["uploads"])

CHUNK_READ = 1024 * 1024  # 1 MiB read granularity when streaming to disk


def _get_session(db: Session, upload_id: str, user: User) -> UploadSession:
    session = db.get(UploadSession, upload_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")
    # Only the uploader (or an admin) may touch the session.
    if user.role != Role.ADMIN and session.created_by != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your upload")
    return session


@router.post(
    "/api/folders/{folder_id}/uploads",
    response_model=UploadSessionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_upload(
    folder_id: int,
    payload: UploadCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UploadSession:
    if db.get(UploadFolder, folder_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")
    if not user_can_access_folder(db, user, folder_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to folder")

    upload_id = str(uuid.uuid4())
    temp_path = uploads_tmp_dir() / f"{upload_id}.part"
    temp_path.touch()

    session = UploadSession(
        id=upload_id,
        folder_id=folder_id,
        filename=safe_filename(payload.filename),
        temp_path=str(temp_path),
        size=payload.size,
        received=0,
        created_by=user.id,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.get("/api/uploads/{upload_id}", response_model=UploadSessionOut)
def upload_status(
    upload_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UploadSession:
    return _get_session(db, upload_id, user)


@router.put("/api/uploads/{upload_id}", response_model=UploadSessionOut)
async def upload_chunk(
    upload_id: str,
    request: Request,
    x_offset: int = Header(default=0, alias="X-Offset"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UploadSession:
    session = _get_session(db, upload_id, user)

    # Resumable: the client must append at the current end of the file. If the
    # offsets disagree, report the authoritative position so it can resync.
    if x_offset != session.received:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Offset mismatch; expected {session.received}",
            headers={"X-Received": str(session.received)},
        )

    written = 0
    with open(session.temp_path, "ab") as fh:
        async for chunk in request.stream():
            if chunk:
                fh.write(chunk)
                written += len(chunk)
        # Force the bytes to the platter BEFORE we advance the committed offset.
        # On a battery-powered Pi this ordering is what makes the resume point
        # trustworthy after a power cut: the DB never claims bytes the disk lost
        # (see integrity.reconcile_upload_sessions).
        fh.flush()
        os.fsync(fh.fileno())

    session.received += written
    db.commit()
    db.refresh(session)
    return session


@router.post("/api/uploads/{upload_id}/complete", response_model=MediaItemOut)
def complete_upload(
    upload_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MediaItem:
    session = _get_session(db, upload_id, user)

    if session.size and session.received != session.size:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Incomplete: received {session.received} of {session.size} bytes",
        )

    # Hash by streaming the temp file (constant memory).
    digest = hashlib.sha256()
    with open(session.temp_path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK_READ), b""):
            digest.update(block)

    final_name = f"{upload_id}__{session.filename}"
    final_path = folder_dir(session.folder_id) / final_name
    os.replace(session.temp_path, final_path)

    media = MediaItem(
        folder_id=session.folder_id,
        filename=session.filename,
        stored_path=str(final_path),
        size=session.received,
        sha256=digest.hexdigest(),
        status=MediaStatus.RECEIVED,
        uploaded_by=user.id,
    )
    db.add(media)
    db.delete(session)
    db.commit()
    db.refresh(media)

    # Queue transfers to every provider linked to this folder.
    from ..transfers import enqueue_for_media

    enqueue_for_media(db, media)
    db.commit()
    db.refresh(media)
    return media


@router.delete(
    "/api/uploads/{upload_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def abort_upload(
    upload_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    session = _get_session(db, upload_id, user)
    try:
        os.unlink(session.temp_path)
    except FileNotFoundError:
        pass
    db.delete(session)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
