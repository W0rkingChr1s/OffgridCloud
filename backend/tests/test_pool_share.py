"""Fair share of one uplink between pool boxes (app.pool_share + peer side)."""

from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select

import app.pool as pool_core
from app import bandwidth, pool_priority, pool_share, pool_watch, transfers
from app.admin_ops import get_system_settings
from app.db import SessionLocal
from app.models import AuditEvent, PoolAccessToken, PoolPeer
from app.pool_priority import Node
from app.pool_share import MIN_SHARE_KBPS, shares
from tests.test_parallel import only_these_jobs  # noqa: F401 (fixture)


def _n(key, level=1, pending=1, paused=False, reachable=True):
    return Node(key=key, name=key, level=level, reachable=reachable,
                paused=paused, pending=pending, owned=False)


# --- the split ---------------------------------------------------------------------


def test_senders_split_the_line_newcomers_get_their_would_be_share():
    out = shares([_n("a"), _n("b"), _n("idle", pending=0), _n("other", level=0)], 1200)
    assert out == {"a": 600, "b": 600, "idle": 400}  # "other" isn't on the line


def test_paused_and_unreachable_boxes_do_not_count_as_senders():
    out = shares([_n("a"), _n("held", paused=True), _n("gone", reachable=False)], 1000)
    assert out == {"a": 1000, "held": 500}


def test_lone_or_idle_line_and_tiny_shares():
    assert shares([_n("a", pending=0), _n("b", pending=0)], 900) == {"a": 900, "b": 900}
    assert shares([_n("a"), _n("b")], 0) == {}
    assert shares([_n(str(i)) for i in range(10)], 50)["0"] == MIN_SHARE_KBPS


# --- peer side: /api/pool/limit and the worker ---------------------------------


@pytest.fixture
def clean(client):
    yield
    pool_share.reset_state()
    pool_priority.reset_state()
    pool_watch.reset_state()
    with SessionLocal() as db:
        db.execute(delete(PoolAccessToken))
        db.execute(delete(PoolPeer))
        policy = bandwidth.get_policy(db)
        policy.pool_limit_kbps, policy.pool_limit_until, policy.pool_limit_by = 0, None, ""
        policy.bwlimit_kbps = 0
        row = get_system_settings(db)
        row.pool_share_enabled, row.pool_share_kbps = False, 0
        row.pool_priority_enabled, row.pool_priority_self = False, 0
        db.commit()


def _hub_headers(client, admin_auth, scope):
    tok = client.post("/api/pool/tokens", headers=admin_auth,
                      json={"name": "ELW", "scope": scope}).json()["token"]
    return {"X-Pool-Token": tok, "X-Pool-Hub": "elw-pi"}


def _limit_audits():
    with SessionLocal() as db:
        return db.scalar(
            select(func.count(AuditEvent.id)).where(AuditEvent.action == "pool.remote_limit")
        )


def test_hub_sets_a_share_only_with_control_and_renewals_stay_quiet(clean, client, admin_auth):
    read = _hub_headers(client, admin_auth, "read")
    assert client.post("/api/pool/limit", headers=read, json={"kbps": 300}).status_code == 403

    control = _hub_headers(client, admin_auth, "control")
    before = _limit_audits()
    out = client.post("/api/pool/limit", headers=control, json={"kbps": 300}).json()
    assert out == {"pool_limit_kbps": 300, "changed": True}
    out = client.post("/api/pool/limit", headers=control, json={"kbps": 300}).json()
    assert out["changed"] is False
    assert _limit_audits() == before + 1  # the renewal is not audited

    bw = client.get("/api/bandwidth", headers=admin_auth).json()
    assert bw["pool_limit_kbps"] == 300 and "ELW" in bw["pool_limit_by"]
    status = client.get("/api/pool/status", headers=control).json()
    assert status["pool_limit_kbps"] == 300

    assert client.post("/api/pool/limit", headers=control, json={"kbps": 0}).json()[
        "pool_limit_kbps"
    ] == 0


def test_share_lapses_without_renewal(clean):
    with SessionLocal() as db:
        now = transfers._utcnow_naive()
        bandwidth.set_pool_limit(db, 300, "hub", now)
        policy = bandwidth.get_policy(db)
        assert bandwidth.active_pool_limit(policy, now) == 300
        later = now + bandwidth.POOL_LIMIT_TTL + timedelta(seconds=1)
        assert bandwidth.active_pool_limit(policy, later) == 0


def test_combine_limits():
    assert bandwidth.combine_limits(0, 0) == 0
    assert bandwidth.combine_limits(1000, 0) == 1000
    assert bandwidth.combine_limits(0, 300) == 300
    assert bandwidth.combine_limits(1000, 300) == 300
    assert bandwidth.combine_limits(200, 300) == 200


def test_worker_uploads_with_the_lower_limit(clean, client, admin_auth, monkeypatch,
                                             only_these_jobs):  # noqa: F811
    seen = []
    monkeypatch.setattr(transfers, "process_job",
                        lambda db, job, **kw: seen.append(kw["bwlimit_kbps"]))
    with SessionLocal() as db:
        bandwidth.get_policy(db).bwlimit_kbps = 1000
        db.commit()
        bandwidth.set_pool_limit(db, 300, "hub", transfers._utcnow_naive())
    only_these_jobs(client, admin_auth, 16)
    assert transfers.process_one() is True
    assert seen == [300]


# --- hub side: one round ----------------------------------------------------------


@pytest.fixture
def hub(clean, monkeypatch):
    with SessionLocal() as db:
        db.add(PoolPeer(name="Drohne", base_url="http://10.0.0.3:8000", priority=1))
        row = get_system_settings(db)
        row.pool_priority_self = 1
        row.pool_share_enabled, row.pool_share_kbps = True, 1000
        db.commit()
    own = {"pending": 2}
    real_status = pool_core.node_status

    def node_status(db, control_enabled=False):
        status = real_status(db, control_enabled)
        status["pending_transfers"] = own["pending"]
        return status

    monkeypatch.setattr(pool_core, "node_status", node_status)
    monkeypatch.setattr(pool_core, "node_name", lambda: "ELW")
    sent = []
    monkeypatch.setattr(pool_core, "limit_peer", lambda p, k: sent.append((p.name, k)) or {})
    return own, sent


BUSY = {"reachable": True, "uploads_paused": False, "pending_transfers": 3}


def _watch(world, monkeypatch):
    monkeypatch.setattr(pool_core, "poll_peer", lambda p: world[p.name])
    with SessionLocal() as db:
        return pool_watch.check_once(db)


def _own_limit():
    with SessionLocal() as db:
        return bandwidth.active_pool_limit(bandwidth.get_policy(db), transfers._utcnow_naive())


def test_round_splits_between_hub_and_peer_and_withdraws_when_off(hub, monkeypatch):
    own, sent = hub
    assert _watch({"Drohne": BUSY}, monkeypatch) == []  # sharing is silent
    assert sent == [("Drohne", 500)] and _own_limit() == 500

    own["pending"] = 0  # hub idle: the drone gets the line, the hub its would-be share
    _watch({"Drohne": BUSY}, monkeypatch)
    assert sent[-1] == ("Drohne", 1000) and _own_limit() == 500

    with SessionLocal() as db:
        get_system_settings(db).pool_share_enabled = False
        db.commit()
    _watch({"Drohne": BUSY}, monkeypatch)
    assert sent[-1] == ("Drohne", 0) and _own_limit() == 0


def test_box_paused_by_priority_does_not_eat_a_share(hub, monkeypatch):
    own, sent = hub
    monkeypatch.setattr(pool_core, "control_peer", lambda p, a: {})
    with SessionLocal() as db:
        db.query(PoolPeer).update({"priority": 3})
        get_system_settings(db).pool_priority_enabled = True
        db.commit()
    lines = _watch({"Drohne": BUSY}, monkeypatch)
    assert lines == ["⏸ Drohne: Uploads pausiert – Vorrang für ELW"]
    assert _own_limit() == 1000  # the hub sends alone
    assert sent == [("Drohne", 500)]  # the paused drone holds its would-be share


def test_missing_control_right_is_reported_once(hub, monkeypatch):
    def refuse(peer, kbps):
        raise pool_core.PeerControlError("Token dieses Hubs hat auf dem Peer nur Leserechte")

    monkeypatch.setattr(pool_core, "limit_peer", refuse)
    first = _watch({"Drohne": BUSY}, monkeypatch)
    assert first == ["⚠️ Drohne: Leitungsanteil nicht setzbar – "
                     "Token dieses Hubs hat auf dem Peer nur Leserechte"]
    assert _watch({"Drohne": BUSY}, monkeypatch) == []


def test_api_needs_a_capacity(clean, client, admin_auth):
    bad = client.put("/api/pool/priority", headers=admin_auth,
                     json={"enabled": False, "share_enabled": True, "share_kbps": 0})
    assert bad.status_code == 422
    ok = client.put("/api/pool/priority", headers=admin_auth,
                    json={"enabled": False, "share_enabled": True, "share_kbps": 2500}).json()
    assert ok["share_enabled"] is True and ok["share_kbps"] == 2500
