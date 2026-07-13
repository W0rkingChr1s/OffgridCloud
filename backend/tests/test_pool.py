"""Tests for multi-server pooling: status auth, token, peer CRUD, overview."""

import app.pool as pool_core
from tests.test_folders import _auth, _make_user
from tests.test_transfers import _folder, _upload


def test_status_requires_token_or_admin(client, admin_auth):
    assert client.get("/api/pool/status").status_code == 401
    resp = client.get("/api/pool/status", headers=admin_auth)
    assert resp.status_code == 200
    assert resp.json()["version"]


def test_rotate_and_use_pool_token(client, admin_auth):
    token = client.post("/api/pool/token", headers=admin_auth).json()["pool_token"]
    assert token

    # A peer-style call using only the shared token works.
    assert client.get("/api/pool/status", headers={"X-Pool-Token": token}).status_code == 200
    # A wrong token is rejected.
    assert client.get("/api/pool/status", headers={"X-Pool-Token": "nope"}).status_code == 401
    assert client.get("/api/pool/self", headers=admin_auth).json()["token_set"] is True

    # Clearing the token disables peer access again.
    assert client.delete("/api/pool/token", headers=admin_auth).status_code == 204
    assert client.get("/api/pool/status", headers={"X-Pool-Token": token}).status_code == 401


def test_status_reports_media_counts(client, admin_auth):
    before = client.get("/api/pool/status", headers=admin_auth).json()["media_total"]
    folder = _folder(client, admin_auth)
    _upload(client, admin_auth, folder["id"], "a.mp4", b"x")
    after = client.get("/api/pool/status", headers=admin_auth).json()
    assert after["media_total"] == before + 1
    assert after["media"].get("received", 0) >= 1


def test_peer_crud_and_token_never_returned(client, admin_auth):
    peer = client.post(
        "/api/pool/peers",
        headers=admin_auth,
        json={"name": "Box2", "base_url": "https://box2.local:8000/", "token": "secret"},
    ).json()
    assert peer["base_url"] == "https://box2.local:8000"  # trailing slash stripped
    assert peer["has_token"] is True
    assert "token" not in peer and "token_encrypted" not in peer

    peers = client.get("/api/pool/peers", headers=admin_auth).json()
    assert [p["id"] for p in peers] == [peer["id"]]

    client.patch(
        f"/api/pool/peers/{peer['id']}", headers=admin_auth, json={"enabled": False}
    )
    assert client.get("/api/pool/peers", headers=admin_auth).json()[0]["enabled"] is False

    assert client.delete(f"/api/pool/peers/{peer['id']}", headers=admin_auth).status_code == 204
    assert client.get("/api/pool/peers", headers=admin_auth).json() == []


def test_pool_management_requires_admin(client, admin_auth):
    _make_user(client, admin_auth, "pool@test.local")
    user_auth = _auth(client, "pool@test.local")
    assert client.get("/api/pool/peers", headers=user_auth).status_code == 403
    assert client.post("/api/pool/token", headers=user_auth).status_code == 403
    assert client.get("/api/pool/overview", headers=user_auth).status_code == 403


def test_overview_aggregates_self_and_peers(client, admin_auth, monkeypatch):
    folder = _folder(client, admin_auth)
    _upload(client, admin_auth, folder["id"], "a.mp4", b"x")
    client.post(
        "/api/pool/peers",
        headers=admin_auth,
        json={"name": "Box2", "base_url": "https://box2.local", "token": "t"},
    )

    def fake_poll(peer):
        return {
            "name": "ignored-by-hub",
            "version": "9.9",
            "reachable": True,
            "error": "",
            "media": {"done": 4},
            "media_total": 4,
            "active_transfers": 2,
            "throughput_kbps": 100.0,
            "disk_free": 10,
            "disk_total": 20,
        }

    monkeypatch.setattr(pool_core, "poll_peer", fake_poll)

    overview = client.get("/api/pool/overview", headers=admin_auth).json()
    self_node = overview["self"]
    assert self_node["media_total"] >= 1
    assert len(overview["peers"]) == 1
    assert overview["peers"][0]["name"] == "Box2"  # configured name wins over polled
    assert overview["peers"][0]["media_total"] == 4

    totals = overview["totals"]
    assert totals["nodes"] == 2
    assert totals["nodes_online"] == 2
    assert totals["media_total"] == self_node["media_total"] + 4
    assert totals["active_transfers"] == self_node["active_transfers"] + 2


def test_overview_marks_unreachable_peer(client, admin_auth, monkeypatch):
    client.post(
        "/api/pool/peers",
        headers=admin_auth,
        json={"name": "Down", "base_url": "https://down.local", "token": "t"},
    )
    monkeypatch.setattr(
        pool_core, "poll_peer", lambda peer: {"reachable": False, "error": "timed out"}
    )
    overview = client.get("/api/pool/overview", headers=admin_auth).json()
    assert overview["peers"][0]["reachable"] is False
    assert overview["peers"][0]["error"] == "timed out"
    assert overview["totals"]["nodes_online"] == 1  # only self
