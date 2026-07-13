"""Media tags: normalisation and batch lookup helpers.

Tags are free-form labels attached to media items so the field team can find
material later (e.g. ``interview``, ``drone``, ``eilig``). They are lower-cased,
trimmed, de-duplicated and capped so a bad client can't bloat the table.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import MediaTag

MAX_TAG_LEN = 64
MAX_TAGS_PER_ITEM = 50


def normalise_tags(tags: list[str]) -> list[str]:
    """Trim, lower-case, de-duplicate (order-preserving) and cap a tag list."""
    seen: list[str] = []
    for raw in tags:
        tag = raw.strip().lower()[:MAX_TAG_LEN]
        if tag and tag not in seen:
            seen.append(tag)
    return seen[:MAX_TAGS_PER_ITEM]


def tags_for(db: Session, media_ids: list[int]) -> dict[int, list[str]]:
    """Return ``{media_id: [tag, ...]}`` for the given ids (sorted tags)."""
    if not media_ids:
        return {}
    rows = db.execute(
        select(MediaTag.media_id, MediaTag.tag)
        .where(MediaTag.media_id.in_(media_ids))
        .order_by(MediaTag.tag)
    ).all()
    out: dict[int, list[str]] = {}
    for media_id, tag in rows:
        out.setdefault(media_id, []).append(tag)
    return out


def set_tags(db: Session, media_id: int, tags: list[str]) -> list[str]:
    """Replace the full tag set of a media item. Returns the stored tags."""
    normalised = normalise_tags(tags)
    db.query(MediaTag).filter(MediaTag.media_id == media_id).delete()
    for tag in normalised:
        db.add(MediaTag(media_id=media_id, tag=tag))
    db.commit()
    return normalised


def all_tags(db: Session, media_ids: list[int] | None = None) -> list[str]:
    """Distinct tags (sorted), optionally restricted to a set of media ids."""
    stmt = select(MediaTag.tag).distinct().order_by(MediaTag.tag)
    if media_ids is not None:
        if not media_ids:
            return []
        stmt = stmt.where(MediaTag.media_id.in_(media_ids))
    return list(db.scalars(stmt))
