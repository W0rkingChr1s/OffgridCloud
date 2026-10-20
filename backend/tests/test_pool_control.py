"""Transfer remote control: local pause/resume/retry and the opt-in pool path."""

import io
import urllib.error

import pytest
from sqlalchemy import select

import app.pool as pool_core
from app import bandwidth, transfers
from app.admin_ops import get_system_settings
from app.db import SessionLocal
from app.models import AuditEvent, TransferJob, TransferStatus
from tests.test_folders import _auth, _make_user
from tests.test_parallel import only_these_jobs  # noqa: F401 (fixture)


@pytest.fixture(autouse=True)
def _restore_control_state():
    """The DB is shared across tests: leave uploads running and control off."""
    yield
    with SessionLocal() as db:
        bandwidth.get_policy(db).uploads_paused = False
        row = get_system_settings(db)
        row.pool_control_enabled = False
        row.pool_token = ""
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
    assert client.put(
        "/api/pool/self", headers=user_auth, json={"control_enabled": True}
    ).status_code == 403


def test_pool_token_controls_only_when_allowed(client, admin_auth):
    token = client.post("/api/pool/token", headers=admin_auth).json()["pool_token"]
    hub = {"X-Pool-Token": token, "X-Pool-Hub": "box1-elw"}

    # Token alone grants read access, not control.
    assert client.get("/api/pool/status", headers=hub).json()["control_enabled"] is False
    resp = _control(client, hub, "pause", "/api/pool/control")
    assert resp.status_code == 403
    assert "nicht freigegeben" in resp.json()["detail"]

    me = client.put("/api/pool/self", headers=admin_auth, json={"control_enabled": True}).json()
    assert me["control_enabled"] is True
    resp = _control(client, hub, "pause", "/api/pool/control")
    assert resp.status_code == 200
    assert resp.json()["uploads_paused"] is True
    assert client.get("/api/pool/status", headers=hub).json()["uploads_paused"] is True
    event = _last_audit()
    assert event.action == "pool.remote_pause"
    assert "box1-elw" in event.detail

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
    assert "nicht freigegeben" in resp.json()["detail"]

    client.patch(f"/api/pool/peers/{peer['id']}", headers=admin_auth, json={"enabled": False})
    resp = _control(client, admin_auth, "pause", f"/api/pool/peers/{peer['id']}/control")
    assert resp.status_code == 409
    client.delete(f"/api/pool/peers/{peer['id']}", headers=admin_auth)


def test_hub_name_header_is_ascii(monkeypatch):
    from app.models import PoolPeer

    monkeypatch.setattr(pool_core, "_node_name", lambda: "Box – Ölwehr")
    headers = pool_core._peer_headers(PoolPeer(name="p", base_url="http://x", token_encrypted=""))
    headers["X-Pool-Hub"].encode("ascii")  # must not raise
