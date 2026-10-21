"""Unattended updates: window, idle gate, once a day, failure memory, report."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from app import autoupdate, notify
from app.admin_ops import get_system_settings
from app.config import get_settings
from app.db import SessionLocal
from app.updater import (
    PHASE_FAILED,
    PHASE_RUNNING,
    PHASE_SUCCESS,
    UpdateInfo,
    UpdateState,
    clear_state,
    write_state,
)

NIGHT = datetime(2026, 10, 21, 3, 30)
NOON = datetime(2026, 10, 21, 12, 0)


def _info(available=True, latest="v9.9.9", error=""):
    def checker(current, repo, **_kw):
        return UpdateInfo(current=current, latest=latest, update_available=available, error=error)

    return checker


@pytest.fixture
def box(client, monkeypatch):
    """Auto-update on, idle box, no real update command or notifications."""
    started: list[str] = []
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(autoupdate, "available", lambda: True)
    monkeypatch.setattr(autoupdate, "_metered", lambda: False)
    monkeypatch.setattr(
        autoupdate, "start_update", lambda data_dir, cmd, ver, **kw: started.append(cmd)
    )
    monkeypatch.setattr(
        notify, "dispatch", lambda settings, event, title, msg, payload: sent.append((event, title))
    )
    clear_state(get_settings().data_dir)
    autoupdate._pending_path().unlink(missing_ok=True)
    with SessionLocal() as db:
        row = get_system_settings(db)
        row.auto_update_enabled = True
        row.auto_update_start_hour = 3
        row.auto_update_end_hour = 5
        row.auto_update_metered = False
        row.auto_update_last_day = ""
        row.auto_update_failed_target = ""
        db.commit()
        yield db, started, sent
    autoupdate._pending_path().unlink(missing_ok=True)
    clear_state(get_settings().data_dir)
    with SessionLocal() as db:
        get_system_settings(db).auto_update_enabled = False
        db.commit()


@pytest.mark.parametrize(
    ("hour", "start", "end", "inside"),
    [
        (3, 3, 5, True),
        (5, 3, 5, False),
        (2, 3, 5, False),
        (23, 22, 4, True),
        (1, 22, 4, True),
        (12, 22, 4, False),
        (12, 0, 0, True),  # start == end: around the clock
    ],
)
def test_in_window(hour, start, end, inside):
    assert autoupdate.in_window(hour, start, end) is inside


def test_starts_in_window_once_per_day(box):
    db, started, _ = box
    assert autoupdate.tick(db, now=NIGHT, checker=_info()) == "Update auf v9.9.9 gestartet."
    assert len(started) == 1
    assert json.loads(autoupdate._pending_path().read_text())["target"] == "v9.9.9"
    # Same day again: no second attempt.
    reason = autoupdate.tick(db, now=NIGHT.replace(minute=50), checker=_info())
    assert "morgen" in reason
    assert len(started) == 1


def test_waits_outside_window(box):
    db, started, _ = box
    assert "Zeitfenster" in autoupdate.tick(db, now=NOON, checker=_info())
    assert started == []


def test_off_by_default_does_nothing(box):
    db, started, _ = box
    get_system_settings(db).auto_update_enabled = False
    db.commit()
    assert "aus" in autoupdate.tick(db, now=NIGHT, checker=_info())
    assert started == []


def test_waits_while_transfers_run(box, monkeypatch):
    db, started, _ = box
    monkeypatch.setattr(autoupdate, "busy_reason", lambda _db, now=None: "Übertragungen laufen")
    assert autoupdate.tick(db, now=NIGHT, checker=_info()) == "Wartet: Übertragungen laufen."
    assert started == []
    assert get_system_settings(db).auto_update_last_day == ""  # retried later in the window


def test_waits_on_metered_uplink_unless_allowed(box, monkeypatch):
    db, started, _ = box
    monkeypatch.setattr(autoupdate, "_metered", lambda: True)
    assert "getaktete" in autoupdate.tick(db, now=NIGHT, checker=_info())
    assert started == []
    get_system_settings(db).auto_update_metered = True
    db.commit()
    autoupdate.tick(db, now=NIGHT, checker=_info())
    assert len(started) == 1


def test_nothing_to_do_when_current(box):
    db, started, _ = box
    assert "Aktuell" in autoupdate.tick(db, now=NIGHT, checker=_info(available=False))
    assert started == []


def test_skips_target_that_failed_before(box):
    db, started, _ = box
    get_system_settings(db).auto_update_failed_target = "v9.9.9"
    db.commit()
    assert "übersprungen" in autoupdate.tick(db, now=NIGHT, checker=_info())
    assert started == []
    # A newer release is tried again.
    autoupdate.tick(db, now=NIGHT, checker=_info(latest="v9.9.10"))
    assert len(started) == 1


def test_reports_failure_once_and_remembers_target(box):
    db, _, sent = box
    data_dir = get_settings().data_dir
    autoupdate._pending_path().write_text(json.dumps({"target": "v9.9.9"}))
    write_state(data_dir, UpdateState(phase=PHASE_RUNNING))
    assert autoupdate.report_result(db) is False  # still running
    write_state(data_dir, UpdateState(phase=PHASE_FAILED, from_version="1.0", message="kaputt"))
    assert autoupdate.report_result(db) is True
    assert sent == [("server.update", "Automatisches Update fehlgeschlagen")]
    assert get_system_settings(db).auto_update_failed_target == "v9.9.9"
    assert not autoupdate._pending_path().exists()
    assert autoupdate.report_result(db) is False  # only once


def test_reports_success_and_clears_failure(box):
    db, _, sent = box
    get_system_settings(db).auto_update_failed_target = "v1.0"
    db.commit()
    autoupdate._pending_path().write_text(json.dumps({"target": "v9.9.9"}))
    write_state(get_settings().data_dir, UpdateState(phase=PHASE_SUCCESS, to_version="9.9.9"))
    autoupdate.report_result(db)
    assert sent == [("server.update", "Automatisches Update abgeschlossen")]
    assert get_system_settings(db).auto_update_failed_target == ""


def test_busy_reason_sees_running_transfer(box, monkeypatch):
    db, _, _ = box
    assert autoupdate.busy_reason(db) == ""

    class Job:
        status = "running"

    from app import storage_import

    monkeypatch.setattr(storage_import, "list_jobs", lambda: [Job()])
    assert autoupdate.busy_reason(db) == "Karten-Import läuft"


def test_endpoints(client, admin_auth, monkeypatch):
    monkeypatch.setattr(autoupdate, "available", lambda: True)
    resp = client.get("/api/updates/auto", headers=admin_auth)
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    resp = client.put(
        "/api/updates/auto",
        json={"enabled": True, "start_hour": 22, "end_hour": 4, "allow_metered": True},
        headers=admin_auth,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["enabled"], body["start_hour"], body["end_hour"], body["allow_metered"]) == (
        True,
        22,
        4,
        True,
    )
    assert (
        client.put("/api/updates/auto", json={"start_hour": 24}, headers=admin_auth).status_code
        == 422
    )
    client.put("/api/updates/auto", json={"enabled": False}, headers=admin_auth)


def test_cannot_enable_where_unavailable(client, admin_auth, monkeypatch):
    monkeypatch.setattr(autoupdate, "available", lambda: False)
    resp = client.put("/api/updates/auto", json={"enabled": True}, headers=admin_auth)
    assert resp.status_code == 409


def test_auto_endpoints_require_admin(client):
    assert client.get("/api/updates/auto").status_code == 401
