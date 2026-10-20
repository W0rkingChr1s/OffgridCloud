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


# --- Gallery, thumbnails, duplicate detection, cache --------------------------

import io  # noqa: E402
import os  # noqa: E402

from PIL import Image  # noqa: E402


def _jpeg(color=(200, 30, 30), size=(1200, 800)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """Camera card (sdb1, unmarked) + an SSD (sdd1) that can become the cache."""
    # The test DB is shared, so give every rig its own capture times — the
    # duplicate check would otherwise (correctly) recognise earlier tests' files.
    # Stay well inside every filesystem's timestamp range (ext4/tmpfs clamp far-future
    # mtimes, which would make two different files look identical).
    t0 = 1_600_000_000 + int.from_bytes(os.urandom(3), "big") * 10
    card = tmp_path / "media" / "CANON"
    (card / "DCIM" / "100CANON").mkdir(parents=True)
    photo = card / "DCIM" / "100CANON" / "IMG_0001.JPG"
    photo.write_bytes(_jpeg(tuple(os.urandom(3))))
    os.utime(photo, (t0, t0))
    # Fake RAW: junk header + embedded preview JPEG, like real CR2/NEF files.
    raw = card / "DCIM" / "100CANON" / "IMG_0002.CR2"
    raw.write_bytes(b"II*\x00" + os.urandom(4000) + _jpeg((20, 160, 40)) + os.urandom(2000))
    os.utime(raw, (t0 + 100, t0 + 100))
    (card / "DCIM" / "100CANON" / "notes.txt").write_text("x")
    ssd = tmp_path / "media" / "SSD"
    ssd.mkdir(parents=True)

    data = _lsblk(media_mount=str(card))
    data["blockdevices"][2]["children"][0]["uuid"] = "CARD-UUID"
    data["blockdevices"].append({
        "name": "sdd", "path": "/dev/sdd", "type": "disk", "size": 500 * 2**30, "tran": "usb",
        "children": [{"name": "sdd1", "path": "/dev/sdd1", "type": "part", "size": 500 * 2**30,
                      "fstype": "ext4", "label": "SSD", "uuid": "CACHE-UUID",
                      "mountpoint": str(ssd)}],
    })
    monkeypatch.setattr(drives, "_run_lsblk", lambda: data)
    monkeypatch.setattr(drives, "_MEDIA_ROOTS", (str(tmp_path) + "/",))
    drives.clear_cache()
    # No root helper in tests: the cache dir is created directly.
    monkeypatch.setattr(get_settings(), "storage_command", "")
    monkeypatch.setattr(storage_import, "SPACE_RESERVE", 0)
    original = storage_import.start
    original_cache = storage_import.start_cache
    monkeypatch.setattr(storage_import, "start", lambda **k: original(**k, run_async=False))
    monkeypatch.setattr(storage_import, "start_cache",
                        lambda **k: original_cache(**k, run_async=False))
    from app import buffers
    from app.db import SessionLocal
    from app.models import StorageBuffer, StorageCache

    monkeypatch.setattr(buffers, "SPACE_RESERVE", 0)
    with SessionLocal() as db:
        db.query(StorageCache).delete()
        db.query(StorageBuffer).delete()
        db.commit()
    return {"card": card, "ssd": ssd, "t0": t0, "lsblk": data}


def _token_of(auth):
    return auth["Authorization"].split(" ", 1)[1]


def test_gallery_flattens_media_and_flags_known(client, admin_auth, rig):
    r = client.get("/api/storage/gallery", params={"device": "/dev/sdb1"}, headers=admin_auth)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [(i["name"], i["kind"], i["dir"]) for i in items] == [
        ("IMG_0001.JPG", "photo", "DCIM/100CANON"),
        ("IMG_0002.CR2", "raw", "DCIM/100CANON"),
    ]
    assert all(i["known"] is None for i in items)

    folder = client.post("/api/folders", headers=admin_auth, json={"name": "G"}).json()
    client.post("/api/storage/imports", headers=admin_auth, json={
        "device": "/dev/sdb1", "paths": ["DCIM/100CANON/IMG_0001.JPG"], "folder_id": folder["id"]})
    items = client.get("/api/storage/gallery", params={"device": "/dev/sdb1"},
                       headers=admin_auth).json()["items"]
    known = {i["name"]: i["known"] for i in items}
    assert known["IMG_0001.JPG"]["match"] == "exact"
    assert known["IMG_0001.JPG"]["folder_name"] == "G"
    assert known["IMG_0002.CR2"] is None


def test_constant_size_raw_with_new_mtime_is_not_skipped(client, admin_auth, rig):
    """Same name + size but a different shot (new mtime, new bytes) must import."""
    folder = client.post("/api/folders", headers=admin_auth, json={"name": "RAW"}).json()
    raw = rig["card"] / "DCIM" / "100CANON" / "IMG_0002.CR2"
    body = {"device": "/dev/sdb1", "paths": ["DCIM/100CANON/IMG_0002.CR2"],
            "folder_id": folder["id"]}
    client.post("/api/storage/imports", headers=admin_auth, json=body)
    # Card reformatted, counter reset: same name, same size, different photo.
    size = raw.stat().st_size
    raw.write_bytes(os.urandom(size))
    os.utime(raw, (rig["t0"] + 5000, rig["t0"] + 5000))
    job = client.post("/api/storage/imports", headers=admin_auth, json=body).json()
    job = next(j for j in client.get("/api/storage/imports", headers=admin_auth).json()
               if j["id"] == job["id"])
    assert job["imported"] == 1 and job["skipped"] == 0
    names = sorted(m["filename"] for m in
                   client.get(f"/api/folders/{folder['id']}/media", headers=admin_auth).json())
    assert names == ["IMG_0002 (2).CR2", "IMG_0002.CR2"]


def test_duplicate_in_other_folder_is_skipped_box_wide(client, admin_auth, rig):
    a = client.post("/api/folders", headers=admin_auth, json={"name": "A"}).json()
    b = client.post("/api/folders", headers=admin_auth, json={"name": "B"}).json()
    body = {"device": "/dev/sdb1", "paths": ["DCIM"], "media_only": True}
    client.post("/api/storage/imports", headers=admin_auth, json={**body, "folder_id": a["id"]})
    job = client.post("/api/storage/imports", headers=admin_auth,
                      json={**body, "folder_id": b["id"]}).json()
    job = next(j for j in client.get("/api/storage/imports", headers=admin_auth).json()
               if j["id"] == job["id"])
    assert job["imported"] == 0 and job["skipped"] == 2


def test_thumbnails_for_photo_and_raw(client, admin_auth, rig):
    tok = _token_of(admin_auth)
    for path in ("DCIM/100CANON/IMG_0001.JPG", "DCIM/100CANON/IMG_0002.CR2"):
        r = client.get("/api/storage/thumb",
                       params={"device": "/dev/sdb1", "path": path, "token": tok})
        assert r.status_code == 200, (path, r.text)
        im = Image.open(io.BytesIO(r.content))
        assert im.format == "JPEG" and max(im.size) <= 320
    r = client.get("/api/storage/thumb",
                   params={"device": "/dev/sdb1", "path": "DCIM/100CANON/notes.txt", "token": tok})
    assert r.status_code == 404
    r = client.get("/api/storage/thumb",
                   params={"device": "/dev/sdb1", "path": "../../etc/passwd", "token": tok})
    assert r.status_code == 409
    r = client.get("/api/storage/thumb", params={"device": "/dev/sdb1",
                   "path": "DCIM/100CANON/IMG_0001.JPG", "token": "bogus"})
    assert r.status_code == 401


def test_cache_backup_browse_and_import(client, admin_auth, rig):
    r = client.post("/api/storage/caches", headers=admin_auth, json={"device": "/dev/sdd1"})
    assert r.status_code == 201, r.text
    overview = client.get("/api/storage", headers=admin_auth).json()
    ssd = next(d for d in overview["disks"] if d["name"] == "sdd")
    assert ssd["partitions"][0]["cache"] is True

    # A cache drive can't be formatted by accident.
    r = client.post("/api/storage/format", headers=admin_auth,
                    json={"device": "/dev/sdd", "fstype": "exfat", "confirm": "sdd"})
    assert r.status_code == 409

    r = client.post("/api/storage/caches/backup", headers=admin_auth,
                    json={"device": "/dev/sdb1"})
    assert r.status_code == 202, r.text
    job = next(j for j in client.get("/api/storage/imports", headers=admin_auth).json()
               if j["id"] == r.json()["id"])
    assert job["kind"] == "cache" and job["status"] == "done" and job["imported"] == 2

    caches = client.get("/api/storage/caches", headers=admin_auth).json()
    snap = caches[0]["snapshots"][0]
    assert snap["files"] == 2 and snap["name"].startswith("CANON_")
    copied = rig["ssd"] / snap["path"] / "DCIM" / "100CANON" / "IMG_0001.JPG"
    assert int(copied.stat().st_mtime) == rig["t0"]  # capture time kept

    # Second run copies nothing.
    r = client.post("/api/storage/caches/backup", headers=admin_auth,
                    json={"device": "/dev/sdb1"})
    job = next(j for j in client.get("/api/storage/imports", headers=admin_auth).json()
               if j["id"] == r.json()["id"])
    assert job["imported"] == 0 and job["skipped"] == 2

    # Upload from the cache copy; then the *card* shows the files as known.
    folder = client.post("/api/folders", headers=admin_auth, json={"name": "C"}).json()
    r = client.post("/api/storage/imports", headers=admin_auth, json={
        "device": "/dev/sdd1", "paths": [snap["path"]], "folder_id": folder["id"]})
    assert r.status_code == 202, r.text
    items = client.get("/api/storage/gallery", params={"device": "/dev/sdb1"},
                       headers=admin_auth).json()["items"]
    assert all(i["known"] and i["known"]["match"] == "exact" for i in items)

    r = client.delete(f"/api/storage/caches/CACHE-UUID/snapshots/{snap['name']}",
                      headers=admin_auth)
    assert r.status_code == 204
    assert client.get("/api/storage/caches", headers=admin_auth).json()[0]["snapshots"] == []
    r = client.delete("/api/storage/caches/CACHE-UUID/snapshots/%2E%2E", headers=admin_auth)
    assert r.status_code == 404
    assert client.delete("/api/storage/caches/CACHE-UUID", headers=admin_auth).status_code == 204


def test_cache_backup_needs_a_cache(client, admin_auth, rig):
    r = client.post("/api/storage/caches/backup", headers=admin_auth, json={"device": "/dev/sdb1"})
    assert r.status_code == 409


# --- Buffer drives ----------------------------------------------------------------


def _upload(client, auth, folder_id, filename, data: bytes):
    sess = client.post(f"/api/folders/{folder_id}/uploads", headers=auth,
                       json={"filename": filename, "size": len(data)}).json()
    client.put(f"/api/uploads/{sess['id']}", headers={**auth, "X-Offset": "0"}, content=data)
    return client.post(f"/api/uploads/{sess['id']}/complete", headers=auth).json()


def _stored_paths(folder_id: int) -> list[str]:
    from app.db import SessionLocal
    from app.models import MediaItem

    with SessionLocal() as db:
        return [m.stored_path for m in db.query(MediaItem).filter_by(folder_id=folder_id)]


def test_buffer_drive_takes_new_files_and_is_guarded(client, admin_auth, rig):
    r = client.post("/api/storage/buffers", headers=admin_auth, json={"device": "/dev/sdd1"})
    assert r.status_code == 201, r.text
    overview = client.get("/api/storage", headers=admin_auth).json()
    part = next(d for d in overview["disks"] if d["name"] == "sdd")["partitions"][0]
    assert part["buffer_drive"] is True and part["protected"] is False
    (buf,) = overview["buffers"]
    assert buf["online"] and buf["device"] == "/dev/sdd1" and buf["pending"] == 0

    folder = client.post("/api/folders", headers=admin_auth, json={"name": "B"}).json()
    # Import from the card and a browser upload both land on the drive.
    r = client.post("/api/storage/imports", headers=admin_auth, json={
        "device": "/dev/sdb1", "paths": ["DCIM"], "folder_id": folder["id"]})
    assert r.status_code == 202, r.text
    _upload(client, admin_auth, folder["id"], "clip.mp4", b"x" * 100)
    paths = _stored_paths(folder["id"])
    root = str(rig["ssd"] / "OffgridCloud-Puffer") + "/"
    # (How many imported depends on the shared test DB: duplicates are box-wide.)
    assert len(paths) >= 3 and all(p.startswith(root) for p in paths)
    # Our own buffer dir is hidden when browsing the drive.
    listing = client.get("/api/storage/browse", params={"device": "/dev/sdd1"}, headers=admin_auth)
    names = [e["name"] for e in listing.json()["entries"]]
    assert "OffgridCloud-Puffer" not in names

    # Pending files: it can't be unmounted, ejected, formatted or unmarked.
    overview = client.get("/api/storage", headers=admin_auth).json()
    assert overview["buffers"][0]["pending"] == len(paths)
    for path, body in (("/api/storage/unmount", {"device": "/dev/sdd1"}),
                       ("/api/storage/eject", {"device": "/dev/sdd"}),
                       ("/api/storage/format", {"device": "/dev/sdd", "fstype": "exfat",
                                                "confirm": "sdd"})):
        r = client.post(path, headers=admin_auth, json=body)
        assert r.status_code == 409, (path, r.text)
    r = client.delete("/api/storage/buffers/CACHE-UUID", headers=admin_auth)
    assert r.status_code == 409 and f"{len(paths)} Datei" in r.json()["detail"]


def test_buffer_drive_unplugged_waits_and_moves(client, admin_auth, rig):
    from app import buffers, integrity, transfers
    from app.db import SessionLocal
    from app.models import (
        CloudProvider,
        MediaItem,
        MediaStatus,
        TransferJob,
        TransferStatus,
    )

    assert client.post("/api/storage/buffers", headers=admin_auth,
                       json={"device": "/dev/sdd1"}).status_code == 201
    folder = client.post("/api/folders", headers=admin_auth, json={"name": "U"}).json()
    media = _upload(client, admin_auth, folder["id"], "far.mp4", b"y" * 50)

    # Pull the drive: its mountpoint is gone.
    ssd, moved = rig["ssd"], rig["ssd"].parent / "SSD-sdd1"
    ssd.rename(moved)
    stored = _stored_paths(folder["id"])[0]
    assert buffers.is_offline(stored)
    # New files fall back to the box's own buffer meanwhile.
    _upload(client, admin_auth, folder["id"], "home.mp4", b"z" * 10)
    assert any("OffgridCloud-Puffer" not in p for p in _stored_paths(folder["id"]))

    with SessionLocal() as db:
        # The boot check must not quarantine files that are merely away.
        integrity.verify_local_media(db)
        assert db.get(MediaItem, media["id"]).local_deleted is False
        # A transfer waits instead of burning its attempts.
        provider = CloudProvider(name="t", type="s3", config_encrypted="")
        db.add(provider)
        db.flush()
        job = TransferJob(media_id=media["id"], provider_id=provider.id)
        db.add(job)
        db.commit()
        calls = []
        transfers.process_job(db, job, upload_fn=lambda *a: calls.append(a))
        assert job.status == TransferStatus.QUEUED and job.attempts == 0 and not calls
        db.delete(job)
        db.delete(provider)
        db.commit()

    # Plugged back in under another mountpoint: stored paths follow it.
    rig["lsblk"]["blockdevices"][-1]["children"][0]["mountpoint"] = str(moved)
    drives.clear_cache()
    overview = client.get("/api/storage", headers=admin_auth).json()
    assert overview["buffers"][0]["online"] is True
    stored = _stored_paths(folder["id"])
    assert any(p.startswith(str(moved) + "/OffgridCloud-Puffer/") for p in stored)
    assert all(Path(p).exists() for p in stored)
    with SessionLocal() as db:
        assert db.get(MediaItem, media["id"]).status != MediaStatus.FAILED


def test_buffer_drive_refuses_fat32(client, admin_auth, rig):
    rig["lsblk"]["blockdevices"][-1]["children"][0]["fstype"] = "vfat"
    r = client.post("/api/storage/buffers", headers=admin_auth, json={"device": "/dev/sdd1"})
    assert r.status_code == 409 and "exFAT" in r.json()["detail"]
    r = client.post("/api/storage/buffers", headers=admin_auth, json={"device": "/dev/mmcblk0p2"})
    assert r.status_code == 409
