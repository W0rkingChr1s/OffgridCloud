"""Startup integrity checks — surviving power loss on a battery bank.

The box is often run from an accumulator/power bank in the field, so it can lose
power at any instant — including mid-upload. Two failure modes matter and are
handled here on every boot, before the API starts accepting traffic:

  1. **Interrupted upload sessions.** A resumable upload streams chunks into a
     ``<uuid>.part`` file and only bumps ``UploadSession.received`` (in the DB)
     *after* the bytes are flushed to disk (see ``routers/uploads.py``). A crash
     can therefore leave the ``.part`` file with a torn/uncommitted tail that is
     longer than ``received``. We truncate every ``.part`` back to the last
     committed offset so the client resumes from a byte-exact, consistent point
     and the final SHA-256 covers only clean data. The rare reverse (file
     shorter than ``received`` — lost buffered writes) is corrected by rewinding
     ``received`` to what actually survived on disk.

  2. **Corrupted buffered media.** A media item whose local buffer copy was
     damaged (truncated) by the power cut must not be uploaded to a provider as
     if it were good. We size-check every still-pending local copy against the
     size recorded at completion and quarantine any mismatch (marked failed +
     local copy flagged gone) so it is neither served nor pushed to the cloud.

Both passes are best-effort and log a one-line summary; failures never block
startup — an admin must always be able to reach the UI to fix things.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .admin_ops import audit
from .db import SessionLocal
from .models import MediaItem, MediaStatus, UploadSession
from .storage import uploads_tmp_dir

logger = logging.getLogger("offgridcloud.integrity")

# Media states whose local buffer copy is still the authoritative data (it has
# not been confirmed at every provider yet), so its integrity still matters.
_PENDING_STATES = {
    MediaStatus.RECEIVED,
    MediaStatus.QUEUED,
    MediaStatus.UPLOADING,
    MediaStatus.FAILED,
}


def _file_size(path: str | os.PathLike[str]) -> int | None:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def reconcile_upload_sessions(db: Session) -> dict:
    """Make every in-progress upload byte-consistent again after a crash.

    Returns a summary ``{"truncated", "rewound", "missing", "orphans"}``.
    """
    truncated = 0  # torn tail discarded (on-disk longer than committed offset)
    rewound = 0  # committed offset rolled back to what survived on disk
    missing = 0  # .part vanished entirely — session reset to zero
    orphans = 0  # .part files with no owning session — removed

    sessions = db.scalars(select(UploadSession)).all()
    live_paths: set[str] = set()
    for session in sessions:
        path = Path(session.temp_path)
        live_paths.add(os.path.normpath(str(path)))
        actual = _file_size(path)

        if actual is None:
            # The partial file is gone (disk wiped / never flushed). Recreate an
            # empty one and reset so the client re-sends from the start.
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            except OSError as exc:  # pragma: no cover - unexpected FS error
                logger.warning("Could not recreate upload part %s: %s", path, exc)
            if session.received != 0:
                session.received = 0
                missing += 1
            continue

        if actual > session.received:
            # Crash after writing bytes but before committing the offset: the
            # tail may be a torn chunk. Drop back to the last committed byte.
            try:
                os.truncate(path, session.received)
                truncated += 1
                logger.info(
                    "Trimmed interrupted upload %s: %d -> %d bytes",
                    session.id, actual, session.received,
                )
            except OSError as exc:  # pragma: no cover
                logger.warning("Could not truncate upload part %s: %s", path, exc)
        elif actual < session.received:
            # Buffered writes were lost before hitting the platter. Trust the
            # disk, not the counter, so the resume offset is real.
            session.received = actual
            rewound += 1
            logger.info(
                "Rewound interrupted upload %s to surviving %d bytes",
                session.id, actual,
            )

    # Remove stray .part files whose session no longer exists (e.g. a crash
    # between finalising the DB and unlinking the temp file).
    try:
        for part in uploads_tmp_dir().glob("*.part"):
            if os.path.normpath(str(part)) not in live_paths:
                try:
                    part.unlink()
                    orphans += 1
                except OSError:  # pragma: no cover
                    pass
    except OSError:  # pragma: no cover - tmp dir unreadable
        pass

    db.commit()
    return {
        "truncated": truncated,
        "rewound": rewound,
        "missing": missing,
        "orphans": orphans,
    }


def verify_local_media(db: Session) -> dict:
    """Quarantine buffered media whose local copy was corrupted by a power cut.

    A cheap size check (not a full re-hash — the box may still be on battery)
    against the size recorded when the upload completed. A file that is missing
    or shorter than expected is truncated/corrupt: it is flagged so it is never
    served for download nor pushed to a provider as good data.

    Returns ``{"checked", "corrupt"}``.
    """
    checked = 0
    corrupt = 0
    items = db.scalars(
        select(MediaItem).where(MediaItem.local_deleted.is_(False))
    ).all()
    for media in items:
        if media.status not in _PENDING_STATES:
            continue  # already safe at a provider; local copy is disposable
        checked += 1
        actual = _file_size(media.stored_path)
        if actual is not None and actual == media.size:
            continue
        # Missing or wrong size → corrupt/incomplete. Quarantine it.
        media.status = MediaStatus.FAILED
        media.local_deleted = True  # stop uploads + block downloads of bad data
        corrupt += 1
        logger.warning(
            "Media %d (%s) failed integrity check: expected %d bytes, found %s",
            media.id, media.filename, media.size,
            "missing" if actual is None else actual,
        )
    if corrupt:
        db.commit()
    return {"checked": checked, "corrupt": corrupt}


def run_startup_checks() -> None:
    """Run every integrity pass at boot and record a single audit summary.

    Wrapped so a failure in one pass never prevents the app from starting.
    """
    with SessionLocal() as db:
        try:
            sess = reconcile_upload_sessions(db)
        except Exception:  # pragma: no cover - must not block startup
            logger.exception("Upload-session reconcile failed")
            sess = {"truncated": 0, "rewound": 0, "missing": 0, "orphans": 0}
        try:
            media = verify_local_media(db)
        except Exception:  # pragma: no cover
            logger.exception("Local media verification failed")
            media = {"checked": 0, "corrupt": 0}

    touched = (
        sess["truncated"] + sess["rewound"] + sess["missing"] + sess["orphans"]
        + media["corrupt"]
    )
    if touched == 0:
        logger.info("Integrity check: %d media verified, all consistent", media["checked"])
        return

    detail = (
        f"uploads trimmed={sess['truncated']} rewound={sess['rewound']} "
        f"reset={sess['missing']} orphans={sess['orphans']}; "
        f"media checked={media['checked']} quarantined={media['corrupt']}"
    )
    logger.warning("Integrity check recovered inconsistencies after restart: %s", detail)
    with SessionLocal() as db:
        audit(db, None, "system.integrity_recover", detail)
