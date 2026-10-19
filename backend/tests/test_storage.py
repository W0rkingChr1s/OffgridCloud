"""Attached storage media: discovery/protection, helper calls, browse + import."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import drives, storage_import
from app.config import get_settings


def _lsblk(media_mount: str = "", buffer_mount: str = "/mnt/ssd") -> dict:
    return {
        "blockdevices": [
            {
                "name": "mmcblk0", "path": "/dev/mmcblk0", "type": "disk",
                "size": 32 * 2**30, "rm": False, "hotplug": False, "tran": None,
                "children": [
                    {"name": "mmcblk0p1", "path": "/dev/mmcblk0p1", "type": "part",
                     "size": 512 * 2**20, "fstype": "vfat", "mountpoint": "/boot/firmware"},
                    {"name": "mmcblk0p2", "path": "/dev/mmcblk0p2", "type": "part",
                     "size": 31 * 2**30, "fstype": "ext4", "mountpoint": "/"},
                ],
            },
            {
                "name": "sda", "path": "/dev/sda", "type": "disk", "size": 500 * 2**30,
                "rm": "0", "hotplug": "1", "tran": "usb", "model": "SSD",
                "children": [
                    {"name": "sda1", "path": "/dev/sda1", "type": "part",
                     "size": 500 * 2**30, "fstype": "ext4", "mountpoint": buffer_mount},
                ],
            },
            {
                "name": "sdb", "path": "/dev/sdb", "type": "disk", "size": 64 * 2**30,
                "rm": True, "hotplug": True, "tran": "usb", "model": "Card Reader",
                "children": [
                    {"name": "sdb1", "path": "/dev/sdb1", "type": "part", "size": 64 * 2**30,
                     "fstype": "exfat", "label": "CANON", "mountpoint": media_mount or None},
                ],
            },
            {"name": "sdc", "path": "/dev/sdc", "type": "disk", "size": 0},  # empty slot
            {"name": "loop0", "path": "/dev/loop0", "type": "loop", "size": 100},
            {"name": "zram0", "path": "/dev/zram0", "type": "disk", "size": 2**30},
        ]
    }


def test_parse_marks_system_and_buffer_disks():
    disks = drives.parse_lsblk(_lsblk(), [Path("/mnt/ssd/offgrid/buffer")])
    by_name = {d.name: d for d in disks}
    assert set(by_name) == {"mmcblk0", "sda", "sdb"}
    assert by_name["mmcblk0"].system and by_name["mmcblk0"].protected
    assert by_name["sda"].buffer and not by_name["sda"].system
    assert by_name["sda"].removable  # hotplug "1" string from old lsblk
    card = by_name["sdb"]
    assert not card.protected
    assert card.partitions[0].label == "CANON"
    assert not card.partitions[0].browsable  # not mounted


def test_parse_mount_outside_media_roots_is_system():
    disks = drives.parse_lsblk(_lsblk(media_mount="/srv/data"), [])
    assert {d.name: d for d in disks}["sdb"].system


@pytest.fixture
def card(tmp_path, monkeypatch):
    """A fake mounted camera card: sdb1 mounted at a temp dir under /media."""
    mount = tmp_path / "media" / "CANON"
    (mount / "DCIM" / "100CANON").mkdir(parents=True)
    (mount / "DCIM" / "100CANON" / "IMG_0001.JPG").write_bytes(b"a" * 1000)
    (mount / "DCIM" / "100CANON" / "IMG_0002.MP4").write_bytes(b"b" * 2000)
    (mount / "DCIM" / "101CANON").mkdir()
    (mount / "DCIM" / "101CANON" / "IMG_0001.JPG").write_bytes(b"c" * 1500)
    (mount / "notes.txt").write_text("hello")
    (mount / ".hidden").write_text("x")
    (mount / "System Volume Information").mkdir()
    (mount / "System Volume Information" / "junk.jpg").write_bytes(b"j")

    data = _lsblk(media_mount=str(mount))
    monkeypatch.setattr(drives, "_run_lsblk", lambda: data)
    # The temp mount isn't under /media/ — treat it as a media root for the test.
    monkeypatch.setattr(drives, "_MEDIA_ROOTS", (str(tmp_path) + "/",))
    return mount


def test_overview_lists_disks(client, admin_auth, card):
    body = client.get("/api/storage", headers=admin_auth).json()
    assert body["manage_enabled"] is True
    assert "exfat" in body["filesystems"]
    names = {d["name"]: d for d in body["disks"]}
    assert names["mmcblk0"]["protected"] is True
    assert names["sdb"]["partitions"][0]["browsable"] is True


def test_storage_requires_admin(client, admin_auth):
    client.post(
        "/api/users",
        headers=admin_auth,
        json={"email": "field@test.local", "password": "fieldpass123", "role": "user"},
    )
    tok = client.post(
        "/api/auth/login", json={"email": "field@test.local", "password": "fieldpass123"}
    ).json()["access_token"]
    assert client.get("/api/storage", headers={"Authorization": f"Bearer {tok}"}).status_code == 403


def test_browse_lists_and_confines(client, admin_auth, card):
    r = client.get("/api/storage/browse", params={"device": "/dev/sdb1"}, headers=admin_auth)
    assert r.status_code == 200, r.text
    names = [e["name"] for e in r.json()["entries"]]
    assert names[0] == "DCIM"  # directories first
    assert "notes.txt" in names

    r = client.get(
        "/api/storage/browse", params={"device": "/dev/sdb1", "path": "DCIM/100CANON"},
        headers=admin_auth,
    )
    body = r.json()
    assert body["parent"] == "DCIM"
    assert {e["name"]: e["is_media"] for e in body["entries"]} == {
        "IMG_0001.JPG": True, "IMG_0002.MP4": True,
    }

    r = client.get(
        "/api/storage/browse", params={"device": "/dev/sdb1", "path": "../../.."},
        headers=admin_auth,
    )
    assert r.status_code == 409


def test_browse_refuses_protected(client, admin_auth, card):
    r = client.get("/api/storage/browse", params={"device": "/dev/mmcblk0p2"}, headers=admin_auth)
    assert r.status_code == 409


def test_import_copies_queues_and_dedupes(client, admin_auth, card, monkeypatch):
    monkeypatch.setattr(storage_import, "SPACE_RESERVE", 0)
    folder = client.post("/api/folders", headers=admin_auth, json={"name": "Card"}).json()

    def run_sync(**kwargs):
        return original(**kwargs, run_async=False)

    original = storage_import.start
    monkeypatch.setattr(storage_import, "start", run_sync)

    r = client.post(
        "/api/storage/imports",
        headers=admin_auth,
        json={
            "device": "/dev/sdb1", "paths": ["DCIM", "notes.txt"], "folder_id": folder["id"],
            "media_only": True, "tags": ["Drohne"],
        },
    )
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["total_files"] == 3  # notes.txt filtered, junk dir skipped

    job = next(j for j in client.get("/api/storage/imports", headers=admin_auth).json()
               if j["id"] == job["id"])
    assert job["status"] == "done"
    assert job["imported"] == 3

    media = client.get(f"/api/folders/{folder['id']}/media", headers=admin_auth).json()
    names = sorted(m["filename"] for m in media)
    # Same name from a second camera folder is disambiguated, not overwritten.
    assert names == ["IMG_0001 (2).JPG", "IMG_0001.JPG", "IMG_0002.MP4"]
    assert all(m["tags"] == ["drohne"] for m in media)
    for m in media:
        stored = Path(get_settings().buffer_dir) / str(folder["id"])
        assert any(p.name.endswith(m["filename"]) for p in stored.iterdir())

    # Re-running the same import skips everything already on the box.
    r = client.post(
        "/api/storage/imports",
        headers=admin_auth,
        json={"device": "/dev/sdb1", "paths": ["DCIM"], "folder_id": folder["id"]},
    )
    again = next(j for j in client.get("/api/storage/imports", headers=admin_auth).json()
                 if j["id"] == r.json()["id"])
    assert again["imported"] == 0 and again["skipped"] == 3


def test_import_rejects_when_buffer_too_small(client, admin_auth, card, monkeypatch):
    folder = client.post("/api/folders", headers=admin_auth, json={"name": "Full"}).json()
    monkeypatch.setattr(storage_import, "SPACE_RESERVE", 10**18)
    r = client.post(
        "/api/storage/imports",
        headers=admin_auth,
        json={"device": "/dev/sdb1", "paths": ["DCIM"], "folder_id": folder["id"]},
    )
    assert r.status_code == 507


def test_mount_calls_helper(client, admin_auth, card, monkeypatch):
    # Unmounted card this time.
    monkeypatch.setattr(drives, "_run_lsblk", lambda: _lsblk())
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)

        class P:
            returncode = 0
            stdout = "/media/offgridcloud/CANON\n"
            stderr = ""

        return P()

    monkeypatch.setattr(drives.subprocess, "run", fake_run)
    r = client.post("/api/storage/mount", headers=admin_auth, json={"device": "/dev/sdb1"})
    assert r.status_code == 200, r.text
    assert r.json()["mountpoint"] == "/media/offgridcloud/CANON"
    assert calls[-1][-2:] == ["mount", "/dev/sdb1"]
    assert calls[-1][0] == "sudo"


def test_protected_actions_refused_before_helper(client, admin_auth, card, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("helper must not run for protected devices")

    monkeypatch.setattr(drives.subprocess, "run", boom)
    for path, body in [
        ("/api/storage/unmount", {"device": "/dev/mmcblk0p2"}),
        ("/api/storage/eject", {"device": "/dev/sda"}),
        ("/api/storage/format",
         {"device": "/dev/mmcblk0", "fstype": "exfat", "confirm": "mmcblk0"}),
    ]:
        r = client.post(path, headers=admin_auth, json=body)
        assert r.status_code == 409, (path, r.text)


def test_format_requires_matching_confirmation_and_valid_label(
    client, admin_auth, card, monkeypatch
):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)

        class P:
            returncode = 0
            stdout = ""
            stderr = ""

        return P()

    monkeypatch.setattr(drives.subprocess, "run", fake_run)
    r = client.post("/api/storage/format", headers=admin_auth,
                    json={"device": "/dev/sdb", "fstype": "exfat", "confirm": "sda"})
    assert r.status_code == 400
    r = client.post("/api/storage/format", headers=admin_auth,
                    json={"device": "/dev/sdb", "fstype": "vfat", "label": "WAY_TOO_LONG_X",
                          "confirm": "sdb"})
    assert r.status_code == 409
    assert calls == []
    r = client.post("/api/storage/format", headers=admin_auth,
                    json={"device": "/dev/sdb", "fstype": "exfat", "label": "FIELD",
                          "confirm": "sdb"})
    assert r.status_code == 204, r.text
    assert calls[-1][-4:] == ["format", "/dev/sdb", "exfat", "FIELD"]


def test_helper_disabled_returns_conflict(client, admin_auth, card, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_command", "")
    assert client.get("/api/storage", headers=admin_auth).json()["manage_enabled"] is False
    r = client.post("/api/storage/eject", headers=admin_auth, json={"device": "/dev/sdb"})
    assert r.status_code == 409
