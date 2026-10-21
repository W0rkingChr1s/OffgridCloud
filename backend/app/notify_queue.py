"""Outbox for notifications that could not be delivered right away.

The box lives in the field: the uplink drops for hours, and every Telegram,
e-mail or webhook sent meanwhile would simply be lost. Instead,
:func:`app.notify.dispatch` hands a failed send to :func:`enqueue`, which keeps
it in SQLite (survives a reboot or a dead battery). :func:`flush` delivers the
backlog once the channel answers again:

* **Telegram / e-mail** — one digest ("5 Meldungen aus der Offline-Zeit") with
  each entry's original time, instead of a burst of stale messages; a single
  entry goes out on its own, marked as delayed.
* **Webhook** — every entry separately (machines want one event per call),
  with ``queued_at`` and ``delayed: true`` added to the payload.

While a channel has a backlog, new messages for it join the queue instead of
being sent live: order is kept, and an offline box doesn't wait out a network
timeout on every event. :func:`watch_loop` retries with backoff and is kicked
immediately when the uplink comes back (:func:`kick`).

Permanent errors (HTTP 4xx like a revoked bot token, SMTP login refused) are
not queued — retrying can't fix them. Entries older than :data:`MAX_AGE` and
anything beyond :data:`MAX_ENTRIES` are dropped, oldest first.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import smtplib
import threading
import time
import urllib.error
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

logger = logging.getLogger("offgridcloud.notify_queue")

CHANNELS = ("webhook", "telegram", "email")
MAX_AGE = timedelta(days=7)
MAX_ENTRIES = 500  # per channel
WATCH_INTERVAL = 15.0
BACKOFF_MIN = 30.0
BACKOFF_MAX = 15 * 60.0
TELEGRAM_LIMIT = 3500  # Bot API caps a message at 4096 chars; leave room for markup

_lock = threading.Lock()
# One flush at a time: the watch loop and the reconnect path must not both
# pick up the same rows and send them twice.
_flush_lock = threading.Lock()
_next_try: dict[str, float] = {}  # channel -> monotonic time of the next attempt
_failures: dict[str, int] = {}


# --- Classification ---------------------------------------------------------------


def is_permanent(exc: BaseException) -> bool:
    """Would retrying later still fail? (Bad token/URL, login refused …)"""
    if isinstance(exc, urllib.error.HTTPError):
        return 400 <= exc.code < 500 and exc.code not in (408, 429)
    return isinstance(
        exc,
        smtplib.SMTPAuthenticationError
        | smtplib.SMTPRecipientsRefused
        | smtplib.SMTPSenderRefused
        | smtplib.SMTPNotSupportedError,
    )


# --- Queue ---------------------------------------------------------------------------


def _session():
    from .db import SessionLocal

    return SessionLocal()


def has_pending(channel: str) -> bool:
    from .models import NotificationOutbox

    with _session() as db:
        return bool(
            db.scalar(
                select(NotificationOutbox.id).where(NotificationOutbox.channel == channel).limit(1)
            )
        )


def enqueue(channel: str, event: str, title: str, message: str, payload: dict,
            error: str = "") -> None:
    from .models import NotificationOutbox

    try:
        with _session() as db:
            db.add(NotificationOutbox(
                channel=channel, event=event, title=title, message=message,
                payload=json.dumps(payload, default=str), last_error=error[:500],
            ))
            db.commit()
            _prune(db)
    except Exception:  # noqa: BLE001 - the queue is best-effort like every channel
        logger.exception("Could not queue %s notification for %s", channel, event)


def _prune(db: Session) -> None:
    from .models import NotificationOutbox

    cutoff = datetime.now(UTC) - MAX_AGE
    dropped = db.execute(
        delete(NotificationOutbox).where(NotificationOutbox.created_at < cutoff)
    ).rowcount or 0
    for channel in CHANNELS:
        ids = list(db.scalars(
            select(NotificationOutbox.id)
            .where(NotificationOutbox.channel == channel)
            .order_by(NotificationOutbox.id.desc())
            .offset(MAX_ENTRIES)
        ))
        if ids:
            db.execute(delete(NotificationOutbox).where(NotificationOutbox.id.in_(ids)))
            dropped += len(ids)
    db.commit()
    if dropped:
        logger.warning("Notification outbox: dropped %d old entr(ies)", dropped)


def counts() -> dict[str, int]:
    from .models import NotificationOutbox

    with _session() as db:
        rows = db.execute(
            select(NotificationOutbox.channel, func.count()).group_by(NotificationOutbox.channel)
        ).all()
    return {channel: n for channel, n in rows}


def clear() -> int:
    from .models import NotificationOutbox

    with _session() as db:
        n = db.execute(delete(NotificationOutbox)).rowcount or 0
        db.commit()
    with _lock:
        _next_try.clear()
        _failures.clear()
    return n


def kick() -> None:
    """Retry every channel on the next round (e.g. the uplink just came back)."""
    with _lock:
        _next_try.clear()


# --- Delivery ----------------------------------------------------------------------------


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _when(ts: datetime) -> str:
    """Original time in the box's local time zone, as the field team reads it."""
    return _aware(ts).astimezone().strftime("%d.%m. %H:%M")


def _configured(settings, channel: str) -> bool:
    from .crypto import decrypt

    if channel == "webhook":
        return bool(settings.webhook_url)
    if channel == "telegram":
        return bool(settings.telegram_chat_id and decrypt(settings.telegram_bot_token_encrypted))
    return bool(settings.smtp_host and settings.smtp_to)


def _telegram_chunks(rows) -> list[tuple[str, list]]:
    """Digest texts (each under the Bot API limit) with the rows each covers."""
    from .notify import TELEGRAM_ICONS, telegram_text

    if len(rows) == 1:
        row = rows[0]
        text = telegram_text(row.event, row.title, row.message)
        return [(f"{text}\n🕓 <i>verzögert — ursprünglich {_when(row.created_at)}</i>", rows)]
    chunks: list[tuple[str, list]] = []
    head = f"📬 <b>{len(rows)} Meldungen aus der Offline-Zeit</b>"
    text, covered = head, []
    for row in rows:
        icon = TELEGRAM_ICONS.get(row.event, "ℹ️")
        line = f"\n\n{icon} <b>{_when(row.created_at)} · {html.escape(row.title)}</b>"
        if row.message:
            line += f"\n{html.escape(row.message)}"
        line = line[:TELEGRAM_LIMIT - len(head) - 10]
        if covered and len(text) + len(line) > TELEGRAM_LIMIT:
            chunks.append((text, covered))
            text, covered = head + " (Forts.)", []
        text += line
        covered.append(row)
    chunks.append((text, covered))
    return chunks


def _email_text(rows) -> tuple[str, str]:
    if len(rows) == 1:
        row = rows[0]
        body = (row.message or row.title) + (
            f"\n\n(Ursprünglich {_when(row.created_at)}, wegen fehlender Verbindung "
            "verzögert zugestellt.)"
        )
        return f"{row.title} (verzögert)", body
    lines = [f"{len(rows)} Meldungen konnten wegen fehlender Verbindung nicht sofort "
             "zugestellt werden:", ""]
    for row in rows:
        lines.append(f"[{_when(row.created_at)}] {row.title}")
        if row.message:
            lines.append(f"    {row.message}")
    return f"OffgridCloud: {len(rows)} Meldungen aus der Offline-Zeit", "\n".join(lines)


def _send_channel(db: Session, settings, channel: str, rows, senders) -> int:
    """Deliver ``rows``; delete each as soon as it is out. Raises on failure."""
    from .crypto import decrypt

    sent = 0
    if channel == "webhook":
        for row in rows:
            payload = json.loads(row.payload or "{}")
            payload.update({"delayed": True, "queued_at": _aware(row.created_at).isoformat()})
            senders.webhook(settings.webhook_url, payload)
            db.delete(row)
            db.commit()
            sent += 1
    elif channel == "telegram":
        token = decrypt(settings.telegram_bot_token_encrypted)
        for text, covered in _telegram_chunks(rows):
            senders.telegram(token, settings.telegram_chat_id, text)
            for row in covered:
                db.delete(row)
            db.commit()
            sent += len(covered)
    else:
        subject, body = _email_text(rows)
        senders.email(settings, subject, body)
        for row in rows:
            db.delete(row)
        db.commit()
        sent += len(rows)
    return sent


def flush(*, senders=None, force: bool = False) -> int:
    """Deliver whatever is due. Returns the number of entries sent."""
    if not _flush_lock.acquire(blocking=False):
        return 0  # another flush is already on it
    try:
        return _flush(senders=senders, force=force)
    finally:
        _flush_lock.release()


def _flush(*, senders, force: bool) -> int:
    from .admin_ops import get_system_settings
    from .models import NotificationOutbox
    from .notify import _DEFAULT_SENDERS

    senders = senders or _DEFAULT_SENDERS
    sent = 0
    now = time.monotonic()
    with _session() as db:
        _prune(db)
        settings = get_system_settings(db)
        for channel in CHANNELS:
            with _lock:
                if not force and now < _next_try.get(channel, 0.0):
                    continue
            rows = list(db.scalars(
                select(NotificationOutbox)
                .where(NotificationOutbox.channel == channel)
                .order_by(NotificationOutbox.id)
            ))
            if not rows:
                continue
            if not _configured(settings, channel):
                # Channel switched off meanwhile: nobody to tell any more.
                for row in rows:
                    db.delete(row)
                db.commit()
                continue
            try:
                sent += _send_channel(db, settings, channel, rows, senders)
            except Exception as exc:  # noqa: BLE001 - keep the rest queued
                db.rollback()
                if is_permanent(exc):
                    logger.warning("Outbox %s: permanent error, dropping backlog: %s",
                                   channel, exc)
                    db.execute(delete(NotificationOutbox)
                               .where(NotificationOutbox.channel == channel))
                    db.commit()
                    continue
                _note_failure(db, channel, exc)
                continue
            with _lock:
                _failures.pop(channel, None)
                _next_try.pop(channel, None)
    if sent:
        logger.info("Outbox: delivered %d delayed notification(s)", sent)
    return sent


def _note_failure(db: Session, channel: str, exc: BaseException) -> None:
    from sqlalchemy import update

    from .models import NotificationOutbox

    with _lock:
        n = _failures.get(channel, 0) + 1
        _failures[channel] = n
        _next_try[channel] = time.monotonic() + min(BACKOFF_MIN * 2 ** (n - 1), BACKOFF_MAX)
    db.execute(
        update(NotificationOutbox)
        .where(NotificationOutbox.channel == channel)
        .values(attempts=NotificationOutbox.attempts + 1, last_error=str(exc)[:500])
    )
    db.commit()
    logger.debug("Outbox %s still unreachable (%s), retry #%d later", channel, exc, n)


async def watch_loop(stop: asyncio.Event) -> None:
    """Deliver the backlog once channels are reachable again."""
    while not stop.is_set():
        try:
            if await asyncio.to_thread(counts):
                await asyncio.to_thread(flush)
        except Exception:  # pragma: no cover - keep the loop alive
            logger.warning("Outbox round failed", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=WATCH_INTERVAL)
        except TimeoutError:
            pass
