"""Pool discovery (mDNS list) and pairing by code."""

import io
import urllib.error

import pytest
from sqlalchemy import delete, select

import app.pool as pool_core
from app.admin_ops import get_system_settings
from app.crypto import decrypt
from app.db import SessionLocal
from app.discovery import discovery
from app.models import PoolAccessToken, PoolPeer
from tests.test_folders import _auth, _make_user


@pytest.fixture(autouse=True)
def _clean(client):  # client: the app (and its tables) must be up for the cleanup
    yield
    pool_core.pairing.clear()
    discovery.forget_all()
    with SessionLocal() as db:
        db.execute(delete(PoolAccessToken))
        db.execute(delete(PoolPeer))
        get_system_settings(db).pool_announce = True
        db.commit()


def _own_id() -> str:
    with SessionLocal() as db:
        return get_system_settings(db).node_id


# --- The code itself ----------------------------------------------------------


def test_code_dies_after_five_wrong_guesses():
    code = pool_core.PairingCode()
    real = code.create()
    assert len(real) == 6 and real.isdigit()
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(4):
        assert code.redeem(wrong) == "wrong"
    assert code.current() is not None
    assert code.redeem(wrong) == "wrong"
    assert code.current() is None
    assert code.redeem(real) == "none"  # burnt, even the right one


def test_code_is_single_use_and_expires():
    clock = [1000.0]
    code = pool_core.PairingCode(now=lambda: clock[0])
    real = code.create()
    assert code.redeem(real) == "ok"
    assert code.redeem(real) == "none"

    real = code.create()
    clock[0] += pool_core.PAIRING_TTL_SECONDS + 1
    assert code.current() is None
    assert code.redeem(real) == "none"


# --- Peer side: /api/pool/pair -------------------------------------------------


def test_pairing_hands_out_a_read_token(client, admin_auth):
    pair = {"code": "123456", "hub_name": "elw-pi", "hub_node_id": "f" * 32}
    resp = client.post("/api/pool/pair", json=pair)
    assert resp.status_code == 403 and "Keine Kopplung" in resp.json()["detail"]

    code = client.post("/api/pool/pairing", headers=admin_auth).json()["code"]
    assert client.get("/api/pool/pairing", headers=admin_auth).json()["code"] == code
    wrong = "000000" if code != "000000" else "111111"
    resp = client.post("/api/pool/pair", json={**pair, "code": wrong})
    assert resp.status_code == 403 and "falsch" in resp.json()["detail"]

    out = client.post("/api/pool/pair", json={**pair, "code": code}).json()
    assert out["node_id"] == _own_id() and out["token"].startswith("ogcp_")
    status = client.get("/api/pool/status", headers={"X-Pool-Token": out["token"]}).json()
    assert status["control_enabled"] is False  # pairing only ever grants "read"
    tokens = client.get("/api/pool/tokens", headers=admin_auth).json()
    assert [(t["name"], t["scope"]) for t in tokens] == [("elw-pi", "read")]
    # The code is used up.
    assert client.get("/api/pool/pairing", headers=admin_auth).json()["code"] == ""


def test_box_cannot_pair_with_itself(client, admin_auth):
    code = client.post("/api/pool/pairing", headers=admin_auth).json()["code"]
    resp = client.post(
        "/api/pool/pair", json={"code": code, "hub_name": "me", "hub_node_id": _own_id()}
    )
    assert resp.status_code == 409


def test_pairing_admin_endpoints_need_admin(client, admin_auth):
    _make_user(client, admin_auth, "pair@test.local")
    user = _auth(client, "pair@test.local")
    assert client.post("/api/pool/pairing", headers=user).status_code == 403
    assert client.get("/api/pool/discovery", headers=user).status_code == 403
    assert client.post(
        "/api/pool/peers/pair", headers=user, json={"base_url": "x", "code": "1"}
    ).status_code == 403


# --- Hub side: /api/pool/peers/pair -------------------------------------------


def test_hub_stores_paired_peer_and_repairs_in_place(client, admin_auth, monkeypatch):
    sent = []

    def fake_pair(base_url, code, hub_node_id):
        sent.append((base_url, code, hub_node_id))
        token = f"ogcp_tok{len(sent)}"
        return {"token": token, "node_id": "b" * 32, "name": "box2", "version": "1"}

    monkeypatch.setattr(pool_core, "pair_with_peer", fake_pair)
    body = {"base_url": "192.168.1.20:8000", "code": " 123456 "}
    peer = client.post("/api/pool/peers/pair", headers=admin_auth, json=body).json()
    assert peer["name"] == "box2" and peer["base_url"] == "http://192.168.1.20:8000"
    assert sent == [("http://192.168.1.20:8000", " 123456 ", _own_id())]

    # Pairing the same box again (new IP after DHCP) updates the one peer.
    body = {"base_url": "http://192.168.1.33:8000", "code": "654321"}
    again = client.post("/api/pool/peers/pair", headers=admin_auth, json=body).json()
    assert again["id"] == peer["id"] and again["base_url"] == "http://192.168.1.33:8000"
    with SessionLocal() as db:
        rows = list(db.scalars(select(PoolPeer)))
        assert len(rows) == 1 and decrypt(rows[0].token_encrypted) == "ogcp_tok2"


def test_hub_passes_on_why_pairing_failed(client, admin_auth, monkeypatch):
    def refuse(req, timeout):
        body = b'{"detail":"Kopplungscode falsch"}'
        raise urllib.error.HTTPError(req.full_url, 403, "x", {}, io.BytesIO(body))

    monkeypatch.setattr(pool_core.urllib.request, "urlopen", refuse)
    resp = client.post(
        "/api/pool/peers/pair", headers=admin_auth,
        json={"base_url": "http://192.168.1.20:8000", "code": "1"},
    )
    assert resp.status_code == 409 and resp.json()["detail"] == "Kopplungscode falsch"
    with SessionLocal() as db:
        assert db.scalar(select(PoolPeer)) is None


# --- Discovery list -------------------------------------------------------------


def _box(node_id, name, addr, port=8000):
    discovery.remember(
        f"{name}._offgridcloud._tcp.local.", node_id=node_id, name=name, version="0.8.0",
        addresses=[addr], port=port,
    )


def test_discovery_lists_other_boxes_and_marks_known_peers(client, admin_auth):
    discovery.node_id = _own_id()
    _box(_own_id(), "this-box", "10.0.0.1")  # never lists itself
    _box("b" * 32, "Box B", "10.0.0.2")
    _box("c" * 32, "Box C", "10.0.0.3")
    _box("d" * 32, "Box D", "10.0.0.4")
    with SessionLocal() as db:
        db.add(PoolPeer(name="B", base_url="http://10.0.0.99:8000", node_id="b" * 32))
        db.add(PoolPeer(name="C by URL", base_url="http://10.0.0.3:8000"))
        db.commit()

    out = client.get("/api/pool/discovery", headers=admin_auth).json()
    assert out["running"] is False and "OGC_MDNS_ENABLED" in out["error"]
    boxes = {b["name"]: b for b in out["boxes"]}
    assert set(boxes) == {"Box B", "Box C", "Box D"}
    assert boxes["Box B"]["peer_id"] is not None  # matched by node id despite new IP
    assert boxes["Box C"]["peer_id"] is not None  # matched by URL
    assert boxes["Box D"]["peer_id"] is None
    assert boxes["Box D"]["url"] == "http://10.0.0.4:8000"


def test_incomplete_announcements_are_ignored():
    discovery.remember("x", node_id="", name="x", version="", addresses=["10.0.0.9"], port=8000)
    discovery.remember("y", node_id="y" * 32, name="y", version="", addresses=[], port=8000)
    assert discovery.boxes() == []


def test_announce_toggle_is_stored(client, admin_auth):
    out = client.put("/api/pool/discovery", headers=admin_auth, json={"announce": False}).json()
    assert out["announce"] is False
    with SessionLocal() as db:
        assert get_system_settings(db).pool_announce is False
