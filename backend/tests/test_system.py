import os

from sqlalchemy import select

from app.db import SessionLocal
from app.models import MediaItem, TransferJob
from app.transfers import process_job
from tests.test_transfers import _folder, _provider, _upload, ok_fn


def test_system_status_reports_disk(client, admin_auth):
    body = client.get("/api/system", headers=admin_auth).json()
    assert body["delete_local_after_upload"] is False
    assert body["disk"]["total"] > 0
    assert "low_space" in body["disk"]
    assert "rclone_available" in body


def test_toggle_delete_local_and_audit(client, admin_auth):
    upd = client.put(
        "/api/system", headers=admin_auth, json={"delete_local_after_upload": True}
    ).json()
    assert upd["delete_local_after_upload"] is True

    # The toggle is audited.
    events = client.get("/api/system/audit", headers=admin_auth).json()
    assert any(e["action"] == "system.update" for e in events)


def test_audit_records_user_creation(client, admin_auth):
    client.post(
        "/api/users",
        headers=admin_auth,
        json={"email": "audit@test.local", "password": "userpass123"},
    )
    events = client.get("/api/system/audit", headers=admin_auth).json()
    create = [e for e in events if e["action"] == "user.create"]
    assert create and "audit@test.local" in create[0]["detail"]


def test_delete_local_after_upload_removes_file(client, admin_auth):
    # Enable the policy.
    client.put("/api/system", headers=admin_auth, json={"delete_local_after_upload": True})
    prov = _provider(client, admin_auth)
    folder = _folder(client, admin_auth)
    client.post(
        f"/api/folders/{folder['id']}/providers",
        headers=admin_auth,
        json={"provider_id": prov["id"]},
    )
    media = _upload(client, admin_auth, folder["id"], "gone.mp4", b"bytes")

    with SessionLocal() as db:
        path = db.get(MediaItem, media["id"]).stored_path
        assert os.path.exists(path)
        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        process_job(db, job, ok_fn)

    with SessionLocal() as db:
        m = db.get(MediaItem, media["id"])
        assert m.status == "done"
        assert m.local_deleted is True
    assert not os.path.exists(path)


def test_delete_local_disabled_keeps_file(client, admin_auth):
    # Explicitly disable (singleton persists across tests in the shared DB).
    client.put("/api/system", headers=admin_auth, json={"delete_local_after_upload": False})
    prov = _provider(client, admin_auth)
    folder = _folder(client, admin_auth)
    client.post(
        f"/api/folders/{folder['id']}/providers",
        headers=admin_auth,
        json={"provider_id": prov["id"]},
    )
    media = _upload(client, admin_auth, folder["id"], "keep.mp4", b"bytes")
    with SessionLocal() as db:
        path = db.get(MediaItem, media["id"]).stored_path
        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        process_job(db, job, ok_fn)
        assert db.get(MediaItem, media["id"]).local_deleted is False
    assert os.path.exists(path)
