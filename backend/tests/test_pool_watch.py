"""Bundled fleet alerts from the pool hub (app.pool_watch)."""

import pytest
from sqlalchemy import delete

import app.pool as pool_core
from app import notices, notify, pool_watch
from app.admin_ops import get_system_settings
from app.db import SessionLocal
from app.models import PoolPeer
from app.pool_watch import OFFLINE_AFTER, REARM_AFTER_ZERO_ROUNDS, evaluate

GB = 1024**3


def _ok(**extra):
    return {
        "reachable": True, "uploads_paused": False, "failed_transfers": 0,
        "disk_free": 50 * GB, "disk_total": 100 * GB, **extra,
    }


DOWN = {"reachable": False, "error": "timed out"}


def _run(statuses, prev=None):
    """Feed statuses through evaluate(); return the alert lines of each round."""
    out = []
    for status in statuses:
        prev, lines = evaluate("Boot", prev, status)
        out.append(lines)
    return prev, out


def test_first_round_only_records():
    _, rounds = _run([_ok(uploads_paused=True, failed_transfers=3, disk_free=1)])
    assert rounds == [[]]
    _, rounds = _run([DOWN])
    assert rounds == [[]]


def test_offline_needs_consecutive_misses_and_is_reported_once():
    _, rounds = _run([_ok(), DOWN, _ok(), DOWN])  # single blips: silent
    assert all(r == [] for r in rounds)
    _, rounds = _run([_ok()] + [DOWN] * (OFFLINE_AFTER + 3) + [_ok()])
    flat = [line for r in rounds for line in r]
    assert flat == ["🔴 Boot: nicht erreichbar (timed out)", "🟢 Boot: wieder erreichbar"]


def test_pause_resume_and_low_space_are_transitions():
    _, rounds = _run([
        _ok(),
        _ok(uploads_paused=True),
        _ok(uploads_paused=True),
        _ok(),
        _ok(disk_free=5 * GB),
        _ok(disk_free=4 * GB),  # still low: no repeat
        _ok(),
        _ok(disk_free=5 * GB),  # low again after recovering: news again
    ])
    assert rounds[1] == ["⏸ Boot: Uploads pausiert"]
    assert rounds[2] == []
    assert rounds[3] == ["▶️ Boot: Uploads fortgesetzt"]
    assert rounds[4] == ["💾 Boot: Speicher knapp – noch 5.0 GB frei (95 % belegt)"]
    assert rounds[5] == [] and rounds[6] == []
    assert rounds[7] and rounds[7][0].startswith("💾")


def test_failures_report_new_highs_not_the_resync_cycle():
    # auto-resync: failed jobs go back to the queue (0) and fail again (3).
    cycle = [_ok(failed_transfers=3), _ok(), _ok(failed_transfers=3)]
    _, rounds = _run([_ok()] + cycle * 4 + [_ok(failed_transfers=5)])
    flat = [line for r in rounds for line in r]
    assert flat == [
        "❌ Boot: 3 neue fehlgeschlagene Transfers (jetzt 3)",
        "❌ Boot: 2 neue fehlgeschlagene Transfers (jetzt 5)",
    ]


def test_failures_rearm_after_a_quiet_hour():
    state, _ = _run([_ok(), _ok(failed_transfers=2)])
    state, rounds = _run([_ok()] * REARM_AFTER_ZERO_ROUNDS + [_ok(failed_transfers=1)], state)
    assert rounds[-1] == ["❌ Boot: 1 neuer fehlgeschlagener Transfer (jetzt 1)"]


# --- one bundled message per round ----------------------------------------------


@pytest.fixture
def peers(client):
    pool_watch.reset_state()
    notices.reset()
    with SessionLocal() as db:
        db.execute(delete(PoolPeer))
        db.add_all([
            PoolPeer(name="Boot", base_url="http://10.0.0.2:8000"),
            PoolPeer(name="Drohne", base_url="http://10.0.0.3:8000"),
            PoolPeer(name="Alt", base_url="http://10.0.0.4:8000", enabled=False),
        ])
        db.commit()
    yield
    pool_watch.reset_state()
    notices.reset()
    with SessionLocal() as db:
        db.execute(delete(PoolPeer))
        get_system_settings(db).notify_on_pool = True
        db.commit()


def test_one_message_for_all_changes(peers, monkeypatch):
    world = {"Boot": _ok(), "Drohne": _ok(), "Alt": _ok()}
    polled = []
    monkeypatch.setattr(
        pool_core, "poll_peer", lambda p: polled.append(p.name) or world[p.name]
    )
    sent = []
    monkeypatch.setattr(
        notify, "dispatch", lambda settings, event, title, message, payload: sent.append(
            (event, title, message)
        ),
    )
    with SessionLocal() as db:
        assert pool_watch.check_once(db) == []  # baseline
        assert "Alt" not in polled  # disabled peers are not watched

        world["Boot"] = _ok(uploads_paused=True)
        world["Drohne"] = _ok(disk_free=1 * GB)
        pool_watch.check_once(db)
    assert len(sent) == 1
    event, title, message = sent[0]
    assert event == "pool.alert" and title == "Pool: 2 Änderungen"
    assert message.splitlines() == [
        "⏸ Boot: Uploads pausiert",
        "💾 Drohne: Speicher knapp – noch 1.0 GB frei (99 % belegt)",
    ]
    assert notices.recent()[-1]["level"] == "warning"


def test_pool_toggle_silences_the_hub(peers, monkeypatch):
    world = {"Boot": _ok(), "Drohne": _ok()}
    monkeypatch.setattr(pool_core, "poll_peer", lambda p: world[p.name])
    sent = []
    monkeypatch.setattr(notify, "dispatch", lambda *a, **k: sent.append(a))
    with SessionLocal() as db:
        get_system_settings(db).notify_on_pool = False
        db.commit()
        pool_watch.check_once(db)
        world["Boot"] = _ok(uploads_paused=True)
        assert pool_watch.check_once(db)  # still detected …
    assert sent == [] and notices.recent() == []  # … but not sent


def test_toggle_is_exposed_in_system_settings(client, admin_auth):
    out = client.put("/api/system", headers=admin_auth, json={"notify_on_pool": False})
    assert out.status_code == 200 and out.json()["notify_on_pool"] is False
    assert client.get("/api/system", headers=admin_auth).json()["notify_on_pool"] is False
    client.put("/api/system", headers=admin_auth, json={"notify_on_pool": True})
