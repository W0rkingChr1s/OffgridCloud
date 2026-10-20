import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import setup_wizard
from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import KnownNetwork, NetworkSettings, User

WIZARD_EMAIL = "chef@feld.local"


# --------------------------------------------------------------------------- #
# Pure helpers                                                                 #
# --------------------------------------------------------------------------- #
AP_CLIENT = "10.42.0.23"


@pytest.mark.parametrize(
    "host",
    ["10.42.0.1", "10.42.0.1:80", "192.168.1.20:8000", "offgridcloud-setup.local",
     "localhost:8000", "[fe80::1]:80", "offgridcloud-setup", ""],
)
def test_captive_redirect_leaves_the_box_itself_alone(host):
    assert setup_wizard.captive_redirect(host, AP_CLIENT) is None


@pytest.mark.parametrize(
    "host", ["captive.apple.com", "connectivitycheck.gstatic.com:80", "www.msftconnecttest.com"]
)
def test_captive_redirect_sends_probes_to_the_wizard(host):
    assert setup_wizard.captive_redirect(host, AP_CLIENT) == "http://10.42.0.1/setup"


def test_captive_redirect_only_on_the_setup_ap():
    # On a LAN the router may hand out a dotted name for the box.
    assert setup_wizard.captive_redirect("offgridcloud-setup.fritz.box", "192.168.178.20") is None
    assert setup_wizard.captive_redirect("captive.apple.com", "testclient") is None


def test_client_ip_trusts_forwarded_for_only_from_loopback():
    assert setup_wizard.client_ip("127.0.0.1", "10.42.0.5, 1.2.3.4") == "10.42.0.5"
    assert setup_wizard.client_ip("::1", "10.42.0.5") == "10.42.0.5"
    assert setup_wizard.client_ip("192.168.1.9", "10.42.0.5") == "192.168.1.9"
    assert setup_wizard.client_ip("127.0.0.1", "") == "127.0.0.1"


def test_code_matches_uses_firstboot_hash():
    state = {"salt": "abc", "code_sha256": setup_wizard.hash_code("geheim", "abc")}
    assert setup_wizard.code_required(state)
    assert setup_wizard.code_matches(state, "geheim")
    assert setup_wizard.code_matches(state, "  geheim ")
    assert not setup_wizard.code_matches(state, "falsch")
    assert setup_wizard.code_matches({}, "")  # no code configured


def test_throttle_locks_after_repeated_failures():
    clock = [0.0]
    throttle = setup_wizard.CodeThrottle(now=lambda: clock[0])
    for _ in range(setup_wizard.MAX_CODE_FAILURES):
        assert throttle.retry_after() == 0
        throttle.failure()
    assert throttle.retry_after() == setup_wizard.LOCKOUT_SECONDS
    clock[0] += setup_wizard.LOCKOUT_SECONDS
    assert throttle.retry_after() == 0


def test_read_scan_and_merge(tmp_path):
    path = tmp_path / "scan.json"
    path.write_text(json.dumps(["Camp", "", 3, "Hafen"]))
    assert setup_wizard.read_scan(path) == ["Camp", "Hafen"]
    assert setup_wizard.read_scan(tmp_path / "missing.json") == []
    assert setup_wizard.merge_ssids(["Hafen"], ["Camp", "Hafen"]) == ["Hafen", "Camp"]


def test_suggested_hostname_falls_back_on_garbage():
    assert setup_wizard.suggested_hostname({"suggested_hostname": "OGC-abc12.local"}) == "ogc-abc12"
    assert setup_wizard.suggested_hostname({"suggested_hostname": "-bad-"}) == "offgridcloud"


def test_launch_finish_runs_detached():
    calls = []
    assert setup_wizard.launch_finish("", popen=lambda *a, **k: calls.append(a)) is False
    assert setup_wizard.launch_finish(
        "sudo /x/finish.sh", popen=lambda args, **kw: calls.append((args, kw))
    )
    assert calls[0][0] == ["sudo", "/x/finish.sh"]
    assert calls[0][1]["start_new_session"] is True


def test_initial_admin_not_created_in_wizard_mode(monkeypatch):
    from app import bootstrap

    monkeypatch.setattr(get_settings(), "setup_wizard", True)
    monkeypatch.setattr(bootstrap, "SessionLocal", lambda: pytest.fail("must not touch the DB"))
    bootstrap.ensure_initial_admin()


# --------------------------------------------------------------------------- #
# Setup mode end to end (no users yet → simulated via has_users)               #
# --------------------------------------------------------------------------- #
@pytest.fixture
def setup_mode(client, monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "setup_wizard", True)
    monkeypatch.setattr(settings, "setup_scan_file", tmp_path / "scan.json")
    monkeypatch.setattr(setup_wizard, "throttle", setup_wizard.CodeThrottle())
    with SessionLocal() as db:
        net = db.get(NetworkSettings, 1)
        saved = (net.fallback_enabled, net.ap_ssid, net.ap_psk_encrypted, net.country_code)
    users_exist = {"value": False}
    monkeypatch.setattr(setup_wizard, "has_users", lambda db: users_exist["value"])
    setup_wizard.reset_cache()
    yield users_exist
    setup_wizard.reset_cache()
    (settings.data_dir / setup_wizard.STATE_FILENAME).unlink(missing_ok=True)
    (settings.data_dir / setup_wizard.RESULT_FILENAME).unlink(missing_ok=True)
    with SessionLocal() as db:
        db.execute(delete(User).where(User.email == WIZARD_EMAIL))
        db.execute(delete(KnownNetwork))
        net = db.get(NetworkSettings, 1)
        net.fallback_enabled, net.ap_ssid, net.ap_psk_encrypted, net.country_code = saved
        db.commit()


def _payload(**overrides):
    body = {
        "admin_email": WIZARD_EMAIL,
        "admin_password": "supergeheim",
        "hostname": "offgridcloud-test1",
        "wifi_ssid": "Basislager",
        "wifi_password": "wlanpasswort",
        "fallback_password": "rueckfall123",
        "country": "de",
    }
    body.update(overrides)
    return body


def test_status_reports_not_required_by_default(client):
    assert client.get("/api/setup/status").json() == {
        "required": False,
        "code_required": False,
        "suggested_hostname": "",
        "country": "",
    }


def test_setup_mode_locks_other_apis_and_redirects_probes(client, admin_auth, setup_mode):
    assert client.get("/api/folders", headers=admin_auth).status_code == 503
    assert client.get("/api/health").status_code == 200
    # A phone on the setup Wi-Fi, proxied by the local Caddy.
    with TestClient(app, client=("127.0.0.1", 50000)) as phone:
        probe = phone.get(
            "/hotspot-detect.html",
            headers={"host": "captive.apple.com", "x-forwarded-for": AP_CLIENT},
            follow_redirects=False,
        )
    assert probe.status_code == 302
    assert probe.headers["location"] == "http://10.42.0.1/setup"
    status = client.get("/api/setup/status").json()
    assert status["required"] is True
    assert status["code_required"] is False


def test_wifi_lists_cached_scan(client, setup_mode, monkeypatch):
    from app.routers import setup as setup_router

    monkeypatch.setattr(setup_router, "scan_wifi", lambda: ["Live"])
    get_settings().setup_scan_file.write_text(json.dumps(["Cache", "Live"]))
    assert client.get("/api/setup/wifi").json() == ["Live", "Cache"]


def test_complete_creates_admin_and_network(client, setup_mode, monkeypatch):
    started = []
    monkeypatch.setattr(setup_wizard, "launch_finish", lambda cmd: started.append(cmd) or True)
    resp = client.post("/api/setup/complete", json=_payload())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["hostname"] == "offgridcloud-test1"
    assert body["wifi_ssid"] == "Basislager"
    assert body["finish_started"] is True
    assert len(started) == 1

    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == WIZARD_EMAIL))
        assert admin is not None and admin.role.value == "admin"
        known = db.scalars(select(KnownNetwork)).all()
        assert [n.ssid for n in known] == ["Basislager"]
        net = db.get(NetworkSettings, 1)
        assert net.fallback_enabled is True
        assert net.ap_ssid == "offgridcloud-test1"
        assert net.country_code == "DE"

    data_dir = get_settings().data_dir
    exported = json.loads(get_settings().network_config_path.read_text())
    assert exported["fallback_enabled"] is True
    assert exported["ap"]["passphrase"] == "rueckfall123"
    assert exported["known_networks"][0]["passphrase"] == "wlanpasswort"
    result = json.loads((data_dir / setup_wizard.RESULT_FILENAME).read_text())
    assert result == {"hostname": "offgridcloud-test1", "country": "DE"}

    # Setup mode is over once the account exists; it can log in.
    setup_mode["value"] = True
    setup_wizard.reset_cache()
    login = client.post(
        "/api/auth/login", json={"email": WIZARD_EMAIL, "password": "supergeheim"}
    )
    assert login.status_code == 200


def test_complete_without_wifi_keeps_current_uplink(client, setup_mode):
    resp = client.post("/api/setup/complete", json=_payload(wifi_ssid="", wifi_password=""))
    assert resp.status_code == 200, resp.text
    assert resp.json()["wifi_ssid"] == ""
    with SessionLocal() as db:
        assert db.scalars(select(KnownNetwork)).all() == []


@pytest.mark.parametrize(
    "override",
    [{"hostname": "-nope-"}, {"fallback_password": "kurz"}, {"wifi_password": "kurz"},
     {"country": "Deutschland"}],
)
def test_complete_rejects_bad_input(client, setup_mode, override):
    resp = client.post("/api/setup/complete", json=_payload(**override))
    assert resp.status_code == 422
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.email == WIZARD_EMAIL)) is None


def test_complete_requires_code_when_configured(client, setup_mode):
    state_file = get_settings().data_dir / setup_wizard.STATE_FILENAME
    state_file.write_text(
        json.dumps({"salt": "s", "code_sha256": setup_wizard.hash_code("4711", "s")})
    )
    assert client.get("/api/setup/status").json()["code_required"] is True
    for _ in range(setup_wizard.MAX_CODE_FAILURES):
        assert client.post("/api/setup/complete", json=_payload(code="0000")).status_code == 403
    # Locked out now — even the right code waits.
    assert client.post("/api/setup/complete", json=_payload(code="4711")).status_code == 429
    setup_wizard.throttle._locked_until = 0
    assert client.post("/api/setup/complete", json=_payload(code="4711")).status_code == 200


def test_endpoints_refuse_once_set_up(client, setup_mode):
    setup_mode["value"] = True
    setup_wizard.reset_cache()
    assert client.get("/api/setup/status").json()["required"] is False
    assert client.get("/api/setup/wifi").status_code == 409
    assert client.post("/api/setup/complete", json=_payload()).status_code == 409
