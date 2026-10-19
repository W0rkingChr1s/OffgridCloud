"""Interactive Telegram bot: greeting, command parsing/auth and polling."""

import time

import pytest

from app import telegram_bot
from app.db import SessionLocal


@pytest.fixture(autouse=True)
def _fresh_bot_state():
    telegram_bot.reset_state()
    yield
    telegram_bot.reset_state()


@pytest.fixture
def calls(monkeypatch):
    """Record Bot API calls; ``getUpdates`` answers from ``calls.updates``."""

    class Recorder(list):
        updates: list = []

    rec = Recorder()

    def fake(token, method, params=None, timeout=15, files=None):
        rec.append((token, method, params or {}))
        if method == "getUpdates":
            out, rec.updates = rec.updates, []
            return out
        if method in ("getChat", "getMe", "getChatMember"):
            return {"type": "private"} if method == "getChat" else {}
        return True

    monkeypatch.setattr(telegram_bot, "api_call", fake)
    return rec


def _sent(calls):
    return [p for _, m, p in calls if m == "sendMessage"]


def _msg(text, chat_id=42, age=0, user_id=1):
    return {
        "text": text,
        "chat": {"id": chat_id},
        "from": {"id": user_id, "username": "chef"},
        "date": int(time.time()) - age,
    }


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
        reply = telegram_bot.handle_message(db, _msg("/status", chat_id=999), "42").text
    assert "nicht freigeschaltet" in reply
    assert "999" in reply
    assert "Speicher" not in reply


def test_id_works_for_anyone(client):
    with SessionLocal() as db:
        reply = telegram_bot.handle_message(db, _msg("/id", chat_id=7), "").text
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
            reply = telegram_bot.handle_message(db, _msg(cmd), "42").text
            assert needle.lower() in reply.lower(), (cmd, reply)
        assert telegram_bot.handle_message(db, _msg("kein befehl"), "42") is None


def test_retry_requeues_via_reconcile(client, monkeypatch):
    from app import transfers

    monkeypatch.setattr(
        transfers, "reconcile_once", lambda db: {"backfilled": 0, "requeued": 3}
    )
    with SessionLocal() as db:
        reply = telegram_bot.handle_message(db, _msg("/retry"), "42").text
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


# --- Reboot with confirmation ---------------------------------------------


def _reboot_prompt(db, user_id=1):
    reply = telegram_bot.handle_message(db, _msg("/reboot", user_id=user_id), "42")
    buttons = reply.reply_markup["inline_keyboard"][0]
    return reply, buttons[0]["callback_data"], buttons[1]["callback_data"]


def _press(db, data, user_id=1, chat_id=42, now=None, run=None):
    callback = {
        "id": "cb1",
        "data": data,
        "from": {"id": user_id, "username": "chef"},
        "message": {"message_id": 5, "chat": {"id": chat_id}},
    }
    return telegram_bot.handle_callback(db, callback, "42", now=now, run=run)


def test_reboot_asks_then_runs_on_confirm(client):
    runs = []
    with SessionLocal() as db:
        reply, confirm, _ = _reboot_prompt(db)
        assert "wirklich neu starten" in reply.text
        assert not runs  # asking alone must not reboot
        result = _press(db, confirm, run=lambda cmd, delay: runs.append((cmd, delay)))
    assert runs and "reboot" in runs[0][0]
    assert runs[0][1] == telegram_bot.REBOOT_DELAY
    assert "Neustart bestätigt" in result.new_text

    with SessionLocal() as db:
        from sqlalchemy import select

        from app.models import AuditEvent

        actions = list(db.scalars(select(AuditEvent.action)))
    assert "telegram.reboot" in actions


def test_reboot_confirm_is_one_time(client):
    runs = []
    with SessionLocal() as db:
        _, confirm, _ = _reboot_prompt(db)
        _press(db, confirm, run=lambda cmd, delay: runs.append(cmd))
        again = _press(db, confirm, run=lambda cmd, delay: runs.append(cmd))
    assert len(runs) == 1
    assert "Abgelaufen" in again.toast


def test_reboot_cancel_expiry_and_foreign_user(client):
    runs = []
    run = lambda cmd, delay: runs.append(cmd)  # noqa: E731
    with SessionLocal() as db:
        _, _, cancel = _reboot_prompt(db)
        assert "abgebrochen" in _press(db, cancel, run=run).new_text

        _, confirm, _ = _reboot_prompt(db)
        late = time.time() + telegram_bot.REBOOT_CONFIRM_SECONDS + 5
        assert "abgelaufen" in _press(db, confirm, now=late, run=run).new_text

        _, confirm, _ = _reboot_prompt(db, user_id=1)
        assert "Nur wer" in _press(db, confirm, user_id=2, run=run).toast
        assert "Nicht freigeschaltet" in _press(db, confirm, chat_id=999, run=run).toast
        # The rightful requester can still confirm after the refusals.
        _press(db, confirm, run=run)
    assert len(runs) == 1


def test_reboot_disabled_without_command(client, monkeypatch):
    monkeypatch.setattr(telegram_bot, "_power_command", lambda action: "")
    with SessionLocal() as db:
        reply = telegram_bot.handle_message(db, _msg("/reboot"), "42")
    assert "deaktiviert" in reply.text
    assert reply.reply_markup is None


def test_shutdown_asks_with_strong_warning_then_runs(client):
    runs = []
    with SessionLocal() as db:
        reply = telegram_bot.handle_message(db, _msg("/shutdown"), "42")
        assert "herunterfahren" in reply.text
        assert "nur vor Ort" in reply.text
        assert not runs
        confirm = reply.reply_markup["inline_keyboard"][0][0]
        assert "ausschalten" in confirm["text"]
        result = _press(
            db, confirm["callback_data"], run=lambda cmd, delay: runs.append(cmd)
        )
    assert runs and "poweroff" in runs[0]
    assert "Herunterfahren bestätigt" in result.new_text

    with SessionLocal() as db:
        from sqlalchemy import select

        from app.models import AuditEvent

        actions = list(db.scalars(select(AuditEvent.action)))
    assert "telegram.shutdown" in actions


def test_confirm_runs_the_action_it_was_issued_for(client):
    runs = []
    with SessionLocal() as db:
        _, reboot_confirm, _ = _reboot_prompt(db)
        shutdown = telegram_bot.handle_message(db, _msg("/shutdown"), "42")
        shutdown_cancel = shutdown.reply_markup["inline_keyboard"][0][1]["callback_data"]
        _press(db, shutdown_cancel, run=lambda cmd, delay: runs.append(cmd))
        _press(db, reboot_confirm, run=lambda cmd, delay: runs.append(cmd))
    # Cancelling the shutdown ran nothing; the reboot button ran the reboot.
    assert len(runs) == 1 and "reboot" in runs[0]


def test_shutdown_disabled_without_command(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "shutdown_command", "")
    with SessionLocal() as db:
        reply = telegram_bot.handle_message(db, _msg("/shutdown"), "42")
    assert "OGC_SHUTDOWN_COMMAND" in reply.text
    assert reply.reply_markup is None


def test_service_restart_asks_then_runs(client):
    runs = []
    with SessionLocal() as db:
        reply = telegram_bot.handle_message(db, _msg("/restart"), "42")
        assert "Dienst neu starten" in reply.text
        assert "nicht die ganze Box" in reply.text
        assert not runs
        confirm = reply.reply_markup["inline_keyboard"][0][0]["callback_data"]
        result = _press(db, confirm, run=lambda cmd, delay: runs.append(cmd))
    assert runs and "restart offgridcloud" in runs[0]
    assert "Dienst-Neustart bestätigt" in result.new_text

    with SessionLocal() as db:
        from sqlalchemy import select

        from app.models import AuditEvent

        actions = list(db.scalars(select(AuditEvent.action)))
    assert "telegram.restart" in actions


def test_service_restart_disabled_without_command(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "restart_service_command", "")
    with SessionLocal() as db:
        reply = telegram_bot.handle_message(db, _msg("/restart"), "42")
    assert "OGC_RESTART_SERVICE_COMMAND" in reply.text
    assert reply.reply_markup is None


def test_poller_answers_reboot_button(client, admin_auth, calls, monkeypatch):
    from app import power

    runs = []
    monkeypatch.setattr(power, "run_power_command", lambda cmd, delay: runs.append(cmd))
    client.put(
        "/api/system",
        headers=admin_auth,
        json={"telegram_bot_token": "tok", "telegram_chat_id": "42"},
    )
    try:
        poller = telegram_bot.BotPoller()
        calls.updates = [{"update_id": 1, "message": _msg("/reboot")}]
        poller.poll_once()
        prompt = [p for p in _sent(calls) if "reply_markup" in p][-1]
        confirm = prompt["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        calls.updates = [
            {
                "update_id": 2,
                "callback_query": {
                    "id": "cb",
                    "data": confirm,
                    "from": {"id": 1},
                    "message": {"message_id": 9, "chat": {"id": 42}},
                },
            }
        ]
        poller.poll_once()
        assert len(runs) == 1
        methods = [m for _, m, _ in calls]
        assert "answerCallbackQuery" in methods
        assert "editMessageText" in methods
    finally:
        client.put(
            "/api/system",
            headers=admin_auth,
            json={"telegram_bot_token": "", "telegram_chat_id": ""},
        )


# --- Group / channel branding ---------------------------------------------


def _fake_api(monkeypatch, chat, member):
    rec = []

    def fake(token, method, params=None, timeout=15, files=None):
        rec.append((method, params or {}, files))
        if method == "getChat":
            return chat
        if method == "getMe":
            return {"id": 777}
        if method == "getChatMember":
            return member
        if method == "sendMessage":
            return {"message_id": 99}
        return True

    monkeypatch.setattr(telegram_bot, "api_call", fake)
    return rec


def test_group_is_branded_and_greeting_pinned(monkeypatch, tmp_path):
    photo = tmp_path / "icon.png"
    photo.write_bytes(b"\x89PNG fake")
    monkeypatch.setattr(telegram_bot, "CHAT_PHOTO", photo)
    rec = _fake_api(
        monkeypatch,
        {"type": "supergroup", "title": "Einsatz"},
        {"status": "administrator", "can_change_info": True, "can_pin_messages": True},
    )
    telegram_bot.announce_connected("tok", "-100")
    methods = [m for m, _, _ in rec]
    title = next(p for m, p, _ in rec if m == "setChatTitle")["title"]
    assert title.startswith("OffgridCloud · ")
    assert "setChatDescription" in methods
    photo_call = next(f for m, _, f in rec if m == "setChatPhoto")
    assert photo_call["photo"][1] == b"\x89PNG fake"
    pin = next(p for m, p, _ in rec if m == "pinChatMessage")
    assert pin["message_id"] == 99


def test_branding_is_idempotent(monkeypatch):
    rec = _fake_api(
        monkeypatch,
        {
            "type": "group",
            "title": telegram_bot.chat_title(),
            "description": telegram_bot.chat_description(),
        },
        {"status": "creator"},
    )
    telegram_bot.announce_connected("tok", "-5")
    methods = [m for m, _, _ in rec]
    assert "setChatTitle" not in methods
    assert "setChatPhoto" not in methods
    assert "setChatDescription" not in methods


def test_group_without_admin_gets_hint(monkeypatch):
    rec = _fake_api(monkeypatch, {"type": "group", "title": "X"}, {"status": "member"})
    telegram_bot.announce_connected("tok", "-5")
    methods = [m for m, _, _ in rec]
    assert "setChatTitle" not in methods
    assert "pinChatMessage" not in methods
    texts = [p["text"] for m, p, _ in rec if m == "sendMessage"]
    assert any("OffgridCloud verbunden" in t for t in texts)
    assert any("Admin" in t for t in texts)


def test_private_chat_is_not_branded(monkeypatch):
    rec = _fake_api(monkeypatch, {"type": "private"}, {})
    telegram_bot.announce_connected("tok", "42")
    methods = [m for m, _, _ in rec]
    assert "getChatMember" not in methods
    assert "setChatTitle" not in methods


def test_multipart_body_contains_fields_and_file():
    body, ctype = telegram_bot._multipart(
        {"chat_id": "-100"}, {"photo": ("a.png", b"BYTES", "image/png")}
    )
    assert ctype.startswith("multipart/form-data; boundary=")
    assert b'name="chat_id"' in body and b"-100" in body
    assert b'filename="a.png"' in body and b"BYTES" in body
