"""Info-Service: fan a status event out to every configured channel.

Channels (all optional, all best-effort):
  * **Webhook**  — a JSON ``POST`` for machine automation (the original
    "Fertig-Webhook", now fired for more than just ``media.done``).
  * **Telegram** — a human-readable message via the Bot API.
  * **E-mail**   — the same message over SMTP.

Every send is wrapped so a broken channel can never disturb an upload or the
transfer worker: failures are logged and swallowed. Which events are allowed to
notify is gated per-event in :class:`SystemSettings`, so an admin can silence
the noisy ones (e.g. "Upload angenommen") without touching the channels.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .crypto import decrypt
from .models import SystemSettings

logger = logging.getLogger("offgridcloud.notify")

# Event key -> the SystemSettings flag that must be on for it to notify.
EVENT_TOGGLE: dict[str, str] = {
    "media.received": "notify_on_received",
    "media.done": "notify_on_done",
    "media.failed": "notify_on_failed",
    "disk.low": "notify_on_low_space",
    # Operational status announcements (see app.announce).
    "server.startup": "notify_on_startup",
    "server.online": "notify_on_reconnect",
    # Bandwidth-gate pause and resume share one toggle.
    "transfer.paused": "notify_on_bandwidth",
    "transfer.resumed": "notify_on_bandwidth",
}


@dataclass
class Senders:
    """Injectable transport seam so tests can capture sends without network."""

    webhook: Callable[[str, dict], None]
    telegram: Callable[[str, str, str], None]
    email: Callable[[SystemSettings, str, str], None]


# --- Transports -----------------------------------------------------------


def _send_webhook(url: str, payload: dict) -> None:
    import urllib.request

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    urllib.request.urlopen(req, timeout=10).close()  # noqa: S310 (admin-set URL)


def _send_telegram(token: str, chat_id: str, text: str) -> None:
    import urllib.parse
    import urllib.request

    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    req = urllib.request.Request(url, data=data, method="POST")
    urllib.request.urlopen(req, timeout=10).close()  # noqa: S310 (admin-set token)


def _send_email(settings: SystemSettings, subject: str, body: str) -> None:
    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from or settings.smtp_username
    msg["To"] = settings.smtp_to
    msg.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port or 587, timeout=15) as smtp:
        if settings.smtp_tls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, decrypt(settings.smtp_password_encrypted))
        smtp.send_message(msg)


_DEFAULT_SENDERS = Senders(webhook=_send_webhook, telegram=_send_telegram, email=_send_email)


# --- Dispatch -------------------------------------------------------------


def dispatch(
    settings: SystemSettings,
    event: str,
    title: str,
    message: str,
    payload: dict,
    *,
    senders: Senders = _DEFAULT_SENDERS,
) -> bool:
    """Notify all configured channels about ``event``.

    ``payload`` is the structured body for the webhook; ``title``/``message``
    are the human text for Telegram and e-mail. Returns ``True`` if the event
    is enabled and at least one channel was attempted (used by callers to set
    their dedup flag), ``False`` if the event is disabled entirely.
    """
    flag = EVENT_TOGGLE.get(event)
    if flag is not None and not getattr(settings, flag, False):
        return False

    text = f"{title}\n{message}" if message else title

    if settings.webhook_url:
        _guard("webhook", event, lambda: senders.webhook(settings.webhook_url, payload))

    token = decrypt(settings.telegram_bot_token_encrypted)
    if token and settings.telegram_chat_id:
        _guard(
            "telegram",
            event,
            lambda: senders.telegram(token, settings.telegram_chat_id, text),
        )

    if settings.smtp_host and settings.smtp_to:
        _guard("email", event, lambda: senders.email(settings, title, message or title))

    return True


def _guard(channel: str, event: str, fn: Callable[[], None]) -> None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - a channel failure must never propagate
        logger.warning("Notify via %s failed for %s: %s", channel, event, exc)


def notify_event(
    db: Session,
    event: str,
    title: str,
    message: str,
    payload: dict,
    *,
    senders: Senders = _DEFAULT_SENDERS,
) -> bool:
    """Convenience wrapper that loads settings and dispatches."""
    from .admin_ops import get_system_settings

    return dispatch(
        get_system_settings(db), event, title, message, payload, senders=senders
    )
