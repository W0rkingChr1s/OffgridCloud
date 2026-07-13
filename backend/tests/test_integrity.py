"""Power-loss hardening: startup reconcile of interrupted uploads + media."""

import os

from app.db import SessionLocal
from app.integrity import reconcile_upload_sessions, verify_local_media
from app.models import MediaItem, MediaStatus, UploadSession
from app.storage import uploads_tmp_dir


def _folder(client, admin_auth, name="Tour"):
    return client.post("/api/folders", headers=admin_auth, json={"name": name}).json()


def _open_session(client, auth, folder_id, filename, size):
    return client.post(
        f"/api/folders/{folder_id}/uploads",
        headers=auth,
        json={"filename": filename, "size": size},
    ).json()


def _put(client, auth, upload_id, data: bytes, offset=0):
    return client.put(
        f"/api/uploads/{upload_id}",
        headers={**auth, "X-Offset": str(offset)},
        content=data,
    )


def _upload(client, auth, folder_id, filename, data: bytes):
    sess = _open_session(client, auth, folder_id, filename, len(data))
    _put(client, auth, sess["id"], data)
    return client.post(f"/api/uploads/{sess['id']}/complete", headers=auth).json()


def test_torn_tail_is_truncated_to_committed_offset(client, admin_auth):
    """A crash after writing bytes but before committing leaves a longer file."""
    folder = _folder(client, admin_auth)
    sess = _open_session(client, admin_auth, folder["id"], "clip.mp4", 100)
    _put(client, admin_auth, sess["id"], b"0123456789")

    with SessionLocal() as db:
        row = db.get(UploadSession, sess["id"])
        assert row.received == 10
        # Simulate a torn tail written to disk but never committed to the DB.
        with open(row.temp_path, "ab") as fh:
            fh.write(b"GARBAGE-TAIL")
        assert os.path.getsize(row.temp_path) == 22

        result = reconcile_upload_sessions(db)

    assert result["truncated"] == 1
    with SessionLocal() as db:
        row = db.get(UploadSession, sess["id"])
        assert os.path.getsize(row.temp_path) == 10
        assert row.received == 10
    # The client can resume cleanly from the reported offset.
    status = client.get(f"/api/uploads/{sess['id']}", headers=admin_auth).json()
    assert status["received"] == 10


def test_lost_buffered_writes_rewind_offset(client, admin_auth):
    """If the disk lost bytes the DB thought were saved, trust the disk."""
    folder = _folder(client, admin_auth)
    sess = _open_session(client, admin_auth, folder["id"], "clip.mp4", 100)
    _put(client, admin_auth, sess["id"], b"0123456789")

    with SessionLocal() as db:
        row = db.get(UploadSession, sess["id"])
        with open(row.temp_path, "r+b") as fh:
            fh.truncate(4)  # only 4 bytes actually survived
        result = reconcile_upload_sessions(db)

    assert result["rewound"] == 1
    with SessionLocal() as db:
        assert db.get(UploadSession, sess["id"]).received == 4


def test_orphan_part_files_are_removed(client, admin_auth):
    stray = uploads_tmp_dir() / "deadbeef.part"
    stray.write_bytes(b"leftover")
    with SessionLocal() as db:
        result = reconcile_upload_sessions(db)
    assert result["orphans"] >= 1
    assert not stray.exists()


def test_missing_part_resets_session(client, admin_auth):
    folder = _folder(client, admin_auth)
    sess = _open_session(client, admin_auth, folder["id"], "clip.mp4", 100)
    _put(client, admin_auth, sess["id"], b"0123456789")
    with SessionLocal() as db:
        row = db.get(UploadSession, sess["id"])
        os.unlink(row.temp_path)
        result = reconcile_upload_sessions(db)
    assert result["missing"] == 1
    with SessionLocal() as db:
        row = db.get(UploadSession, sess["id"])
        assert row.received == 0
        assert os.path.exists(row.temp_path)


def test_corrupt_local_media_is_quarantined(client, admin_auth):
    folder = _folder(client, admin_auth)
    media = _upload(client, admin_auth, folder["id"], "clip.mp4", b"the-full-file")

    with SessionLocal() as db:
        row = db.get(MediaItem, media["id"])
        row.status = MediaStatus.QUEUED  # pending upload, local copy still matters
        db.commit()
        # Truncate the buffered copy as a power cut might.
        with open(row.stored_path, "r+b") as fh:
            fh.truncate(3)
        result = verify_local_media(db)

    assert result["corrupt"] == 1
    with SessionLocal() as db:
        row = db.get(MediaItem, media["id"])
        assert row.status == MediaStatus.FAILED
        assert row.local_deleted is True
    # A quarantined file is no longer downloadable.
    token = client.post(
        "/api/auth/login", json={"email": "admin@test.local", "password": "adminpass123"}
    ).json()["access_token"]
    resp = client.get(f"/api/media/{media['id']}/download?token={token}")
    assert resp.status_code == 410


def test_startup_repairs_torn_upload_on_next_boot(client, admin_auth):
    """End-to-end: a torn .part left by a power cut is fixed when the app boots.

    Simulates the real sequence — upload in progress, power lost mid-chunk, box
    powered back on — by spinning a fresh app instance (which runs the lifespan
    startup checks) and confirming the resume offset is byte-consistent again.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    folder = _folder(client, admin_auth)
    sess = _open_session(client, admin_auth, folder["id"], "clip.mp4", 100)
    _put(client, admin_auth, sess["id"], b"0123456789")

    with SessionLocal() as db:
        row = db.get(UploadSession, sess["id"])
        with open(row.temp_path, "ab") as fh:
            fh.write(b"TORN-TAIL-FROM-POWER-CUT")

    # "Reboot": a new app context re-runs lifespan -> run_startup_checks().
    with TestClient(app) as booted:
        status = booted.get(f"/api/uploads/{sess['id']}", headers=admin_auth).json()
        assert status["received"] == 10

    with SessionLocal() as db:
        row = db.get(UploadSession, sess["id"])
        assert os.path.getsize(row.temp_path) == 10


def test_intact_media_passes_verification(client, admin_auth):
    folder = _folder(client, admin_auth)
    media = _upload(client, admin_auth, folder["id"], "clip.mp4", b"intact-bytes")
    with SessionLocal() as db:
        verify_local_media(db)
        row = db.get(MediaItem, media["id"])
        assert row.local_deleted is False
        assert row.status != MediaStatus.FAILED
