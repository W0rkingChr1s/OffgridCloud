"""Keep provider notes ("no chunked upload, files get split") current.

A note is set when someone tests a provider, but targets change underneath:
Hetzner updates the Nextcloud behind a share link, an admin swaps the link.
The reconciler calls :func:`refresh_notes` on every pass; each provider whose
type has a ``test_note`` is asked again at most every NOTE_INTERVAL. When the
server can't be reached (the box is often offline) the old note stays.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .crypto import decrypt
from .models import CloudProvider
from .providers_registry import get_type

logger = logging.getLogger("offgridcloud.provider_health")

NOTE_INTERVAL = timedelta(hours=6)


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def refresh_notes(db: Session, now: datetime | None = None) -> int:
    """Re-check due providers; returns how many notes changed."""
    now = now or datetime.now(UTC).replace(tzinfo=None)
    changed = 0
    for provider in db.scalars(select(CloudProvider)).all():
        pt = get_type(provider.type)
        if pt is None or pt.test_note is None:
            continue
        checked = _naive_utc(provider.note_checked_at)
        if checked is not None and now - checked < NOTE_INTERVAL:
            continue
        try:
            config = json.loads(decrypt(provider.config_encrypted) or "{}")
            known, note = pt.test_note(config)
        except Exception:  # noqa: BLE001 - a broken config must not stop the loop
            logger.debug("Hinweis für Provider %s nicht prüfbar", provider.id, exc_info=True)
            continue
        if not known:
            continue  # offline or no answer: keep what we knew
        provider.note_checked_at = now
        if note != (provider.note or ""):
            provider.note = note
            changed += 1
            logger.info(
                "Provider %s: Hinweis %s", provider.name, "aktualisiert" if note else "entfernt"
            )
    db.commit()
    return changed
