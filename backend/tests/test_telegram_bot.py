"""Interactive Telegram bot: greeting, command parsing/auth and polling."""

import time

import pytest

from app import telegram_bot
from app.db import SessionLocal


@pytest.fixture
def calls(monkeypatch):
    """Record Bot API calls; ``getUpdates`` answers from ``calls.updates``."""

    class Recorder(list):
        updates: list = []

    rec = Recorder()

    def fake(token, method, params=None, timeout=15):
        rec.append((token, method, params or {}))
        if method == "getUpdates":
            out, rec.updates = rec.updates, []
            return out
        return True

    monkeypatch.setattr(telegram_bot, "api_call", fake)
    return rec


def _sent(calls):
    return [p for _, m, p in calls if m == "sendMessage"]


def _msg(text, chat_id=42, age=0):
    return {"text": text, "chat": {"id": chat_id}, "date": int(time.time()) - age}


def test_parse_command():
    assert telegram_bot.parse_command("/Status@OffgridBot extra") == "status"
    assert telegram_bot.parse_command("  /queue") == "queue"
    assert telegram_bot.parse_command("hallo") is None
    assert telegram_bot.parse_command("") is None


def test_announce_connected_sets_profile_and_greets(calls):
    telegram_bot.announce_connected("tok", "42")
    methods = [m for _, m, _ in calls]
    assert "setMyCommands" in methods
    assert "setMyDescription" in methods
    assert "setChatMenuButton" in methods
    sent = _sent(calls)
    assert len(sent) == 1
    assert sent[0]["chat_id"] == "42"
    assert "OffgridCloud verbunden" in sent[0]["text"]
    assert sent[0]["parse_mode"] == "HTML"
    menu = next(p for _, m, p in calls if m == "setMyCommands")["commands"]
    names = {c["command"] for c in menu}
    assert {"status", "queue", "speicher", "retry"} <= names
    assert "start" not in names  # alias stays out of the menu


def test_unknown_chat_is_refused_but_learns_its_id(client):
    with SessionLocal() as db:
        reply = telegram_bot.handle_message(db, _msg("/status", chat_id=999), "42")
    assert "nicht freigeschaltet" in reply
    assert "999" in reply
    assert "Speicher" not in reply


def test_id_works_for_anyone(client):
    with SessionLocal() as db:
        reply = telegram_bot.handle_message(db, _msg("/id", chat_id=7), "")
    assert "7" in reply


def test_commands_answer_for_configured_chat(client):
    with SessionLocal() as db:
        for cmd, needle in [
            ("/status", "Status"),
            ("/speicher", "frei von"),
            ("/queue", "Warteschlange"),
            ("/fehler", "fehlgeschlagen"),
            ("/ping", "Pong"),
            ("/hilfe", "/status"),
            ("/start", "Moin"),
            ("/gibtsnicht", "Unbekannter Befehl"),
        ]:
            reply = telegram_bot.handle_message(db, _msg(cmd), "42")
            assert needle.lower() in reply.lower(), (cmd, reply)
        assert telegram_bot.handle_message(db, _msg("kein befehl"), "42") is None


def test_retry_requeues_via_reconcile(client, monkeypatch):
    from app import transfers

    monkeypatch.setattr(
        transfers, "reconcile_once", lambda db: {"backfilled": 0, "requeued": 3}
    )
    with SessionLocal() as db:
        reply = telegram_bot.handle_message(db, _msg("/retry"), "42")
    assert "3" in reply


def test_poller_answers_fresh_commands_and_skips_stale(client, admin_auth, calls):
    client.put(
        "/api/system",
        headers=admin_auth,
        json={"telegram_bot_token": "tok", "telegram_chat_id": "42"},
    )
    try:
        poller = telegram_bot.BotPoller()
        calls.updates = [
            {"update_id": 10, "message": _msg("/ping")},
            {"update_id": 11, "message": _msg("/ping", age=telegram_bot.MAX_COMMAND_AGE + 60)},
        ]
        assert poller.poll_once() == 0.0
        replies = [p for p in _sent(calls) if "Pong" in p["text"]]
        assert len(replies) == 1
        # First round installed the profile; the cursor advanced past both.
        assert any(m == "setMyCommands" for _, m, _ in calls)
        poller.poll_once()
        last_poll = [p for _, m, p in calls if m == "getUpdates"][-1]
        assert last_poll["offset"] == 12
    finally:
        client.put(
            "/api/system",
            headers=admin_auth,
            json={"telegram_bot_token": "", "telegram_chat_id": ""},
        )


def test_poller_idles_without_token(client):
    assert telegram_bot.BotPoller().poll_once() == telegram_bot.IDLE_INTERVAL


def test_saving_bot_greets_chat(client, admin_auth, monkeypatch):
    greeted = []
    monkeypatch.setattr(
        telegram_bot, "announce_connected_async", lambda t, c: greeted.append((t, c))
    )
    try:
        r = client.put(
            "/api/system",
            headers=admin_auth,
            json={"telegram_bot_token": "tok", "telegram_chat_id": "42"},
        )
        assert r.status_code == 200
        assert greeted == [("tok", "42")]
        # Unrelated saves don't re-greet.
        client.put("/api/system", headers=admin_auth, json={"auto_resync": True})
        assert len(greeted) == 1
    finally:
        client.put(
            "/api/system",
            headers=admin_auth,
            json={"telegram_bot_token": "", "telegram_chat_id": ""},
        )


def test_test_endpoint_reports_result(client, admin_auth, calls):
    r = client.post("/api/system/telegram/test", headers=admin_auth)
    assert r.status_code == 409
    client.put(
        "/api/system",
        headers=admin_auth,
        json={"telegram_bot_token": "tok", "telegram_chat_id": "42"},
    )
    try:
        r = client.post("/api/system/telegram/test", headers=admin_auth)
        assert r.status_code == 200
        assert r.json()["sent"] is True
        assert any("verbunden" in p["text"] for p in _sent(calls))
    finally:
        client.put(
            "/api/system",
            headers=admin_auth,
            json={"telegram_bot_token": "", "telegram_chat_id": ""},
        )


def test_notify_telegram_text_is_formatted_and_escaped():
    from app.notify import telegram_text

    text = telegram_text("media.done", "Upload <fertig>", "a & b")
    assert text.startswith("✅ <b>Upload &lt;fertig&gt;</b>")
    assert "a &amp; b" in text
