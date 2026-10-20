"""Priorities between boxes on a shared uplink (app.pool_priority)."""

import pytest
from sqlalchemy import delete

import app.pool as pool_core
from app import bandwidth, notify, pool_priority, pool_watch
from app.admin_ops import get_system_settings
from app.db import SessionLocal
from app.models import PoolPeer
from app.pool_priority import Node, decide


def _n(key, level, pending=1, paused=False, owned=False, reachable=True):
    return Node(key=key, name=key.upper(), level=level, reachable=reachable,
                paused=paused, pending=pending, owned=owned)


def _actions(decisions):
    return {d.key: d.action for d in decisions}


# --- decide(): the rule ----------------------------------------------------------


def test_higher_rank_with_work_pauses_lower_ranks_with_work():
    nodes = [_n("a", 1), _n("b", 2), _n("c", 3), _n("idle", 3, pending=0), _n("free", 0)]
    decisions, _ = decide(nodes, set())
    assert _actions(decisions) == {"b": "pause", "c": "pause"}
    assert all(d.reason == "Vorrang für A" for d in decisions)


def test_nothing_happens_without_competition():
    assert decide([_n("a", 1, pending=0), _n("b", 2)], set())[0] == []
    assert decide([_n("a", 1, reachable=False), _n("b", 2)], set())[0] == []


def test_line_is_handed_down_rank_by_rank():
    # A is done: B and C (both paused by us) get the line back …
    nodes = [_n("a", 1, pending=0), _n("b", 2, paused=True, owned=True),
             _n("c", 3, paused=True, owned=True)]
    assert _actions(decide(nodes, set())[0]) == {"b": "resume", "c": "resume"}
    # … next round B claims it, so C waits again.
    nodes = [_n("a", 1, pending=0), _n("b", 2), _n("c", 3)]
    assert _actions(decide(nodes, set())[0]) == {"c": "pause"}


def test_manual_pause_is_never_undone():
    nodes = [_n("a", 1, pending=0), _n("b", 2, paused=True, owned=False)]
    assert decide(nodes, set())[0] == []
    # A paused-by-hand box with work doesn't claim the line either.
    nodes = [_n("a", 1, paused=True), _n("b", 2)]
    assert decide(nodes, set())[0] == []


def test_hand_resume_is_respected_until_the_rule_lets_go():
    nodes = [_n("a", 1), _n("b", 2, paused=False, owned=True)]
    decisions, overrides = decide(nodes, set())
    assert _actions(decisions) == {"b": "release"} and overrides == {"b"}
    # Still competing: no new pause for B.
    decisions, overrides = decide([_n("a", 1), _n("b", 2)], overrides)
    assert decisions == [] and overrides == {"b"}
    # A done: override ends, later competition pauses B again.
    _, overrides = decide([_n("a", 1, pending=0), _n("b", 2)], overrides)
    assert overrides == set()
    assert _actions(decide([_n("a", 1), _n("b", 2)], overrides)[0]) == {"b": "pause"}


def test_box_taken_out_of_the_ranking_gets_its_uploads_back():
    nodes = [_n("a", 1), _n("b", 0, paused=True, owned=True)]
    assert _actions(decide(nodes, set())[0]) == {"b": "resume"}


# --- apply_round(): acting on the hub and its peers -------------------------------


@pytest.fixture
def hub(client, monkeypatch):
    """Hub ("self") plus one peer, priorities on; transport faked."""
    pool_priority.reset_state()
    pool_watch.reset_state()
    with SessionLocal() as db:
        db.execute(delete(PoolPeer))
        db.add(PoolPeer(name="Drohne", base_url="http://10.0.0.3:8000", priority=1))
        row = get_system_settings(db)
        row.pool_priority_enabled, row.pool_priority_self = True, 2
        db.commit()
    own = {"pending": 3}
    real_status = pool_core.node_status

    def node_status(db, control_enabled=False):
        status = real_status(db, control_enabled)
        status["pending_transfers"] = own["pending"]
        return status

    monkeypatch.setattr(pool_core, "node_status", node_status)
    monkeypatch.setattr(pool_core, "node_name", lambda: "ELW")
    sent = []
    monkeypatch.setattr(pool_core, "control_peer", lambda p, a: sent.append((p.name, a)) or {})
    yield own, sent
    pool_priority.reset_state()
    pool_watch.reset_state()
    with SessionLocal() as db:
        db.execute(delete(PoolPeer))
        row = get_system_settings(db)
        row.pool_priority_enabled, row.pool_priority_self = False, 0
        row.pool_priority_paused = False
        bandwidth.get_policy(db).uploads_paused = False
        db.commit()


def _round(drohne_status):
    with SessionLocal() as db:
        peers = list(db.query(PoolPeer))
        return pool_priority.apply_round(db, peers, {peers[0].id: drohne_status})


def _hub_state():
    with SessionLocal() as db:
        row = get_system_settings(db)
        return bandwidth.get_policy(db).uploads_paused, row.pool_priority_paused


BUSY = {"reachable": True, "uploads_paused": False, "pending_transfers": 4}
IDLE = {"reachable": True, "uploads_paused": False, "pending_transfers": 0}


def test_hub_pauses_itself_for_a_higher_ranked_peer_and_resumes(hub):
    lines = _round(BUSY)
    assert [line for _, line, _ in lines] == ["⏸ ELW: Uploads pausiert – Vorrang für Drohne"]
    assert _hub_state() == (True, True)
    assert _round(BUSY) == []  # stable
    lines = _round(IDLE)
    assert [line for _, line, _ in lines] == ["▶️ ELW: Uploads fortgesetzt – Leitung wieder frei"]
    assert _hub_state() == (False, False)


def test_peer_is_paused_through_its_token(hub):
    own, sent = hub
    with SessionLocal() as db:
        db.query(PoolPeer).update({"priority": 3})
        db.commit()
    _round(BUSY)
    assert sent == [("Drohne", "pause")]
    with SessionLocal() as db:
        assert db.query(PoolPeer).one().priority_paused is True
    own["pending"] = 0  # hub done
    _round({**BUSY, "uploads_paused": True})
    assert sent[-1] == ("Drohne", "resume")


def test_missing_control_right_is_reported_once(hub, monkeypatch):
    own, _ = hub
    with SessionLocal() as db:
        db.query(PoolPeer).update({"priority": 3})
        db.commit()

    def refuse(peer, action):
        raise pool_core.PeerControlError("Token dieses Hubs hat auf dem Peer nur Leserechte")

    monkeypatch.setattr(pool_core, "control_peer", refuse)
    first = _round(BUSY)
    assert len(first) == 1 and "Vorrang nicht anwendbar" in first[0][1]
    assert _round(BUSY) == []  # not every minute


def test_switching_off_returns_what_the_rule_paused(hub):
    _round(BUSY)
    assert _hub_state() == (True, True)
    with SessionLocal() as db:
        get_system_settings(db).pool_priority_enabled = False
        db.commit()
    lines = _round(BUSY)
    assert lines[0][1].endswith("Uploads fortgesetzt – Vorrang ausgeschaltet")
    assert _hub_state() == (False, False)


def test_watch_round_reports_a_priority_pause_once(hub, monkeypatch):
    own, _ = hub
    with SessionLocal() as db:
        db.query(PoolPeer).update({"priority": 3})
        get_system_settings(db).pool_priority_self = 1
        db.commit()
    world = {"Drohne": dict(BUSY)}
    monkeypatch.setattr(pool_core, "poll_peer", lambda p: world[p.name])
    sent = []
    monkeypatch.setattr(notify, "dispatch", lambda s, e, t, m, p: sent.append(m))
    with SessionLocal() as db:
        lines = pool_watch.check_once(db)
        assert lines == ["⏸ Drohne: Uploads pausiert – Vorrang für ELW"]
        world["Drohne"] = {**BUSY, "uploads_paused": True}  # the pause took effect
        assert pool_watch.check_once(db) == []  # no second, generic "pausiert"
    assert len(sent) == 1


# --- API ------------------------------------------------------------------------


def test_priority_api_and_overview(hub, client, admin_auth):
    out = client.get("/api/pool/priority", headers=admin_auth).json()
    assert out["enabled"] is True
    levels = {n["name"]: n["level"] for n in out["nodes"]}
    assert levels["Drohne"] == 1 and out["nodes"][0]["peer_id"] is None
    peer_id = out["nodes"][1]["peer_id"]
    resp = client.put(
        "/api/pool/priority", headers=admin_auth,
        json={"enabled": False, "nodes": [{"peer_id": None, "level": 1},
                                          {"peer_id": peer_id, "level": 3}]},
    )
    assert resp.status_code == 200 and resp.json()["enabled"] is False
    assert [n["level"] for n in resp.json()["nodes"]] == [1, 3]
    bad = client.put("/api/pool/priority", headers=admin_auth,
                     json={"enabled": True, "nodes": [{"peer_id": None, "level": 7}]})
    assert bad.status_code == 422
    overview = client.get("/api/pool/overview", headers=admin_auth).json()
    assert overview["self"]["priority"] == 1
    assert "pending_transfers" in overview["self"]
