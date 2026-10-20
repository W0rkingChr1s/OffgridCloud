"""Transfer remote control: local pause/resume/retry and the per-hub token path."""

import io
import urllib.error

import pytest
from sqlalchemy import delete, select

import app.pool as pool_core
from app import bandwidth, transfers
from app.db import SessionLocal
from app.models import AuditEvent, PoolAccessToken, TransferJob, TransferStatus
from tests.test_folders import _auth, _make_user
from tests.test_parallel import only_these_jobs  # noqa: F401 (fixture)


@pytest.fixture(autouse=True)
def _restore_control_state():
    """The DB is shared across tests: leave uploads running and no hub tokens."""
    yield
    with SessionLocal() as db:
        bandwidth.get_policy(db).uploads_paused = False
        db.execute(delete(PoolAccessToken))
        db.commit()


def _control(client, headers, action, path="/api/transfers/control"):
    return client.post(path, headers=headers, json={"action": action})


def _fail(job_id: int) -> None:
    with SessionLocal() as db:
        job = db.get(TransferJob, job_id)
        job.status = TransferStatus.FAILED
        job.attempts = 5
        job.last_error = "boom"
        db.commit()


def _last_audit() -> AuditEvent:
    with SessionLocal() as db:
        return db.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))


def test_pause_stops_new_uploads_until_resumed(
    client, admin_auth, monkeypatch, only_these_jobs  # noqa: F811
):
    job_id = only_these_jobs(client, admin_auth, 64)
    started = []
    monkeypatch.setattr(transfers, "process_job", lambda db, job, **kw: started.append(job.id))

    resp = _control(client, admin_auth, "pause")
    assert resp.status_code == 200
    assert resp.json()["uploads_paused"] is True
    assert transfers.process_one() is False
    assert started == []
    bw = client.get("/api/bandwidth", headers=admin_auth).json()
    assert bw["uploads_paused"] is True
    assert bw["gated"] is True and "manuell" in bw["gate_reason"]

    assert _control(client, admin_auth, "resume").json()["uploads_paused"] is False
    assert transfers.process_one() is True
    assert started == [job_id]


def test_retry_failed_requeues_every_failed_job(
    client, admin_auth, only_these_jobs  # noqa: F811
):
    ids = [only_these_jobs(client, admin_auth, 16) for _ in range(2)]
    for job_id in ids:
        _fail(job_id)
    before = client.get("/api/transfers/control", headers=admin_auth).json()
    assert before["failed_transfers"] >= 2

    out = _control(client, admin_auth, "retry_failed").json()
    assert out["failed_transfers"] == 0
    assert "neu eingereiht" in out["message"]
    with SessionLocal() as db:
        for job_id in ids:
            job = db.get(TransferJob, job_id)
            assert job.status == TransferStatus.QUEUED
            assert job.attempts == 0 and job.last_error == ""


def test_unknown_action_is_rejected(client, admin_auth):
    assert _control(client, admin_auth, "delete_everything").status_code == 422


def test_control_requires_admin(client, admin_auth):
    _make_user(client, admin_auth, "ctl@test.local")
    user_auth = _auth(client, "ctl@test.local")
    assert _control(client, user_auth, "pause").status_code == 403
    assert client.post(
        "/api/pool/tokens", headers=user_auth, json={"name": "x", "scope": "control"}
    ).status_code == 403


def test_only_control_tokens_may_control(client, admin_auth):
    created = client.post(
        "/api/pool/tokens", headers=admin_auth, json={"name": "ELW"}
    ).json()
    assert created["scope"] == "read"  # least privilege by default
    hub = {"X-Pool-Token": created["token"], "X-Pool-Hub": "box1-elw"}

    # A read token sees the status but may not control.
    assert client.get("/api/pool/status", headers=hub).json()["control_enabled"] is False
    resp = _control(client, hub, "pause", "/api/pool/control")
    assert resp.status_code == 403
    assert "nur lesen" in resp.json()["detail"]

    # Upgrading the scope takes effect on the very next request.
    client.patch(
        f"/api/pool/tokens/{created['id']}", headers=admin_auth, json={"scope": "control"}
    )
    assert client.get("/api/pool/status", headers=hub).json()["control_enabled"] is True
    resp = _control(client, hub, "pause", "/api/pool/control")
    assert resp.status_code == 200
    assert resp.json()["uploads_paused"] is True
    event = _last_audit()
    assert event.action == "pool.remote_pause"
    assert "ELW" in event.detail and "box1-elw" in event.detail

    # A wrong token never gets through, permission or not.
    bad = {"X-Pool-Token": "nope"}
    assert _control(client, bad, "resume", "/api/pool/control").status_code == 401


def _add_peer(client, admin_auth, **extra):
    return client.post(
        "/api/pool/peers",
        headers=admin_auth,
        json={"name": "Box2", "base_url": "http://box2.local:8000", "token": "t", **extra},
    ).json()


def test_hub_forwards_action_to_peer(client, admin_auth, monkeypatch):
    peer = _add_peer(client, admin_auth)
    sent = []

    def fake_control(p, action):
        sent.append((p.name, action))
        return {"uploads_paused": True, "failed_transfers": 0, "message": "Uploads pausiert"}

    monkeypatch.setattr(pool_core, "control_peer", fake_control)
    resp = _control(client, admin_auth, "pause", f"/api/pool/peers/{peer['id']}/control")
    assert resp.status_code == 200
    assert resp.json()["uploads_paused"] is True
    assert sent == [("Box2", "pause")]
    assert _last_audit().action == "pool.peer_pause"
    client.delete(f"/api/pool/peers/{peer['id']}", headers=admin_auth)


def test_hub_explains_refused_control(client, admin_auth, monkeypatch):
    peer = _add_peer(client, admin_auth)

    def refuse(req, timeout):
        assert req.get_method() == "POST"
        assert req.get_header("X-pool-token") == "t"
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b""))

    monkeypatch.setattr(pool_core.urllib.request, "urlopen", refuse)
    resp = _control(client, admin_auth, "pause", f"/api/pool/peers/{peer['id']}/control")
    assert resp.status_code == 409
    assert "Leserechte" in resp.json()["detail"]

    client.patch(f"/api/pool/peers/{peer['id']}", headers=admin_auth, json={"enabled": False})
    resp = _control(client, admin_auth, "pause", f"/api/pool/peers/{peer['id']}/control")
    assert resp.status_code == 409
    client.delete(f"/api/pool/peers/{peer['id']}", headers=admin_auth)


def test_hub_name_header_is_ascii(monkeypatch):
    from app.models import PoolPeer

    monkeypatch.setattr(pool_core, "node_name", lambda: "Box – Ölwehr")
    headers = pool_core._peer_headers(PoolPeer(name="p", base_url="http://x", token_encrypted=""))
    headers["X-Pool-Hub"].encode("ascii")  # must not raise


# --- Job list of a peer ------------------------------------------------------


def _hub(client, admin_auth, scope):
    created = client.post(
        "/api/pool/tokens", headers=admin_auth, json={"name": f"Hub-{scope}", "scope": scope}
    ).json()
    return {"X-Pool-Token": created["token"], "X-Pool-Hub": "hub-pi"}


def test_read_token_lists_jobs_with_details(client, admin_auth, only_these_jobs):  # noqa: F811
    job_id = only_these_jobs(client, admin_auth, 32)
    _fail(job_id)
    hub = _hub(client, admin_auth, "read")

    assert client.get("/api/pool/jobs").status_code == 401
    failed = client.get("/api/pool/jobs?status=failed", headers=hub).json()
    mine = next(j for j in failed if j["id"] == job_id)
    assert mine["last_error"] == "boom" and mine["media_filename"]
    assert all(j["status"] == "failed" for j in failed)
    queued = client.get("/api/pool/jobs?status=queued", headers=hub).json()
    assert job_id not in {j["id"] for j in queued}


def test_retrying_a_single_job_needs_control(client, admin_auth, only_these_jobs):  # noqa: F811
    job_id = only_these_jobs(client, admin_auth, 32)
    _fail(job_id)
    path = f"/api/pool/jobs/{job_id}/retry"

    resp = client.post(path, headers=_hub(client, admin_auth, "read"))
    assert resp.status_code == 403

    resp = client.post(path, headers=_hub(client, admin_auth, "control"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued" and resp.json()["attempts"] == 0
    event = _last_audit()
    assert event.action == "pool.remote_retry_job"
    assert "Hub-control" in event.detail

    control = _hub(client, admin_auth, "control")
    assert client.post("/api/pool/jobs/999999/retry", headers=control).status_code == 404
    with SessionLocal() as db:
        db.get(TransferJob, job_id).status = TransferStatus.RUNNING
        db.commit()
    resp = client.post(path, headers=control)
    assert resp.status_code == 400 and "läuft" in resp.json()["detail"]
    _fail(job_id)  # leave it in a state other tests don't trip over


def _job(**extra):
    return {
        "id": 7, "media_id": 1, "provider_id": 1, "status": "failed", "progress": 0.0,
        "bytes_transferred": 0, "attempts": 5, "last_error": "boom",
        "created_at": "2026-10-20T10:00:00", "updated_at": "2026-10-20T10:00:00",
        "media_filename": "drohne.mp4", "provider_name": "S3", "folder_id": 1, **extra,
    }


def test_hub_lists_and_retries_peer_jobs(client, admin_auth, monkeypatch):
    peer = _add_peer(client, admin_auth)
    calls = []
    monkeypatch.setattr(
        pool_core, "peer_jobs", lambda p, status: calls.append(("list", status)) or [_job()]
    )
    monkeypatch.setattr(
        pool_core,
        "retry_peer_job",
        lambda p, job_id: calls.append(("retry", job_id)) or _job(status="queued", attempts=0),
    )
    jobs = client.get(f"/api/pool/peers/{peer['id']}/jobs?status=failed", headers=admin_auth)
    assert jobs.status_code == 200
    assert jobs.json()[0]["media_filename"] == "drohne.mp4"
    resp = client.post(f"/api/pool/peers/{peer['id']}/jobs/7/retry", headers=admin_auth)
    assert resp.json()["status"] == "queued"
    assert calls == [("list", "failed"), ("retry", 7)]
    assert _last_audit().action == "pool.peer_retry_job"
    client.delete(f"/api/pool/peers/{peer['id']}", headers=admin_auth)


@pytest.mark.parametrize(
    ("code", "body", "hub_status", "text"),
    [
        (404, b'{"detail":"Job not found"}', 404, "nicht (mehr) vorhanden"),
        (404, b'{"detail":"Not Found"}', 502, "Update"),
        (400, '{"detail":"Job läuft bereits"}'.encode(), 409, "läuft bereits"),
        (403, b"", 409, "Leserechte"),
    ],
)
def test_hub_explains_peer_job_errors(
    client, admin_auth, monkeypatch, code, body, hub_status, text
):
    peer = _add_peer(client, admin_auth)

    def refuse(req, timeout):
        raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(body))

    monkeypatch.setattr(pool_core.urllib.request, "urlopen", refuse)
    resp = client.post(f"/api/pool/peers/{peer['id']}/jobs/7/retry", headers=admin_auth)
    assert resp.status_code == hub_status
    assert text in resp.json()["detail"]
    client.delete(f"/api/pool/peers/{peer['id']}", headers=admin_auth)
