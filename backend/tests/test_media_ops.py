"""Tests for media download/delete and the transfer reconciler."""

import os

from sqlalchemy import select

from app.db import SessionLocal
from app.models import MediaItem, TransferJob, TransferStatus
from app.rclone import DeleteResult
from app.transfers import delete_media, process_job, reconcile_once
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD
from tests.test_transfers import _folder, _provider, _upload, fail_fn, ok_fn


def _token(client) -> str:
    return client.post(
        "/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    ).json()["access_token"]


# --- Download -------------------------------------------------------------


def test_download_returns_original_bytes(client, admin_auth):
    folder = _folder(client, admin_auth)
    media = _upload(client, admin_auth, folder["id"], "note.txt", b"hello world")
    token = _token(client)

    resp = client.get(f"/api/media/{media['id']}/download?token={token}")
    assert resp.status_code == 200
    assert resp.content == b"hello world"
    assert "note.txt" in resp.headers.get("content-disposition", "")


def test_download_requires_valid_token(client, admin_auth):
    folder = _folder(client, admin_auth)
    media = _upload(client, admin_auth, folder["id"], "note.txt", b"hi")
    resp = client.get(f"/api/media/{media['id']}/download?token=bogus")
    assert resp.status_code == 401


# --- Delete ---------------------------------------------------------------


def test_delete_removes_local_file_and_row(client, admin_auth):
    folder = _folder(client, admin_auth)
    media = _upload(client, admin_auth, folder["id"], "gone.txt", b"bye")
    with SessionLocal() as db:
        path = db.get(MediaItem, media["id"]).stored_path
    assert os.path.exists(path)

    resp = client.delete(
        f"/api/folders/{folder['id']}/media/{media['id']}", headers=admin_auth
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert not os.path.exists(path)
    with SessionLocal() as db:
        assert db.get(MediaItem, media["id"]) is None


def test_delete_without_remote_setting_skips_remote(client, admin_auth):
    prov = _provider(client, admin_auth)
    folder = _folder(client, admin_auth)
    client.post(
        f"/api/folders/{folder['id']}/providers",
        headers=admin_auth,
        json={"provider_id": prov["id"]},
    )
    media = _upload(client, admin_auth, folder["id"], "r.txt", b"x")
    with SessionLocal() as db:
        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        process_job(db, job, ok_fn)  # mark uploaded

    calls: list = []

    def spy(options, dest):
        calls.append(dest)
        return DeleteResult(True, "ok")

    with SessionLocal() as db:
        summary = delete_media(db, media["id"], delete_fn=spy)
    assert summary["remote_attempted"] == 0
    assert calls == []  # remote deletion off by default


def test_delete_with_remote_setting_deletes_uploaded_copies(client, admin_auth):
    client.put(
        "/api/system", headers=admin_auth, json={"delete_remote_on_local_delete": True}
    )
    prov = _provider(client, admin_auth)
    folder = _folder(client, admin_auth)
    client.post(
        f"/api/folders/{folder['id']}/providers",
        headers=admin_auth,
        json={"provider_id": prov["id"], "dest_path": "bucket/x"},
    )
    media = _upload(client, admin_auth, folder["id"], "r.mp4", b"data")
    with SessionLocal() as db:
        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        process_job(db, job, ok_fn)

    seen: list = []

    def spy(options, dest):
        seen.append(dest)
        return DeleteResult(True, "ok")

    with SessionLocal() as db:
        summary = delete_media(db, media["id"], delete_fn=spy)
    assert summary["remote_attempted"] == 1
    assert summary["remote_deleted"] == 1
    assert seen == ["bucket/x/r.mp4"]


def test_delete_reports_remote_errors(client, admin_auth):
    client.put(
        "/api/system", headers=admin_auth, json={"delete_remote_on_local_delete": True}
    )
    prov = _provider(client, admin_auth)
    folder = _folder(client, admin_auth)
    client.post(
        f"/api/folders/{folder['id']}/providers",
        headers=admin_auth,
        json={"provider_id": prov["id"]},
    )
    media = _upload(client, admin_auth, folder["id"], "e.mp4", b"data")
    with SessionLocal() as db:
        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        process_job(db, job, ok_fn)

    with SessionLocal() as db:
        summary = delete_media(
            db, media["id"], delete_fn=lambda o, d: DeleteResult(False, "denied")
        )
    assert summary["deleted"] is True  # local delete still happens
    assert summary["remote_deleted"] == 0
    assert summary["remote_errors"] and "denied" in summary["remote_errors"][0]


# --- Reconcile ------------------------------------------------------------


def test_reconcile_requeues_failed_jobs(client, admin_auth):
    prov = _provider(client, admin_auth)
    folder = _folder(client, admin_auth)
    client.post(
        f"/api/folders/{folder['id']}/providers",
        headers=admin_auth,
        json={"provider_id": prov["id"]},
    )
    media = _upload(client, admin_auth, folder["id"], "f.mp4", b"data")

    # Drive the job to a terminal FAILED state.
    for _ in range(10):
        with SessionLocal() as db:
            job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
            if job.status == TransferStatus.FAILED:
                break
            process_job(db, job, fail_fn)

    with SessionLocal() as db:
        result = reconcile_once(db)
    assert result["requeued"] == 1

    with SessionLocal() as db:
        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        assert job.status == TransferStatus.QUEUED
        assert job.attempts == 0
        assert db.get(MediaItem, media["id"]).status == "queued"


def test_reconcile_backfills_missing_jobs(client, admin_auth):
    prov = _provider(client, admin_auth)
    folder = _folder(client, admin_auth)
    client.post(
        f"/api/folders/{folder['id']}/providers",
        headers=admin_auth,
        json={"provider_id": prov["id"]},
    )
    media = _upload(client, admin_auth, folder["id"], "b.mp4", b"data")

    # Simulate a lost job row (e.g. created before the link existed and pruned).
    with SessionLocal() as db:
        db.query(TransferJob).filter(TransferJob.media_id == media["id"]).delete()
        db.commit()
        assert db.scalar(
            select(TransferJob).where(TransferJob.media_id == media["id"])
        ) is None

    with SessionLocal() as db:
        result = reconcile_once(db)
    assert result["backfilled"] == 1
    with SessionLocal() as db:
        assert db.scalar(
            select(TransferJob).where(TransferJob.media_id == media["id"])
        ) is not None


def test_reconcile_skips_locally_deleted_media(client, admin_auth):
    prov = _provider(client, admin_auth)
    folder = _folder(client, admin_auth)
    client.post(
        f"/api/folders/{folder['id']}/providers",
        headers=admin_auth,
        json={"provider_id": prov["id"]},
    )
    media = _upload(client, admin_auth, folder["id"], "d.mp4", b"data")

    for _ in range(10):
        with SessionLocal() as db:
            job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
            if job.status == TransferStatus.FAILED:
                break
            process_job(db, job, fail_fn)

    # Mark the source as locally removed — it cannot be re-uploaded.
    with SessionLocal() as db:
        db.get(MediaItem, media["id"]).local_deleted = True
        db.commit()

    with SessionLocal() as db:
        result = reconcile_once(db)
    assert result["requeued"] == 0
    with SessionLocal() as db:
        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        assert job.status == TransferStatus.FAILED
