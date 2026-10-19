"""Interactive Telegram bot: connect greeting, bot profile and chat commands.

:mod:`app.notify` only *pushes* status messages. This module makes the same bot
talk back:

* **Connect greeting** — when an admin stores a bot token / chat ID, the box
  posts a short "OffgridCloud verbunden" message into that chat, so the setup is
  confirmed where it matters instead of only in the portal.
* **Bot profile** — command menu (``setMyCommands``), description texts and the
  chat's menu button are set up once per token, so the chat feels like an
  OffgridCloud console rather than a bare bot.
* **Commands** — a long-polling loop (``getUpdates``; no inbound port or public
  URL needed, which matters behind CGNAT / LTE) answers ``/status``,
  ``/speicher``, ``/queue`` & co.

Only the configured chat may run commands. Anyone else only gets their chat ID
back (handy during setup) and a "nicht freigeschaltet" note. Everything is
best-effort: an offline uplink, a revoked token or a Telegram hiccup is logged
and retried with back-off, never raised into the app.
"""

from __future__ import annotations

import html
import json
import logging
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import __version__
from .crypto import decrypt

logger = logging.getLogger("offgridcloud.telegram")

API_BASE = "https://api.telegram.org"
POLL_TIMEOUT = 20  # seconds Telegram holds a getUpdates long-poll open
IDLE_INTERVAL = 30.0  # no token configured -> re-check settings this often
ERROR_BACKOFF = 30.0  # network/server error -> wait before the next poll
AUTH_BACKOFF = 300.0  # 401: token revoked/wrong -> don't hammer the API
# Commands queued while the box was offline are still answered once it is back,
# but only if they are reasonably fresh — a "/retry" from yesterday is noise.
MAX_COMMAND_AGE = 15 * 60


class TelegramError(Exception):
    """A Bot API call failed. ``code`` is the HTTP/API error code, if known."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


# --- Bot API transport ----------------------------------------------------


def _api_call(token: str, method: str, params: dict | None = None, timeout: float = 15) -> object:
    import urllib.error
    import urllib.request

    data = json.dumps(params or {}).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}/bot{token}/{method}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            raise TelegramError(f"HTTP {exc.code}", exc.code) from exc
    if not body.get("ok"):
        raise TelegramError(
            body.get("description") or "Telegram-Fehler", body.get("error_code")
        )
    return body.get("result")


# Indirection so tests can swap the transport without touching the network.
api_call: Callable[..., object] = _api_call


def send_message(token: str, chat_id: str, text: str) -> None:
    api_call(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )


# --- Bot profile ----------------------------------------------------------


@dataclass(frozen=True)
class Command:
    name: str
    description: str
    # Shown in the command menu; aliases (/start, /help) stay hidden.
    menu: bool = True


COMMANDS: tuple[Command, ...] = (
    Command("status", "Kompakter Lagebericht der Box"),
    Command("speicher", "Freier Speicher im Puffer"),
    Command("queue", "Warteschlange und laufende Übertragungen"),
    Command("fehler", "Zuletzt fehlgeschlagene Übertragungen"),
    Command("retry", "Fehlgeschlagene Übertragungen neu anstoßen"),
    Command("netz", "Verbindung, IPs, VPN und Bandbreite"),
    Command("ping", "Lebenszeichen der Box"),
    Command("id", "Chat-ID dieses Chats anzeigen"),
    Command("hilfe", "Alle Befehle anzeigen"),
    Command("start", "Begrüßung", menu=False),
    Command("help", "Alle Befehle anzeigen", menu=False),
)

DESCRIPTION = (
    "OffgridCloud-Box: Upload-Puffer fürs Feld. Dieser Bot meldet Uploads, "
    "Übertragungen, Speicher- und Verbindungsstatus und beantwortet Befehle "
    "wie /status oder /queue."
)
SHORT_DESCRIPTION = "Status & Befehle deiner OffgridCloud-Box"


def setup_profile(token: str, chat_id: str = "") -> None:
    """Install the command menu and bot descriptions (idempotent).

    Each call is guarded on its own: a bot shared with other tools may refuse
    one tweak (e.g. the menu button in a channel) without losing the rest.
    """
    steps: list[tuple[str, dict]] = [
        (
            "setMyCommands",
            {
                "commands": [
                    {"command": c.name, "description": c.description}
                    for c in COMMANDS
                    if c.menu
                ]
            },
        ),
        ("setMyDescription", {"description": DESCRIPTION}),
        ("setMyShortDescription", {"short_description": SHORT_DESCRIPTION}),
    ]
    if chat_id:
        steps.append(
            ("setChatMenuButton", {"chat_id": chat_id, "menu_button": {"type": "commands"}})
        )
    for method, params in steps:
        try:
            api_call(token, method, params)
        except Exception as exc:  # noqa: BLE001 - cosmetic, never fatal
            logger.debug("Telegram %s failed: %s", method, exc)


def _box_name() -> str:
    try:
        return socket.gethostname() or "OffgridCloud"
    except OSError:
        return "OffgridCloud"


def connected_message() -> str:
    return (
        "✅ <b>OffgridCloud verbunden</b>\n"
        f"Box <b>{html.escape(_box_name())}</b> (v{html.escape(__version__)}) meldet "
        "sich ab jetzt in diesem Chat.\n\n"
        "📡 /status – Lagebericht\n"
        "📦 /queue – Warteschlange\n"
        "💾 /speicher – freier Platz\n"
        "❓ /hilfe – alle Befehle"
    )


def announce_connected(token: str, chat_id: str) -> None:
    """Set up the bot profile and greet the chat. Raises on send failure so a
    synchronous caller (the portal's test button) can show the error."""
    setup_profile(token, chat_id)
    send_message(token, chat_id, connected_message())


def announce_connected_async(token: str, chat_id: str) -> None:
    """Fire-and-forget variant for the settings save path: the uplink may be
    slow or down, and saving the form must never wait on Telegram."""

    def run() -> None:
        try:
            announce_connected(token, chat_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram connect greeting failed: %s", exc)

    threading.Thread(target=run, name="telegram-connect", daemon=True).start()


# --- Command handlers -----------------------------------------------------


def _fmt_bytes(num: int) -> str:
    from .announce import _format_bytes

    return _format_bytes(num)


def _cmd_help(db: Session) -> str:
    lines = ["🛰 <b>OffgridCloud – Befehle</b>"]
    lines += [f"/{c.name} – {html.escape(c.description)}" for c in COMMANDS if c.menu]
    return "\n".join(lines)


def _cmd_start(db: Session) -> str:
    return (
        f"👋 Moin! Hier spricht <b>{html.escape(_box_name())}</b>.\n\n" + _cmd_help(db)
    )


def _queue_counts(db: Session) -> dict:
    from .models import TransferJob, TransferStatus

    rows = db.execute(
        select(TransferJob.status, func.count(TransferJob.id)).group_by(TransferJob.status)
    ).all()
    counts = {s.value: 0 for s in TransferStatus}
    for status, count in rows:
        counts[status.value if hasattr(status, "value") else str(status)] = int(count)
    return counts


def _disk_line() -> str:
    from .admin_ops import disk_usage

    disk = disk_usage()
    icon = "🔴" if disk["low_space"] else "🟢"
    return (
        f"{icon} {_fmt_bytes(disk['free'])} frei von {_fmt_bytes(disk['total'])} "
        f"(belegt {disk['percent_used']:.0f}%)"
    )


def _cmd_status(db: Session) -> str:
    from .announce import _vpn_connected, format_kbps
    from .bandwidth import get_policy
    from .models import CloudProvider, ProviderStatus

    providers = list(db.scalars(select(CloudProvider).order_by(CloudProvider.name)))
    ok = sum(1 for p in providers if p.status == ProviderStatus.OK)
    counts = _queue_counts(db)
    lines = [
        f"📡 <b>Status {html.escape(_box_name())}</b>",
        f"💾 Speicher: {_disk_line()}",
        f"☁️ Cloud-Ziele: {ok}/{len(providers)} verbunden"
        if providers
        else "☁️ Cloud-Ziele: keine konfiguriert",
        f"📦 Warteschlange: {counts['queued']} wartend, {counts['running']} aktiv, "
        f"{counts['failed']} fehlgeschlagen",
        f"🚀 Bandbreite: {format_kbps(get_policy(db).last_kbps)}",
        f"🔐 VPN: {'verbunden' if _vpn_connected() else 'getrennt'}",
        f"🏷 Version: {html.escape(__version__)}",
    ]
    return "\n".join(lines)


def _cmd_speicher(db: Session) -> str:
    return f"💾 <b>Speicher</b>\n{_disk_line()}"


def _cmd_queue(db: Session) -> str:
    from .models import CloudProvider, MediaItem, TransferJob, TransferStatus
    from .transfers import get_live

    counts = _queue_counts(db)
    lines = [
        "📦 <b>Warteschlange</b>",
        f"⏳ wartend: {counts['queued']}",
        f"⬆️ aktiv: {counts['running']}",
        f"✅ fertig: {counts['done']}",
        f"❌ fehlgeschlagen: {counts['failed']}",
    ]
    running = db.execute(
        select(TransferJob, MediaItem.filename, CloudProvider.name)
        .join(MediaItem, MediaItem.id == TransferJob.media_id)
        .join(CloudProvider, CloudProvider.id == TransferJob.provider_id)
        .where(TransferJob.status == TransferStatus.RUNNING)
        .limit(5)
    ).all()
    if running:
        live = get_live()
        lines.append("")
        for job, filename, provider in running:
            info = live.get(job.id)
            pct = ""
            if info and info.get("total"):
                pct = f" – {info['bytes'] / info['total'] * 100:.0f}%"
            lines.append(f"• {html.escape(filename)} → {html.escape(provider)}{pct}")
    return "\n".join(lines)


def _cmd_fehler(db: Session) -> str:
    from .models import CloudProvider, MediaItem, TransferJob, TransferStatus

    rows = db.execute(
        select(TransferJob, MediaItem.filename, CloudProvider.name)
        .join(MediaItem, MediaItem.id == TransferJob.media_id)
        .join(CloudProvider, CloudProvider.id == TransferJob.provider_id)
        .where(TransferJob.status == TransferStatus.FAILED)
        .order_by(TransferJob.updated_at.desc())
        .limit(5)
    ).all()
    if not rows:
        return "✅ Keine fehlgeschlagenen Übertragungen."
    lines = ["❌ <b>Fehlgeschlagene Übertragungen</b>"]
    for job, filename, provider in rows:
        error = (job.last_error or "unbekannter Fehler").strip().splitlines()[0][:160]
        lines.append(
            f"• {html.escape(filename)} → {html.escape(provider)}\n"
            f"  <i>{html.escape(error)}</i>"
        )
    lines.append("\nMit /retry neu anstoßen.")
    return "\n".join(lines)


def _cmd_retry(db: Session) -> str:
    from .admin_ops import audit
    from .transfers import reconcile_once

    result = reconcile_once(db)
    audit(db, None, "telegram.retry", json.dumps(result))
    if not result["requeued"] and not result["backfilled"]:
        return "👌 Nichts zu tun – keine fehlgeschlagenen Übertragungen."
    return (
        f"🔁 {result['requeued']} Übertragung(en) neu eingereiht"
        + (f", {result['backfilled']} fehlende ergänzt" if result["backfilled"] else "")
        + "."
    )


def _cmd_netz(db: Session) -> str:
    from . import netinfo
    from .announce import _vpn_connected, format_kbps
    from .bandwidth import get_policy

    return "\n".join(
        [
            "🌐 <b>Netz</b>",
            f"Externe IP: {html.escape(netinfo.external_ip() or 'unbekannt')}",
            f"Interne IP: {html.escape(netinfo.internal_ip() or 'unbekannt')}",
            f"VPN: {'verbunden' if _vpn_connected() else 'getrennt'}",
            f"Bandbreite: {format_kbps(get_policy(db).last_kbps)}",
        ]
    )


def _cmd_ping(db: Session) -> str:
    return f"🏓 Pong! {html.escape(_box_name())} ist wach (v{html.escape(__version__)})."


HANDLERS: dict[str, Callable[[Session], str]] = {
    "start": _cmd_start,
    "hilfe": _cmd_help,
    "help": _cmd_help,
    "status": _cmd_status,
    "speicher": _cmd_speicher,
    "queue": _cmd_queue,
    "fehler": _cmd_fehler,
    "retry": _cmd_retry,
    "netz": _cmd_netz,
    "ping": _cmd_ping,
}


def parse_command(text: str) -> str | None:
    """``"/Status@MyBot extra"`` -> ``"status"``; non-commands -> ``None``."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return None
    word = text.split()[0][1:]
    return word.split("@", 1)[0].lower() or None


def handle_message(db: Session, message: dict, allowed_chat_id: str) -> str | None:
    """Return the reply text for one incoming message (``None`` = stay silent)."""
    command = parse_command(message.get("text", ""))
    if command is None:
        return None
    chat_id = str((message.get("chat") or {}).get("id", ""))
    if command == "id":
        return f"🆔 Chat-ID: <code>{html.escape(chat_id)}</code>"
    if not allowed_chat_id or chat_id != allowed_chat_id.strip():
        # Strangers learn nothing about the box — just how to get unlocked.
        return (
            "🔒 Dieser Chat ist nicht freigeschaltet.\n"
            f"Chat-ID: <code>{html.escape(chat_id)}</code> – im OffgridCloud-Portal "
            "unter System → Benachrichtigungen eintragen."
        )
    handler = HANDLERS.get(command)
    if handler is None:
        return f"🤷 Unbekannter Befehl /{html.escape(command)}. Siehe /hilfe."
    try:
        return handler(db)
    except Exception:  # noqa: BLE001 - one broken command must not kill the bot
        logger.exception("Telegram command /%s failed", command)
        return f"⚠️ /{html.escape(command)} ist gerade fehlgeschlagen."


# --- Polling loop ---------------------------------------------------------


class BotPoller:
    """Long-polls ``getUpdates`` and answers commands. Re-reads the token and
    chat ID from the settings each round, so portal changes apply live."""

    def __init__(self) -> None:
        self._offset: int | None = None
        self._token: str = ""

    def _credentials(self) -> tuple[str, str]:
        from .admin_ops import get_system_settings
        from .db import SessionLocal

        with SessionLocal() as db:
            row = get_system_settings(db)
            return decrypt(row.telegram_bot_token_encrypted), row.telegram_chat_id

    def poll_once(self, now: float | None = None) -> float:
        """One poll round. Returns how long to wait before the next one."""
        from .db import SessionLocal

        token, chat_id = self._credentials()
        if not token:
            self._token = ""
            return IDLE_INTERVAL
        if token != self._token:
            # New/changed bot: reset the cursor and (re)install the profile so
            # existing installs get the command menu after an update too.
            self._token, self._offset = token, None
            setup_profile(token, chat_id)

        params: dict = {"timeout": POLL_TIMEOUT, "allowed_updates": ["message"]}
        if self._offset is not None:
            params["offset"] = self._offset
        try:
            updates = api_call(token, "getUpdates", params, timeout=POLL_TIMEOUT + 10)
        except TelegramError as exc:
            if exc.code == 401:
                logger.warning("Telegram token rejected (401) — check the bot token")
                return AUTH_BACKOFF
            if exc.code == 409:
                # A webhook is set (or another poller runs): we can only poll
                # without one. Pending updates are kept.
                logger.info("Telegram webhook active — removing it to allow polling")
                try:
                    api_call(token, "deleteWebhook", {"drop_pending_updates": False})
                except Exception:  # noqa: BLE001
                    pass
            else:
                logger.debug("getUpdates failed: %s", exc)
            return ERROR_BACKOFF
        except Exception as exc:  # noqa: BLE001 - offline is the normal case here
            logger.debug("getUpdates failed: %s", exc)
            return ERROR_BACKOFF

        now = time.time() if now is None else now
        for update in updates or []:
            self._offset = int(update["update_id"]) + 1
            message = update.get("message")
            if not message or now - message.get("date", now) > MAX_COMMAND_AGE:
                continue
            with SessionLocal() as db:
                reply = handle_message(db, message, chat_id)
            if reply:
                try:
                    send_message(token, str(message["chat"]["id"]), reply)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Telegram reply failed: %s", exc)
        return 0.0


def run_bot(stop: threading.Event) -> None:
    """Thread target: poll until ``stop`` is set. Runs as a daemon thread so a
    long-poll in flight never delays shutdown."""
    poller = BotPoller()
    logger.info("Telegram bot poller started")
    while not stop.is_set():
        try:
            wait = poller.poll_once()
        except Exception:  # noqa: BLE001 - keep the poller alive
            logger.exception("Telegram poller error")
            wait = ERROR_BACKOFF
        if wait:
            stop.wait(wait)
    logger.info("Telegram bot poller stopped")
