"""Thematic descriptions and their generated ``.txt`` sidecars.

A *description* bundles a group of media items with a free-text explanation of
what they show. Besides being stored for the UI, every description is rendered
into a plain-text sidecar file that becomes its own :class:`MediaItem` — so the
explanation is uploaded to every linked cloud target through the normal transfer
pipeline, right next to the photos and videos it describes.

The sidecar's *filename* is fixed at creation time (from the title or a
timestamp) and never changes on edit, so re-generating its content overwrites
the same object in the cloud instead of orphaning the old one.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    MediaDescription,
    MediaDescriptionItem,
    MediaItem,
    MediaStatus,
    User,
)
from .storage import folder_dir, safe_filename

MAX_TITLE_LEN = 200
MAX_BODY_LEN = 20_000


def covered_media(db: Session, description_id: int) -> list[MediaItem]:
    """Media items a description covers, oldest first (upload order)."""
    return list(
        db.scalars(
            select(MediaItem)
            .join(MediaDescriptionItem, MediaDescriptionItem.media_id == MediaItem.id)
            .where(MediaDescriptionItem.description_id == description_id)
            .order_by(MediaItem.created_at)
        )
    )


def covered_media_ids(db: Session, description_id: int) -> list[int]:
    return list(
        db.scalars(
            select(MediaDescriptionItem.media_id)
            .where(MediaDescriptionItem.description_id == description_id)
            .order_by(MediaDescriptionItem.id)
        )
    )


def render_sidecar_text(
    *,
    title: str,
    body: str,
    filenames: list[str],
    author: str,
    created_at: datetime,
) -> str:
    """Render the human-readable ``.txt`` that ships to the cloud.

    Deliberately plain (no Markdown) so it stays readable in any viewer on any
    device the field team happens to open it with.
    """
    when = created_at.strftime("%Y-%m-%d %H:%M UTC")
    lines = ["OffgridCloud — Beschreibung", "===========================", ""]
    if title.strip():
        lines.append(f"Thema: {title.strip()}")
    lines.append(f"Erstellt: {when}")
    if author.strip():
        lines.append(f"Von: {author.strip()}")
    lines.append("")
    lines.append(body.strip())
    lines.append("")
    if filenames:
        lines.append(f"Zugehörige Dateien ({len(filenames)}):")
        lines.extend(f"- {name}" for name in filenames)
    else:
        lines.append("Zugehörige Dateien: keine")
    lines.append("")
    return "\n".join(lines)


def _sidecar_filename(title: str, created_at: datetime) -> str:
    """A stable, readable ``.txt`` name derived from the title (or a timestamp)."""
    stem = title.strip() or f"Beschreibung {created_at.strftime('%Y-%m-%d %H-%M')}"
    name = safe_filename(f"Beschreibung - {stem}")
    if not name.lower().endswith(".txt"):
        name = f"{name}.txt"
    return name[:500]


def _valid_media_ids(db: Session, folder_id: int, media_ids: list[int]) -> list[int]:
    """Keep only ids that are real media items in this folder (order-preserving)."""
    if not media_ids:
        return []
    wanted = list(dict.fromkeys(media_ids))  # de-dupe, keep order
    present = set(
        db.scalars(
            select(MediaItem.id).where(
                MediaItem.folder_id == folder_id, MediaItem.id.in_(wanted)
            )
        )
    )
    return [mid for mid in wanted if mid in present]


def _author_label(user: User | None) -> str:
    if user is None:
        return ""
    return f"{user.name} <{user.email}>".strip() if user.name else user.email


def _write_sidecar(path: str, text: str) -> tuple[int, str]:
    """Write the sidecar to disk (fsync'd) and return ``(size, sha256)``."""
    data = text.encode("utf-8")
    with open(path, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    return len(data), hashlib.sha256(data).hexdigest()


def create_description(
    db: Session,
    *,
    folder_id: int,
    user: User | None,
    title: str,
    body: str,
    media_ids: list[int],
) -> MediaDescription:
    """Create a description, generate its ``.txt`` sidecar and queue its upload.

    Commits its own work. The sidecar is created as a normal :class:`MediaItem`
    so :func:`app.transfers.enqueue_for_media` fans it out to every provider
    linked to the folder.
    """
    from .transfers import enqueue_for_media

    title = title.strip()[:MAX_TITLE_LEN]
    body = body.strip()[:MAX_BODY_LEN]
    now = datetime.now(UTC)
    ids = _valid_media_ids(db, folder_id, media_ids)

    desc = MediaDescription(
        folder_id=folder_id,
        title=title,
        body=body,
        created_by=user.id if user else None,
        created_at=now,
    )
    db.add(desc)
    db.flush()  # assign desc.id
    for mid in ids:
        db.add(MediaDescriptionItem(description_id=desc.id, media_id=mid))
    db.flush()

    filenames = [m.filename for m in covered_media(db, desc.id)]
    text = render_sidecar_text(
        title=title,
        body=body,
        filenames=filenames,
        author=_author_label(user),
        created_at=now,
    )

    stored_name = _sidecar_filename(title, now)
    final_path = folder_dir(folder_id) / f"{uuid.uuid4()}__{stored_name}"
    size, digest = _write_sidecar(str(final_path), text)

    sidecar = MediaItem(
        folder_id=folder_id,
        filename=stored_name,
        stored_path=str(final_path),
        size=size,
        sha256=digest,
        status=MediaStatus.RECEIVED,
        uploaded_by=user.id if user else None,
    )
    db.add(sidecar)
    db.flush()
    desc.txt_media_id = sidecar.id

    enqueue_for_media(db, sidecar)
    db.commit()
    db.refresh(desc)
    return desc


def regenerate_sidecar(db: Session, desc: MediaDescription) -> None:
    """Rewrite the sidecar's content in place and re-queue its upload.

    Keeps the same filename/stored path so the cloud copy is overwritten rather
    than duplicated. No-op (silently) if the sidecar file is gone.
    """
    from .transfers import enqueue_for_media, recompute_media_status

    if desc.txt_media_id is None:
        return
    sidecar = db.get(MediaItem, desc.txt_media_id)
    if sidecar is None:
        desc.txt_media_id = None
        db.commit()
        return

    author = _author_label(db.get(User, desc.created_by)) if desc.created_by else ""
    filenames = [m.filename for m in covered_media(db, desc.id)]
    text = render_sidecar_text(
        title=desc.title,
        body=desc.body,
        filenames=filenames,
        author=author,
        created_at=desc.created_at,
    )
    size, digest = _write_sidecar(sidecar.stored_path, text)
    sidecar.size = size
    sidecar.sha256 = digest
    sidecar.local_deleted = False
    # Re-queue every transfer so the refreshed text re-uploads; re-arm notices.
    from .models import TransferJob, TransferStatus

    jobs = list(
        db.scalars(select(TransferJob).where(TransferJob.media_id == sidecar.id))
    )
    for job in jobs:
        job.status = TransferStatus.QUEUED
        job.attempts = 0
        job.last_error = ""
        job.next_attempt_at = None
        job.progress = 0.0
    sidecar.notified = False
    sidecar.notified_failed = False
    db.flush()
    enqueue_for_media(db, sidecar)  # backfill jobs for any newly-linked provider
    recompute_media_status(db, sidecar.id)
    db.commit()


def update_description(
    db: Session,
    desc: MediaDescription,
    *,
    title: str | None,
    body: str | None,
    media_ids: list[int] | None,
) -> MediaDescription:
    """Patch a description (title/body/covered items) and refresh its sidecar."""
    if title is not None:
        desc.title = title.strip()[:MAX_TITLE_LEN]
    if body is not None:
        desc.body = body.strip()[:MAX_BODY_LEN]
    if media_ids is not None:
        ids = _valid_media_ids(db, desc.folder_id, media_ids)
        db.query(MediaDescriptionItem).filter(
            MediaDescriptionItem.description_id == desc.id
        ).delete()
        for mid in ids:
            db.add(MediaDescriptionItem(description_id=desc.id, media_id=mid))
    db.flush()
    regenerate_sidecar(db, desc)
    db.refresh(desc)
    return desc


def delete_description(db: Session, desc: MediaDescription) -> dict:
    """Delete a description and its generated sidecar (respecting remote-delete).

    Returns the sidecar's :func:`app.transfers.delete_media` summary (or a
    ``deleted=False`` stub when there was no sidecar) so the caller can surface
    any provider that refused the remote deletion.
    """
    from .transfers import delete_media

    result = {"deleted": False, "remote_attempted": 0, "remote_deleted": 0, "remote_errors": []}
    txt_media_id = desc.txt_media_id
    db.delete(desc)  # cascades the description-item links
    db.commit()
    if txt_media_id is not None:
        result = delete_media(db, txt_media_id)
    return result
