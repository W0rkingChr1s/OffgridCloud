"""Upload handover for the companion app (app.handover, Companion PLAN.md §3.6)."""

import hashlib
import stat

import pytest

from app import handover, https_config, transfers
from app.db import SessionLocal
from app.models import BleDevice, User
from app.network import NetworkStatus, Uplink
from app.security import create_access_token


def _user_auth(client, admin_auth, email):
    client.post(
        "/api/users",
        headers=admin_auth,
        json={"email": email, "password": "userpass123", "role": "user"},
    )
    resp = client.post("/api/auth/login", json={"email": email, "password": "userpass123"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _device(email: str, key: str) -> tuple[int, dict]:
    """A paired phone of ``email`` and the bearer header the BLE bridge would send."""
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one()
        device = BleDevice(user_id=user.id, name=f"Handy {key}", public_key=key * 64)
        db.add(device)
        db.commit()
        token = create_access_token(
            user_id=user.id, role=user.role.value, claims={"dev": device.id}
        )
        return device.id, {"Authorization": f"Bearer {token}"}


class FakeBox:
    """Stands in for nmcli and the root helper."""

    def __init__(self, monkeypatch, chosen: handover.Plan, links=()) -> None:
        self.chosen = chosen
        self.calls: list[tuple[str, ...]] = []
        self.ok = True
        status = NetworkStatus(supported=True, links=list(links))
        monkeypatch.setattr(handover, "_current_plan", lambda: (self.chosen, status))
        monkeypatch.setattr(handover, "_helper_command", lambda: "helper")
        monkeypatch.setattr(handover, "_run_helper", self._run)

    def _run(self, *args):
        self.calls.append(args)
        return self.ok, ""


@pytest.fixture(autouse=True)
def _no_session():
    handover._active = None
    yield
    handover._active = None


AP = handover.Plan(True, "ap", "wlan1")
PAUSE = handover.Plan(True, "ap", "wlan0", uplink_pause=True)


# --- choosing the way ------------------------------------------------------


def _status(*links: Uplink, ap_active=False) -> NetworkStatus:
    return NetworkStatus(supported=True, links=list(links), ap_active=ap_active)


def test_plan_prefers_running_fallback_ap():
    chosen = handover.plan(_status(ap_active=True), ["wlan0"], helper=False)
    assert (chosen.possible, chosen.mode, chosen.uplink_pause) == (True, "fallback", False)


def test_plan_uses_spare_adapter_without_pause():
    wlan0 = Uplink(device="wlan0", kind="client", label="WLAN")
    chosen = handover.plan(_status(wlan0), ["wlan0", "wlan1"], helper=True)
    assert (chosen.interface, chosen.uplink_pause) == ("wlan1", False)


def test_plan_takes_idle_wlan0_while_on_cable():
    eth = Uplink(device="eth0", kind="ethernet", label="Kabel")
    chosen = handover.plan(_status(eth), ["wlan0"], helper=True)
    assert (chosen.interface, chosen.uplink_pause) == ("wlan0", False)


def test_plan_pauses_only_wifi_uplink():
    wlan0 = Uplink(device="wlan0", kind="client", label="WLAN")
    chosen = handover.plan(_status(wlan0), ["wlan0"], helper=True)
    assert (chosen.interface, chosen.uplink_pause) == ("wlan0", True)
    # Cable in as well: the box stays online without wlan0.
    eth = Uplink(device="eth0", kind="ethernet", label="Kabel")
    assert not handover.plan(_status(wlan0, eth), ["wlan0"], helper=True).uplink_pause


def test_plan_reasons_when_impossible():
    assert not handover.plan(NetworkStatus(detail="kein nmcli"), ["wlan0"], helper=True).possible
    assert "install.sh" in handover.plan(_status(), ["wlan0"], helper=False).reason
    assert "WLAN-Adapter" in handover.plan(_status(), [], helper=True).reason


def test_upload_ssid_fits_32_bytes():
    assert handover.upload_ssid("offgridcloud-ab12c") == "OGC-offgridcloud-ab12c-up"
    assert len(handover.upload_ssid("ä" * 40).encode()) <= 32


# --- endpoints --------------------------------------------------------------


def test_only_reachable_over_ble(client, admin_auth, monkeypatch):
    FakeBox(monkeypatch, AP)
    assert client.get("/api/uplink/handover", headers=admin_auth).status_code == 403
    assert client.post("/api/uplink/handover", headers=admin_auth).status_code == 403


def test_start_upload_and_stop(client, admin_auth, monkeypatch):
    box = FakeBox(monkeypatch, AP)
    device_id, ble = _device("admin@test.local", "a")
    folder = client.post("/api/folders", headers=admin_auth, json={"name": "Handover"}).json()

    status = client.get("/api/uplink/handover", headers=ble).json()
    assert status["possible"] and not status["active"] and status["allowed"]

    conn = client.post("/api/uplink/handover", headers=ble).json()
    assert conn["mode"] == "ap" and conn["ssid"].startswith("OGC-")
    assert conn["address"] == "10.43.0.1" and len(conn["passphrase"]) >= 20
    assert box.calls[0][0] == "up"
    config = handover._config_file()
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert conn["passphrase"] in config.read_text()

    # The upload token opens the chunk upload …
    up = {"Authorization": f"Bearer {conn['upload_token']}"}
    sess = client.post(
        f"/api/folders/{folder['id']}/uploads", headers=up, json={"filename": "a.jpg", "size": 3}
    ).json()
    client.put(f"/api/uploads/{sess['id']}", headers={**up, "X-Offset": "0"}, content=b"abc")
    media = client.post(f"/api/uploads/{sess['id']}/complete", headers=up).json()
    assert media["sha256"] == hashlib.sha256(b"abc").hexdigest()
    # … and nothing else.
    assert client.get("/api/folders", headers=up).status_code == 401
    assert client.get("/api/uplink/handover", headers=up).status_code == 401

    # Asking again resumes the same session and Wi-Fi key.
    again = client.post("/api/uplink/handover", headers=ble).json()
    assert again["passphrase"] == conn["passphrase"]
    assert client.get("/api/uplink/handover", headers=ble).json()["mine"] is True
    assert len(box.calls) == 1

    assert client.delete("/api/uplink/handover", headers=ble).status_code == 204
    assert box.calls[-1] == ("down",)
    assert not config.exists()
    assert client.get(f"/api/uploads/{sess['id']}", headers=up).status_code == 401

    # Stable key per device: the phone's OS only asks once.
    assert client.post("/api/uplink/handover", headers=ble).json()["passphrase"] == conn[
        "passphrase"
    ]
    with SessionLocal() as db:
        assert db.get(BleDevice, device_id).wifi_psk_encrypted


def test_one_device_at_a_time(client, admin_auth, monkeypatch):
    FakeBox(monkeypatch, AP)
    _, first = _device("admin@test.local", "b")
    _, second = _device("admin@test.local", "c")
    first_key = client.post("/api/uplink/handover", headers=first).json()["passphrase"]
    assert client.post("/api/uplink/handover", headers=second).status_code == 409
    status = client.get("/api/uplink/handover", headers=second).json()
    assert status["active"] and not status["mine"] and status["connection"] is None
    # Someone else's DELETE leaves the session alone.
    client.delete("/api/uplink/handover", headers=second)
    assert handover._active is not None
    client.delete("/api/uplink/handover", headers=first)
    second_key = client.post("/api/uplink/handover", headers=second).json()["passphrase"]
    assert second_key != first_key


def test_pausing_the_only_uplink(client, admin_auth, monkeypatch):
    box = FakeBox(monkeypatch, PAUSE)
    user_auth = _user_auth(client, admin_auth, "handover-user@test.local")
    _, admin_ble = _device("admin@test.local", "d")
    _, user_ble = _device("handover-user@test.local", "e")

    status = client.get("/api/uplink/handover", headers=user_ble).json()
    assert status["uplink_pause"] and not status["allowed"]
    assert client.post("/api/uplink/handover", headers=user_ble).status_code == 403

    # Admins must confirm that the box goes offline.
    assert client.post("/api/uplink/handover", headers=admin_ble).status_code == 409
    assert box.calls == []
    resp = client.post("/api/uplink/handover", headers=admin_ble, json={"confirm_pause": True})
    assert resp.status_code == 200 and resp.json()["uplink_pause"] is True
    assert handover.uplink_paused()
    assert transfers.process_one() is False  # cloud transfers wait
    client.delete("/api/uplink/handover", headers=admin_ble)
    assert not handover.uplink_paused()

    # An admin can allow it for users.
    settings = client.put(
        "/api/ble/settings", headers=admin_auth, json={"handover_users_may_pause": True}
    ).json()
    assert settings["handover_users_may_pause"] and settings["enabled"]
    assert client.put(
        "/api/ble/settings", headers=user_auth, json={"handover_users_may_pause": False}
    ).status_code == 403
    resp = client.post("/api/uplink/handover", headers=user_ble, json={"confirm_pause": True})
    assert resp.status_code == 200
    client.delete("/api/uplink/handover", headers=user_ble)
    client.put("/api/ble/settings", headers=admin_auth, json={"handover_users_may_pause": False})


def test_fallback_ap_is_handed_over_as_is(client, admin_auth, monkeypatch):
    box = FakeBox(monkeypatch, handover.Plan(True, "fallback"))
    _, ble = _device("admin@test.local", "f")
    from types import SimpleNamespace

    from app import network
    from app.crypto import encrypt

    ap = SimpleNamespace(
        ap_ssid="Feld-AP", ap_psk_encrypted=encrypt("feldpasswort1"), ap_address="10.42.0.1/24"
    )
    monkeypatch.setattr(network, "get_network_settings", lambda _db: ap)
    conn = client.post("/api/uplink/handover", headers=ble).json()
    assert conn["mode"] == "fallback" and conn["ssid"] == "Feld-AP"
    assert conn["passphrase"] == "feldpasswort1" and conn["address"] == "10.42.0.1"
    client.delete("/api/uplink/handover", headers=ble)
    assert box.calls == []  # nothing to raise or drop


def test_pins_root_ca_when_https_is_up(client, admin_auth, monkeypatch):
    FakeBox(monkeypatch, AP)
    _, ble = _device("admin@test.local", "g")
    monkeypatch.setattr(https_config, "is_active", lambda _dir: True)
    monkeypatch.setattr(https_config, "read_root_ca", lambda _dir: b"der-bytes")
    conn = client.post("/api/uplink/handover", headers=ble).json()
    assert (conn["scheme"], conn["port"]) == ("https", 443)
    assert conn["ca_sha256"] == hashlib.sha256(b"der-bytes").hexdigest()
    monkeypatch.setattr(https_config, "is_active", lambda _dir: False)
    conn = client.get("/api/uplink/handover", headers=ble).json()["connection"]
    assert (conn["scheme"], conn["ca_der"]) == ("http", None)


def test_revoking_the_device_ends_its_wifi(client, admin_auth, monkeypatch):
    box = FakeBox(monkeypatch, AP)
    device_id, ble = _device("admin@test.local", "h")
    client.post("/api/uplink/handover", headers=ble)
    assert client.delete(f"/api/ble/devices/{device_id}", headers=admin_auth).status_code == 204
    assert handover._active is None and box.calls[-1] == ("down",)


def test_helper_failure_rolls_back(client, admin_auth, monkeypatch):
    box = FakeBox(monkeypatch, AP)
    box.ok = False
    _, ble = _device("admin@test.local", "i")
    assert client.post("/api/uplink/handover", headers=ble).status_code == 502
    assert handover._active is None and box.calls[-1] == ("down",)
    assert not handover._config_file().exists()


def test_idle_and_maximum_duration(client, admin_auth, monkeypatch):
    box = FakeBox(monkeypatch, AP)
    _, ble = _device("admin@test.local", "j")
    client.post("/api/uplink/handover", headers=ble)
    session = handover._active
    with SessionLocal() as db:
        handover.expire(db, now=session.last_activity + handover.IDLE_TIMEOUT - 1)
        assert handover._active is session
        handover.expire(db, now=session.last_activity + handover.IDLE_TIMEOUT)
        assert handover._active is None and box.calls[-1] == ("down",)

    client.post("/api/uplink/handover", headers=ble)
    session = handover._active
    session.last_activity = session.started + handover.MAX_DURATION
    with SessionLocal() as db:
        handover.expire(db, now=session.started + handover.MAX_DURATION)
    assert handover._active is None


def test_leftover_wifi_is_dropped_on_startup(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(handover, "_helper_command", lambda: "helper")
    monkeypatch.setattr(handover, "_run_helper", lambda *a: calls.append(a) or (True, ""))
    handover._config_file().write_text("{}")
    handover.cleanup_on_startup()
    assert calls == [("down",)] and not handover._config_file().exists()


def test_ble_bridge_tokens_name_the_device(client):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    from app.ble.bridge import BoxHost
    from app.ble.registry import DeviceInfo
    from app.security import decode_access_token

    host = BoxHost(X25519PrivateKey.generate(), "http://127.0.0.1:1")
    one = DeviceInfo(id=7, name="A", user_id=1, user_email="a@x", role="admin")
    two = DeviceInfo(id=8, name="B", user_id=1, user_email="a@x", role="admin")
    assert decode_access_token(host._token(one))["dev"] == 7
    # Same user, other phone: its own token, not the cached one.
    assert decode_access_token(host._token(two))["dev"] == 8


def test_ended_fallback_ap_ends_the_session(client, admin_auth, monkeypatch):
    from app import network

    box = FakeBox(monkeypatch, handover.Plan(True, "fallback"))
    _, ble = _device("admin@test.local", "k")
    client.post("/api/uplink/handover", headers=ble)
    monkeypatch.setattr(network, "get_status", lambda: NetworkStatus(ap_active=True))
    assert client.get("/api/uplink/handover", headers=ble).json()["mine"] is True
    # Uplink back, watchdog dropped the fallback AP: offer the next way instead.
    monkeypatch.setattr(network, "get_status", lambda: NetworkStatus(ap_active=False))
    box.chosen = AP
    status = client.get("/api/uplink/handover", headers=ble).json()
    assert status["active"] is False and status["possible"] is True
    assert handover._active is None
