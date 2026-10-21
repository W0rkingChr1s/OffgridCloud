"""Bluetooth pairing codes and devices (docs/BLE-PROTOKOLL.md §6)."""

import base64
from datetime import timedelta

from app.ble import registry
from app.ble.keys import box_public_key
from app.db import SessionLocal
from app.models import BlePairing, User


def _user_auth(client, admin_auth, email="ble-user@test.local"):
    client.post(
        "/api/users",
        headers=admin_auth,
        json={"email": email, "password": "userpass123", "role": "user"},
    )
    resp = client.post("/api/auth/login", json={"email": email, "password": "userpass123"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _decode_code(code: str) -> tuple[int, bytes]:
    value = 0
    for ch in code.replace("-", ""):
        value = value * 32 + registry.CROCKFORD.index(ch)
    return value >> 80, (value & ((1 << 80) - 1)).to_bytes(10, "big")


def test_code_format_matches_test_vector():
    # docs/ble/testvectors.json → pairing_code
    assert registry.format_code(0x5A3C1, bytes.fromhex("0f1e2d3c4b5a69788796")) == (
        "B8Y11-WF2TF-2BB9M-QH1WP"
    )


def test_status_exposes_box_key(client, admin_auth):
    body = client.get("/api/ble/status", headers=admin_auth).json()
    assert body["enabled"] is True
    assert body["service_running"] is False
    pk = base64.urlsafe_b64decode(body["box_public_key"] + "=")
    assert pk == box_public_key() and len(pk) == 32


def test_pairing_code_and_link(client, admin_auth):
    resp = client.post("/api/ble/pairings", headers=admin_auth)
    assert resp.status_code == 201
    body = resp.json()
    pid, secret = _decode_code(body["code"])
    assert body["expires_in"] == 300
    assert body["link"].startswith(f"offgridcloud://pair?v=1&c={body['code'].replace('-', '')}&pk=")
    with SessionLocal() as db:
        claim = registry.claim_pairing(db, pid)
        assert claim is not None and claim.secret == secret


def test_claim_limits_attempts_and_expiry(client, admin_auth):
    pid, _ = _decode_code(client.post("/api/ble/pairings", headers=admin_auth).json()["code"])
    with SessionLocal() as db:
        for _ in range(registry.MAX_HANDSHAKES_PER_CODE):
            assert registry.claim_pairing(db, pid) is not None
        assert registry.claim_pairing(db, pid) is None
        assert registry.claim_pairing(db, 0xFFFFF + 1) is None

    pid2, _ = _decode_code(client.post("/api/ble/pairings", headers=admin_auth).json()["code"])
    with SessionLocal() as db:
        row = db.query(BlePairing).filter_by(pid=pid2).one()
        row.expires_at -= timedelta(minutes=10)
        db.commit()
        assert registry.claim_pairing(db, pid2) is None


def test_confirm_registers_device_and_burns_code(client, admin_auth):
    pid, _ = _decode_code(client.post("/api/ble/pairings", headers=admin_auth).json()["code"])
    key = bytes(range(32))
    with SessionLocal() as db:
        claim = registry.claim_pairing(db, pid)
        device = registry.confirm_pairing(db, claim, key, "Testhandy")
        assert device is not None and device.role == "admin"
        assert registry.claim_pairing(db, pid) is None
        assert registry.find_device(db, key).id == device.id
        # A second confirmation with the same claim does nothing.
        assert registry.confirm_pairing(db, claim, key, "x") is None

    devices = client.get("/api/ble/devices", headers=admin_auth).json()
    assert [d["name"] for d in devices] == ["Testhandy"]


def test_users_see_and_revoke_only_their_own_devices(client, admin_auth):
    user_auth = _user_auth(client, admin_auth)
    pid, _ = _decode_code(client.post("/api/ble/pairings", headers=user_auth).json()["code"])
    with SessionLocal() as db:
        claim = registry.claim_pairing(db, pid)
        own = registry.confirm_pairing(db, claim, bytes([7]) * 32, "Mein Handy")
        assert own.role == "user"
        admin_id = db.query(User).filter_by(email="admin@test.local").one().id
        other = registry.confirm_pairing(
            db,
            registry.claim_pairing(db, registry.create_pairing(db, db.get(User, admin_id)).pid),
            bytes([8]) * 32,
            "Admin-Handy",
        )

    mine = client.get("/api/ble/devices", headers=user_auth).json()
    assert [d["id"] for d in mine] == [own.id]
    assert client.delete(f"/api/ble/devices/{other.id}", headers=user_auth).status_code == 404
    renamed = client.patch(
        f"/api/ble/devices/{own.id}", headers=user_auth, json={"name": "Neu"}
    ).json()
    assert renamed["name"] == "Neu"
    assert client.delete(f"/api/ble/devices/{own.id}", headers=user_auth).status_code == 204
    with SessionLocal() as db:
        assert registry.find_device(db, bytes([7]) * 32) is None
        assert not registry.device_still_valid(db, own.id)
    # Admins revoke any device.
    assert client.delete(f"/api/ble/devices/{other.id}", headers=admin_auth).status_code == 204


def test_disabled_bluetooth_blocks_new_codes(client, admin_auth):
    user_auth = _user_auth(client, admin_auth, "ble-user2@test.local")
    off = {"enabled": False}
    assert client.put("/api/ble/settings", headers=user_auth, json=off).status_code == 403
    assert client.put("/api/ble/settings", headers=admin_auth, json=off).json()["enabled"] is False
    assert client.post("/api/ble/pairings", headers=admin_auth).status_code == 409
    client.put("/api/ble/settings", headers=admin_auth, json={"enabled": True})
    assert client.post("/api/ble/pairings", headers=admin_auth).status_code == 201


def test_box_host_reads_settings_and_open_codes(client, admin_auth):
    import asyncio

    from app.ble.bridge import BoxHost
    from app.ble.keys import load_or_create_box_key

    host = BoxHost(load_or_create_box_key(), "http://127.0.0.1:9")
    client.put("/api/ble/settings", headers=admin_auth, json={"enabled": True})
    client.post("/api/ble/pairings", headers=admin_auth)
    assert asyncio.run(host.read_enabled()) is True
    assert asyncio.run(host.pairing_open()) is True


def test_status_reports_install_adapter_and_error(client, admin_auth, tmp_path, monkeypatch):
    import json
    import time

    from app.ble import service
    from app.ble.keys import ble_dir
    from app.routers import ble as ble_router

    unit = tmp_path / "offgridcloud-ble.service"
    sysbt = tmp_path / "bluetooth"
    sysbt.mkdir()
    monkeypatch.setattr(ble_router, "SERVICE_UNIT", unit)
    monkeypatch.setattr(ble_router, "SYS_BLUETOOTH", sysbt)

    body = client.get("/api/ble/status", headers=admin_auth).json()
    assert body["service_installed"] is False
    assert body["adapter_present"] is False

    unit.write_text("")
    (sysbt / "hci0").mkdir()
    service.write_startup_error("RuntimeError: Kein Bluetooth-Adapter")
    body = client.get("/api/ble/status", headers=admin_auth).json()
    assert body["service_installed"] is True
    assert body["adapter_present"] is True
    assert body["service_running"] is False
    assert body["service_error"] == "RuntimeError: Kein Bluetooth-Adapter"

    # A running service hides the stale error.
    (ble_dir() / service.STATUS_FILE).write_text(json.dumps({"updated": time.time()}))
    body = client.get("/api/ble/status", headers=admin_auth).json()
    assert body["service_running"] is True
    assert body["service_error"] is None
