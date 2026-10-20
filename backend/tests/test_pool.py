"""Tests for multi-server pooling: status auth, token, peer CRUD, overview."""

import app.pool as pool_core
from tests.test_folders import _auth, _make_user
from tests.test_transfers import _folder, _upload


def test_status_requires_token_or_admin(client, admin_auth):
    assert client.get("/api/pool/status").status_code == 401
    resp = client.get("/api/pool/status", headers=admin_auth)
    assert resp.status_code == 200
    assert resp.json()["version"]


def _issue(client, admin_auth, name, scope="read"):
    resp = client.post("/api/pool/tokens", headers=admin_auth, json={"name": name, "scope": scope})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_each_hub_has_its_own_revocable_token(client, admin_auth):
    elw = _issue(client, admin_auth, "ELW")
    boot = _issue(client, admin_auth, "Boot")
    assert elw["token"].startswith("ogcp_") and elw["token"] != boot["token"]
    assert elw["hint"] == elw["token"][-4:]

    def status_code(token):
        return client.get("/api/pool/status", headers={"X-Pool-Token": token}).status_code

    assert status_code(elw["token"]) == 200
    assert status_code(boot["token"]) == 200
    assert status_code("nope") == 401

    # The plaintext is shown once at creation and never again.
    listed = client.get("/api/pool/tokens", headers=admin_auth).json()
    assert {t["name"] for t in listed} >= {"ELW", "Boot"}
    assert all("token" not in t and "token_hash" not in t for t in listed)

    # Revoking one hub leaves the other untouched.
    assert client.delete(f"/api/pool/tokens/{elw['id']}", headers=admin_auth).status_code == 204
    assert status_code(elw["token"]) == 401
    assert status_code(boot["token"]) == 200
    client.delete(f"/api/pool/tokens/{boot['id']}", headers=admin_auth)


def test_token_records_last_use(client, admin_auth):
    t = _issue(client, admin_auth, "ELW")
    assert t["last_used_at"] is None
    client.get("/api/pool/status", headers={"X-Pool-Token": t["token"], "X-Pool-Hub": "elw-pi"})
    tokens = client.get("/api/pool/tokens", headers=admin_auth).json()
    row = next(x for x in tokens if x["id"] == t["id"])
    assert row["last_used_at"] is not None
    assert row["last_used_hub"] == "elw-pi"
    client.delete(f"/api/pool/tokens/{t['id']}", headers=admin_auth)


def test_tokens_are_stored_hashed(client, admin_auth):
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import PoolAccessToken

    t = _issue(client, admin_auth, "ELW")
    with SessionLocal() as db:
        row = db.scalar(select(PoolAccessToken).where(PoolAccessToken.id == t["id"]))
        assert row.token_hash == pool_core.hash_token(t["token"])
        assert t["token"] not in row.token_hash
    client.delete(f"/api/pool/tokens/{t['id']}", headers=admin_auth)


def test_legacy_shared_token_keeps_working_after_migration(client, admin_auth):
    from sqlalchemy import select

    from app.admin_ops import get_system_settings
    from app.db import SessionLocal
    from app.models import PoolAccessToken

    with SessionLocal() as db:
        get_system_settings(db).pool_token = "legacy-shared-token"
        db.commit()
    pool_core.migrate_legacy_token()
    pool_core.migrate_legacy_token()  # idempotent

    hub = {"X-Pool-Token": "legacy-shared-token"}
    status = client.get("/api/pool/status", headers=hub).json()
    assert status["control_enabled"] is False  # migrated as read-only
    with SessionLocal() as db:
        assert get_system_settings(db).pool_token == ""  # plaintext gone
        rows = db.scalars(
            select(PoolAccessToken).where(PoolAccessToken.name == "Bisheriges Token")
        ).all()
        assert len(rows) == 1
        db.delete(rows[0])
        db.commit()


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
    assert client.get("/api/pool/tokens", headers=user_auth).status_code == 403
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


def test_poll_peer_explains_tls_on_plain_http_port(monkeypatch):
    import ssl
    import urllib.error

    from app.models import PoolPeer

    def boom(*a, **k):
        raise urllib.error.URLError(
            ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER] wrong version number")
        )

    monkeypatch.setattr(pool_core.urllib.request, "urlopen", boom)
    result = pool_core.poll_peer(PoolPeer(name="p", base_url="https://10.0.0.2:8000"))
    assert result["reachable"] is False
    assert "http://" in result["error"]


def test_peer_url_without_scheme_defaults_to_http(client, admin_auth):
    resp = client.post(
        "/api/pool/peers",
        json={"name": "bare", "base_url": " 192.168.178.128:8000/ "},
        headers=admin_auth,
    )
    assert resp.status_code == 201
    assert resp.json()["base_url"] == "http://192.168.178.128:8000"
    assert pool_core.normalize_base_url("https://box.local/") == "https://box.local"
