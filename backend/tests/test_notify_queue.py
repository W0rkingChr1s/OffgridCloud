"""Outbox: notifications for an unreachable channel are delivered later."""

from __future__ import annotations

import json
import urllib.error
from datetime import UTC, datetime, timedelta

import pytest

from app import notify_queue
from app.crypto import encrypt
from app.db import SessionLocal
from app.models import NotificationOutbox, SystemSettings
from app.notify import Senders, dispatch


class Net:
    """Fake channels that can be switched offline."""

    def __init__(self):
        self.online = False
        self.webhook, self.telegram, self.email = [], [], []

    def _check(self):
        if not self.online:
            raise OSError("Network is unreachable")

    def senders(self) -> Senders:
        def webhook(url, payload):
            self._check()
            self.webhook.append(payload)

        def telegram(token, chat, text):
            self._check()
            self.telegram.append(text)

        def email(settings, subject, body):
            self._check()
            self.email.append((subject, body))

        return Senders(webhook=webhook, telegram=telegram, email=email)


@pytest.fixture
def net(client, monkeypatch):
    """All three channels configured on the stored settings row."""
    with SessionLocal() as db:
        row = db.get(SystemSettings, 1)
        before = (row.webhook_url, row.telegram_bot_token_encrypted, row.telegram_chat_id,
                  row.smtp_host, row.smtp_to, row.notify_on_done)
        row.webhook_url = "http://hook.local/x"
        row.telegram_bot_token_encrypted = encrypt("tok")
        row.telegram_chat_id = "42"
        row.smtp_host, row.smtp_to = "smtp.local", "ops@field.local"
        row.notify_on_done = True
        db.commit()
    notify_queue.clear()
    global _net
    n = _net = Net()
    # flush() looks the default senders up at call time.
    monkeypatch.setattr("app.notify._DEFAULT_SENDERS", n.senders())
    yield n
    with SessionLocal() as db:
        row = db.get(SystemSettings, 1)
        (row.webhook_url, row.telegram_bot_token_encrypted, row.telegram_chat_id,
         row.smtp_host, row.smtp_to, row.notify_on_done) = before
        db.commit()


_net: Net | None = None


def _send(title: str, message: str = "m") -> None:
    with SessionLocal() as db:
        settings = db.get(SystemSettings, 1)
        dispatch(settings, "media.done", title, message, {"event": "media.done", "t": title},
                 senders=_net.senders())


def test_offline_messages_wait_and_arrive_as_digest(net):
    _send("Eins")
    _send("Zwei")
    assert notify_queue.counts() == {"webhook": 2, "telegram": 2, "email": 2}

    # Still offline: the retry fails and keeps everything.
    assert notify_queue.flush(force=True) == 0
    assert sum(notify_queue.counts().values()) == 6

    net.online = True
    assert notify_queue.flush(force=True) == 6
    assert notify_queue.counts() == {}
    # Telegram/e-mail: one digest with both, in order.
    assert len(net.telegram) == 1 and "2 Meldungen aus der Offline-Zeit" in net.telegram[0]
    assert net.telegram[0].index("Eins") < net.telegram[0].index("Zwei")
    assert len(net.email) == 1 and "2 Meldungen" in net.email[0][0]
    # Webhook: one call per event, marked as delayed.
    assert [p["t"] for p in net.webhook] == ["Eins", "Zwei"]
    assert all(p["delayed"] is True and p["queued_at"] for p in net.webhook)


def test_backlog_keeps_order_for_new_messages(net):
    _send("Alt")
    net.online = True
    _send("Neu")  # channel has a backlog: queued behind "Alt", not sent first
    assert net.telegram == [] and notify_queue.counts()["telegram"] == 2
    notify_queue.flush(force=True)
    assert net.telegram[0].index("Alt") < net.telegram[0].index("Neu")


def test_single_delayed_message_is_marked(net):
    _send("Einzeln", "Karte fertig")
    net.online = True
    notify_queue.flush(force=True)
    assert "Karte fertig" in net.telegram[0] and "verzögert" in net.telegram[0]
    assert net.email[0][0] == "Einzeln (verzögert)"


def test_online_sends_live_without_queue(net):
    net.online = True
    _send("Live")
    assert len(net.telegram) == 1 and notify_queue.counts() == {}


def test_permanent_error_is_not_queued(net, monkeypatch):
    def forbidden(token, chat, text):
        raise urllib.error.HTTPError("https://api.telegram.org", 401, "Unauthorized", {}, None)

    s = net.senders()
    with SessionLocal() as db:
        settings = db.get(SystemSettings, 1)
        net.online = True
        dispatch(settings, "media.done", "t", "m", {},
                 senders=Senders(webhook=s.webhook, telegram=forbidden, email=s.email))
    assert notify_queue.counts() == {}


def test_backoff_skips_until_due_and_kick_resets(net):
    _send("x")
    notify_queue.flush()  # fails -> backoff
    net.online = True
    assert notify_queue.flush() == 0  # not due yet
    notify_queue.kick()
    assert notify_queue.flush() == 3


def test_old_entries_are_dropped(net):
    with SessionLocal() as db:
        db.add(NotificationOutbox(channel="telegram", event="media.done", title="uralt",
                                  payload=json.dumps({}),
                                  created_at=datetime.now(UTC) - timedelta(days=30)))
        db.commit()
    net.online = True
    assert notify_queue.flush(force=True) == 0
    assert net.telegram == []


def test_unconfigured_channel_drops_backlog(net):
    _send("x")
    with SessionLocal() as db:
        db.get(SystemSettings, 1).telegram_chat_id = ""
        db.commit()
    net.online = True
    notify_queue.flush(force=True)
    assert net.telegram == [] and notify_queue.counts() == {}


def test_long_backlog_is_split_for_telegram(net):
    for i in range(60):
        _send(f"Meldung {i}", "x" * 200)
    net.online = True
    notify_queue.flush(force=True)
    assert len(net.telegram) > 1
    assert all(len(t) <= notify_queue.TELEGRAM_LIMIT for t in net.telegram)
    assert sum(t.count("Meldung ") for t in net.telegram) == 60


def test_api_shows_flushes_and_clears(client, admin_auth, net):
    _send("x")
    status = client.get("/api/system", headers=admin_auth).json()
    assert status["notify_queued"] == {"webhook": 1, "telegram": 1, "email": 1}
    r = client.post("/api/system/notify-queue/flush", headers=admin_auth)
    assert r.json()["notify_queued"]["telegram"] == 1  # still offline
    r = client.delete("/api/system/notify-queue", headers=admin_auth)
    assert r.json()["notify_queued"] == {}


def test_concurrent_flush_is_skipped(net):
    _send("x")
    net.online = True
    with notify_queue._flush_lock:
        assert notify_queue.flush(force=True) == 0
    assert notify_queue.flush(force=True) == 3
