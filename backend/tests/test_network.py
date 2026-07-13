import json

import pytest

from app import network
from app.config import get_settings


# --------------------------------------------------------------------------- #
# Pure parsing / validation (no nmcli needed)                                  #
# --------------------------------------------------------------------------- #
def test_split_terse_handles_escaped_colon():
    assert network._split_terse(r"wlan0:wifi:connected:My\:SSID") == [
        "wlan0",
        "wifi",
        "connected",
        "My:SSID",
    ]


def test_parse_device_status():
    raw = "eth0:ethernet:unavailable:\nwlan0:wifi:connected:HomeWifi\n"
    rows = network.parse_device_status(raw)
    assert rows[1] == {
        "device": "wlan0",
        "type": "wifi",
        "state": "connected",
        "connection": "HomeWifi",
    }


def test_build_status_client_mode():
    devices = [
        {"device": "eth0", "type": "ethernet", "state": "unavailable", "connection": ""},
        {"device": "wlan0", "type": "wifi", "state": "connected", "connection": "HomeWifi"},
    ]
    status = network.build_status(devices=devices, connectivity="full", wifi_ip="192.168.1.50")
    assert status.mode == "client"
    assert status.wifi_ssid == "HomeWifi"
    assert status.wifi_ip == "192.168.1.50"
    assert status.online is True
    assert status.ap_active is False


def test_build_status_online_when_connected_despite_no_portal():
    # Connected as a client with an IP, but NM's internet check says "none"
    # (checking disabled / networkd-rendered). Must still read as online, not
    # "offline" — otherwise the UI shows a dead link and the fallback looks armed.
    devices = [
        {"device": "wlan0", "type": "wifi", "state": "connected", "connection": "MartinRouterKing"},
    ]
    status = network.build_status(devices=devices, connectivity="none", wifi_ip="192.168.178.70")
    assert status.mode == "client"
    assert status.online is True


def test_build_status_ap_mode_detected():
    devices = [
        {
            "device": "wlan0",
            "type": "wifi",
            "state": "connected",
            "connection": network.AP_CONNECTION_NAME,
        }
    ]
    status = network.build_status(devices=devices, connectivity="none")
    assert status.mode == "ap"
    assert status.ap_active is True
    assert status.online is False


def test_build_status_offline():
    devices = [
        {"device": "wlan0", "type": "wifi", "state": "disconnected", "connection": ""},
    ]
    status = network.build_status(devices=devices, connectivity="none")
    assert status.mode == "offline"


@pytest.mark.parametrize("bad", ["", "x" * 33])
def test_validate_ssid_rejects_bad_length(bad):
    with pytest.raises(ValueError):
        network.validate_ssid(bad)


def test_validate_passphrase_rules():
    assert network.validate_passphrase("", allow_empty=True) == ""
    with pytest.raises(ValueError):
        network.validate_passphrase("", allow_empty=False)
    with pytest.raises(ValueError):
        network.validate_passphrase("short", allow_empty=False)
    assert network.validate_passphrase("goodpass", allow_empty=False) == "goodpass"


def test_validate_country():
    assert network.validate_country("de") == "DE"
    assert network.validate_country("") == ""
    with pytest.raises(ValueError):
        network.validate_country("DEU")


# --------------------------------------------------------------------------- #
# API                                                                          #
# --------------------------------------------------------------------------- #
def test_overview_requires_admin(client):
    assert client.get("/api/network").status_code in (401, 403)


def test_overview_defaults(client, admin_auth):
    body = client.get("/api/network", headers=admin_auth).json()
    assert body["settings"]["ap_ssid"] == "OffgridCloud"
    assert body["settings"]["fallback_enabled"] is False
    assert body["known_networks"] == []
    # On the CI host there is no NetworkManager — must degrade, not error.
    assert body["status"]["supported"] is False
    assert body["status"]["apply_wired"] is False


def test_update_settings_and_audit(client, admin_auth):
    resp = client.put(
        "/api/network/settings",
        headers=admin_auth,
        json={
            "fallback_enabled": True,
            "ap_ssid": "Feld-Box",
            "ap_password": "supersecret",
            "country_code": "de",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fallback_enabled"] is True
    assert body["ap_ssid"] == "Feld-Box"
    assert body["ap_has_password"] is True
    assert body["country_code"] == "DE"

    events = client.get("/api/system/audit", headers=admin_auth).json()
    assert any(e["action"] == "network.settings" for e in events)


def test_update_settings_rejects_bad_password(client, admin_auth):
    resp = client.put(
        "/api/network/settings", headers=admin_auth, json={"ap_password": "short"}
    )
    assert resp.status_code == 422


def test_known_network_crud(client, admin_auth):
    created = client.post(
        "/api/network/known",
        headers=admin_auth,
        json={"ssid": "Baustelle", "password": "baupass1", "priority": 5},
    )
    assert created.status_code == 201, created.text
    nid = created.json()["id"]
    assert created.json()["has_password"] is True
    assert created.json()["priority"] == 5

    listing = client.get("/api/network/known", headers=admin_auth).json()
    assert any(n["id"] == nid for n in listing)

    updated = client.put(
        f"/api/network/known/{nid}",
        headers=admin_auth,
        json={"autoconnect": False},
    )
    assert updated.json()["autoconnect"] is False

    assert client.delete(f"/api/network/known/{nid}", headers=admin_auth).status_code == 204
    assert client.delete(f"/api/network/known/{nid}", headers=admin_auth).status_code == 404


def test_add_known_open_network_allowed(client, admin_auth):
    resp = client.post(
        "/api/network/known", headers=admin_auth, json={"ssid": "OpenNet"}
    )
    assert resp.status_code == 201
    assert resp.json()["has_password"] is False


def test_apply_exports_config_without_helper(client, admin_auth):
    client.put(
        "/api/network/settings",
        headers=admin_auth,
        json={"fallback_enabled": True, "ap_ssid": "Feld", "ap_password": "feldpass1"},
    )
    client.post(
        "/api/network/known",
        headers=admin_auth,
        json={"ssid": "Uplink", "password": "uplink12", "priority": 9},
    )
    resp = client.post("/api/network/apply", headers=admin_auth).json()
    # No OGC_NET_APPLY_COMMAND on the test host -> export-only.
    assert resp["ok"] is False
    assert "gespeichert" in resp["message"].lower()

    exported = json.loads(get_settings().network_config_path.read_text())
    assert exported["fallback_enabled"] is True
    assert exported["ap"]["ssid"] == "Feld"
    assert exported["ap"]["passphrase"] == "feldpass1"  # decrypted for the helper
    uplink = [n for n in exported["known_networks"] if n["ssid"] == "Uplink"]
    assert uplink and uplink[0]["passphrase"] == "uplink12"
