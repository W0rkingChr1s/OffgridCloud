from sqlalchemy import select

from app.db import SessionLocal
from app.models import MediaItem, TransferJob, TransferStatus
from app.rclone import UploadResult
from app.transfers import process_job


def _provider(client, admin_auth, name="S3"):
    return client.post(
        "/api/providers",
        headers=admin_auth,
        json={
            "name": name,
            "type": "s3",
            "config": {"access_key_id": "a", "secret_access_key": "b"},
        },
    ).json()


def _folder(client, admin_auth, name="Tour"):
    return client.post("/api/folders", headers=admin_auth, json={"name": name}).json()


def _upload(client, auth, folder_id, filename, data: bytes):
    sess = client.post(
        f"/api/folders/{folder_id}/uploads",
        headers=auth,
        json={"filename": filename, "size": len(data)},
    ).json()
    client.put(
        f"/api/uploads/{sess['id']}",
        headers={**auth, "X-Offset": "0"},
        content=data,
    )
    return client.post(f"/api/uploads/{sess['id']}/complete", headers=auth).json()


def ok_fn(local, options, dest, bwlimit=0, on_progress=None):
    if on_progress:
        on_progress(999, 999, 500.0)
    return UploadResult(True, 999, "", 500.0)


def fail_fn(local, options, dest, bwlimit=0, on_progress=None):
    return UploadResult(False, 0, "boom")


def test_upload_after_link_enqueues_job(client, admin_auth):
    prov = _provider(client, admin_auth)
    folder = _folder(client, admin_auth)
    client.post(
        f"/api/folders/{folder['id']}/providers",
        headers=admin_auth,
        json={"provider_id": prov["id"], "dest_path": "bucket/tour"},
    )
    media = _upload(client, admin_auth, folder["id"], "a.mp4", b"hello")

    # Media should be queued, with one transfer job.
    assert media["status"] == "queued"
    transfers = client.get("/api/transfers", headers=admin_auth).json()
    assert len(transfers) == 1
    assert transfers[0]["media_id"] == media["id"]
    assert transfers[0]["status"] == "queued"
    assert transfers[0]["media_filename"] == "a.mp4"


def test_successful_job_marks_media_done(client, admin_auth):
    prov = _provider(client, admin_auth)
    folder = _folder(client, admin_auth)
    client.post(
        f"/api/folders/{folder['id']}/providers",
        headers=admin_auth,
        json={"provider_id": prov["id"]},
    )
    media = _upload(client, admin_auth, folder["id"], "b.mp4", b"data")

    with SessionLocal() as db:
        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        process_job(db, job, ok_fn)

    with SessionLocal() as db:
        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        assert job.status == TransferStatus.DONE
        assert job.progress == 1.0
        assert db.get(MediaItem, media["id"]).status == "done"


def test_failed_job_retries_then_fails(client, admin_auth):
    prov = _provider(client, admin_auth)
    folder = _folder(client, admin_auth)
    client.post(
        f"/api/folders/{folder['id']}/providers",
        headers=admin_auth,
        json={"provider_id": prov["id"]},
    )
    media = _upload(client, admin_auth, folder["id"], "c.mp4", b"data")

    # First failure -> requeued, attempts == 1.
    with SessionLocal() as db:
        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        process_job(db, job, fail_fn)
        assert job.status == TransferStatus.QUEUED
        assert job.attempts == 1
        assert job.last_error == "boom"

    # Exhaust attempts (max default 5) -> failed.
    for _ in range(5):
        with SessionLocal() as db:
            job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
            if job.status == TransferStatus.FAILED:
                break
            process_job(db, job, fail_fn)

    with SessionLocal() as db:
        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        assert job.status == TransferStatus.FAILED
        assert db.get(MediaItem, media["id"]).status == "failed"


def test_linking_provider_backfills_existing_media(client, admin_auth):
    folder = _folder(client, admin_auth)
    media = _upload(client, admin_auth, folder["id"], "old.mp4", b"old")
    assert media["status"] == "received"  # no providers yet

    prov = _provider(client, admin_auth)
    client.post(
        f"/api/folders/{folder['id']}/providers",
        headers=admin_auth,
        json={"provider_id": prov["id"]},
    )
    transfers = client.get("/api/transfers", headers=admin_auth).json()
    assert any(t["media_id"] == media["id"] for t in transfers)


def test_retry_endpoint_requeues_failed_job(client, admin_auth):

    prov = _provider(client, admin_auth)
    folder = _folder(client, admin_auth)
    client.post(
        f"/api/folders/{folder['id']}/providers",
        headers=admin_auth,
        json={"provider_id": prov["id"]},
    )
    media = _upload(client, admin_auth, folder["id"], "d.mp4", b"data")
    with SessionLocal() as db:
        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        for _ in range(5):
            job = db.get(TransferJob, job.id)
            if job.status == TransferStatus.FAILED:
                break
            process_job(db, job, fail_fn)
        job_id = job.id

    resp = client.post(f"/api/transfers/{job_id}/retry", headers=admin_auth)
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    assert resp.json()["attempts"] == 0


def test_priority_job_is_picked_first(client, admin_auth):
    from app.transfers import _pick_eligible

    p_low = _provider(client, admin_auth, "Low")
    p_high = _provider(client, admin_auth, "High")
    folder = _folder(client, admin_auth)
    client.post(
        f"/api/folders/{folder['id']}/providers",
        headers=admin_auth,
        json={"provider_id": p_low["id"], "priority": 0},
    )
    client.post(
        f"/api/folders/{folder['id']}/providers",
        headers=admin_auth,
        json={"provider_id": p_high["id"], "priority": 10},
    )
    _upload(client, admin_auth, folder["id"], "x.mp4", b"data")

    with SessionLocal() as db:
        job = _pick_eligible(db)
        assert job is not None
        assert job.provider_id == p_high["id"]  # higher priority wins


def test_two_providers_media_done_only_when_all_done(client, admin_auth):

    p1 = _provider(client, admin_auth, "P1")
    p2 = _provider(client, admin_auth, "P2")
    folder = _folder(client, admin_auth)
    for p in (p1, p2):
        client.post(
            f"/api/folders/{folder['id']}/providers",
            headers=admin_auth,
            json={"provider_id": p["id"]},
        )
    media = _upload(client, admin_auth, folder["id"], "multi.mp4", b"data")

    with SessionLocal() as db:
        jobs = list(db.scalars(select(TransferJob).where(TransferJob.media_id == media["id"])))
        assert len(jobs) == 2
        process_job(db, jobs[0], ok_fn)  # one done
        assert db.get(MediaItem, media["id"]).status == "queued"  # other still queued
        jobs = list(db.scalars(select(TransferJob).where(TransferJob.media_id == media["id"])))
        for j in jobs:
            if j.status == TransferStatus.QUEUED:
                process_job(db, j, ok_fn)
        assert db.get(MediaItem, media["id"]).status == "done"
