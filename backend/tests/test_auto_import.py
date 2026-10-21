"""Auto-import of plugged-in media + status LED feedback."""

from __future__ import annotations

import pytest

from app import auto_import, drives, status_led, storage_import
from app.config import get_settings
from app.db import SessionLocal
from app.models import AutoImportSettings, MediaItem

from .test_storage import _jpeg, _lsblk


@pytest.fixture
def plug(tmp_path, monkeypatch):
    """A mounted camera card (sdb1) whose presence the test can toggle, a fake
    sysfs LED, and captured helper calls / notifications."""
    card = tmp_path / "media" / "CANON"
    (card / "DCIM").mkdir(parents=True)
    (card / "DCIM" / "IMG_0001.JPG").write_bytes(_jpeg((10, 200, 30)))
    (card / "DCIM" / "IMG_0002.JPG").write_bytes(_jpeg((200, 10, 30)))
    (card / "readme.txt").write_text("x")

    data = _lsblk(media_mount=str(card))
    data["blockdevices"][2]["children"][0]["uuid"] = "AUTO-CARD"
    state = {"present": True}
    without = {"blockdevices": [d for d in data["blockdevices"] if d["name"] != "sdb"]}
    monkeypatch.setattr(drives, "_run_lsblk", lambda: data if state["present"] else without)
    monkeypatch.setattr(drives, "_MEDIA_ROOTS", (str(tmp_path) + "/",))
    monkeypatch.setattr(get_settings(), "storage_command", "")
    monkeypatch.setattr(storage_import, "SPACE_RESERVE", 0)
    original = storage_import.start
    monkeypatch.setattr(storage_import, "start", lambda **k: original(**k, run_async=False))

    leds = tmp_path / "leds"
    (leds / "ACT").mkdir(parents=True)
    (leds / "ACT" / "trigger").write_text("none timer heartbeat [mmc0] default-on\n")
    (leds / "ACT" / "brightness").write_text("0\n")
    monkeypatch.setattr(status_led, "LEDS_DIR", leds)
    led_calls: list[tuple] = []
    monkeypatch.setattr(status_led, "_helper", lambda *a: led_calls.append(a))
    notes: list[tuple] = []
    monkeypatch.setattr(auto_import, "_notify",
                        lambda entry, cfg, ok: notes.append((entry.label, ok, entry.message)))

    status_led.reset()
    auto_import.reset()
    with SessionLocal() as db:
        db.query(AutoImportSettings).delete()
        db.commit()
    yield {"card": card, "present": state, "led": led_calls, "notes": notes}
    status_led.reset()
    auto_import.reset()


def _enable(client, auth, **extra):
    folder = client.post("/api/folders", headers=auth, json={"name": "Auto"}).json()
    r = client.put("/api/storage/auto-import", headers=auth,
                   json={"enabled": True, "folder_id": folder["id"], **extra})
    assert r.status_code == 200, r.text
    return folder


def test_enable_needs_a_target(client, admin_auth, plug):
    r = client.put("/api/storage/auto-import", headers=admin_auth, json={"enabled": True})
    assert r.status_code == 400
    r = client.put("/api/storage/auto-import", headers=admin_auth,
                   json={"enabled": True, "mode": "cache"})
    assert r.status_code == 400
    out = client.get("/api/storage/auto-import", headers=admin_auth).json()
    assert out["enabled"] is False
    assert out["leds"] == ["ACT"] and out["led_active"] == "ACT"


def test_plugged_card_is_imported_once_and_led_follows(client, admin_auth, plug):
    folder = _enable(client, admin_auth)

    auto_import.tick()
    with SessionLocal() as db:
        names = sorted(m.filename for m in db.query(MediaItem).filter_by(folder_id=folder["id"]))
    assert names == ["IMG_0001.JPG", "IMG_0002.JPG"]  # media only
    assert auto_import.overall_state() == "done"
    assert plug["led"][-1] == ("ACT", "on")
    assert plug["notes"] == [("CANON", True, plug["notes"][0][2])]
    assert "2 Datei(en)" in plug["notes"][0][2]

    # Still plugged in: nothing happens again.
    auto_import.tick()
    assert len(plug["notes"]) == 1
    assert client.get("/api/storage/auto-import", headers=admin_auth).json()["media"][0][
        "state"] == "done"

    # Pulled: the LED goes back to what it did before (SD activity).
    plug["present"]["present"] = False
    auto_import.tick()
    assert auto_import.overall_state() == "idle"
    assert plug["led"][-1] == ("ACT", "trigger", "mmc0")

    # Plugged in again: re-checked, but every file is a known duplicate.
    plug["present"]["present"] = True
    auto_import.tick()
    assert "0 Datei(en)" in plug["notes"][-1][2] and "2 schon vorhanden" in plug["notes"][-1][2]


def test_busy_blinks_slowly(client, admin_auth, plug, monkeypatch):
    _enable(client, admin_auth)
    # Keep the job "running": don't execute it.
    started = []

    def pending(**kwargs):
        job = storage_import.ImportJob(id="j1", kind="import", device=kwargs["device"],
                                       user_id=None, status="running", auto=True)
        started.append(job)
        storage_import._jobs[job.id] = job
        return job

    monkeypatch.setattr(storage_import, "start", pending)
    auto_import.tick()
    assert started and auto_import.overall_state() == "busy"
    assert plug["led"][-1] == ("ACT", "blink", "500", "500")

    started[0].status, started[0].failed = "done", 1
    started[0].errors = ["IMG_0002.JPG: I/O error"]
    auto_import.tick()
    assert auto_import.overall_state() == "error"
    assert plug["led"][-1] == ("ACT", "blink", "100", "100")
    assert plug["notes"][-1][1] is False and "I/O error" in plug["notes"][-1][2]
    storage_import._jobs.pop("j1", None)


def test_cache_mode_without_cache_drive_flags_error(client, admin_auth, plug):
    _enable(client, admin_auth)
    with SessionLocal() as db:
        auto_import.get_row(db).mode = "cache"
        db.commit()
    auto_import.tick()
    assert auto_import.overall_state() == "error"
    assert "Cache" in plug["notes"][-1][2]


def test_offgridcloud_drives_are_left_alone(client, admin_auth, plug):
    (plug["card"] / storage_import.CACHE_DIRNAME).mkdir()
    _enable(client, admin_auth)
    auto_import.tick()
    assert auto_import.overall_state() == "idle"
    assert plug["notes"] == [] and plug["led"] == []


def test_disabled_does_nothing(client, admin_auth, plug):
    auto_import.tick()
    assert auto_import.entries() == [] and plug["led"] == []


def test_led_off_switch_keeps_led_untouched(client, admin_auth, plug):
    _enable(client, admin_auth, led_enabled=False)
    auto_import.tick()
    assert auto_import.overall_state() == "done"
    assert plug["led"] == []


def test_led_test_needs_helper(client, admin_auth, plug):
    r = client.post("/api/storage/auto-import/led-test", headers=admin_auth,
                    json={"state": "done"})
    assert r.status_code == 409


def test_led_test_previews_state(client, admin_auth, plug, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_command", "true")
    r = client.post("/api/storage/auto-import/led-test", headers=admin_auth,
                    json={"state": "error"})
    assert r.status_code == 204
    assert plug["led"][-1] == ("ACT", "blink", "100", "100")


def test_unknown_led_name_rejected(client, admin_auth, plug):
    r = client.put("/api/storage/auto-import", headers=admin_auth, json={"led_name": "x; rm"})
    assert r.status_code == 422
