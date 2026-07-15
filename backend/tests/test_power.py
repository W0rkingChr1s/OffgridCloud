"""System power control: helper + endpoints (restart service / reboot / shutdown)."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.power import run_power_command


def test_run_power_command_launches_detached_shell():
    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return object()

    run_power_command("sudo systemctl restart offgridcloud", delay=0.5, popen=fake_popen)

    assert captured["argv"][0] == "sh"
    assert captured["argv"][1] == "-c"
    # The delay runs in the child so the request handler returns immediately.
    assert captured["argv"][2] == "sleep 0.5; exec sudo systemctl restart offgridcloud"
    # Detached so it survives our own termination (the restart kills us).
    assert captured["kwargs"]["start_new_session"] is True


def test_run_power_command_rejects_empty():
    with pytest.raises(ValueError):
        run_power_command("   ")


def test_power_status_flags_default_on(client, admin_auth):
    # Power control is active out of the box now (config ships default commands).
    body = client.get("/api/system", headers=admin_auth).json()
    assert body["power_restart_service_enabled"] is True
    assert body["power_reboot_enabled"] is True
    assert body["power_shutdown_enabled"] is True


def test_power_action_disabled_when_command_cleared(client, admin_auth, monkeypatch):
    # Clearing a command opts that single action out -> 409, nothing is launched.
    monkeypatch.setattr(get_settings(), "restart_service_command", "")
    r = client.post("/api/system/power/restart-service", headers=admin_auth)
    assert r.status_code == 409
    assert client.get("/api/system", headers=admin_auth).json()[
        "power_restart_service_enabled"
    ] is False


def test_power_action_unknown_slug_is_404(client, admin_auth):
    r = client.post("/api/system/power/self-destruct", headers=admin_auth)
    assert r.status_code == 404


def test_power_action_requires_admin(client, admin_auth):
    client.post(
        "/api/users",
        headers=admin_auth,
        json={"email": "plainpwr@test.local", "password": "userpass123"},
    )
    token = client.post(
        "/api/auth/login", json={"email": "plainpwr@test.local", "password": "userpass123"}
    ).json()["access_token"]
    r = client.post(
        "/api/system/power/reboot", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403


def test_power_action_runs_configured_command(client, admin_auth, monkeypatch):
    import app.routers.system as sys_router

    # Wire a command on the cached settings + stub the launcher so nothing spawns.
    monkeypatch.setattr(get_settings(), "reboot_command", "sudo systemctl reboot")
    launched = {}
    monkeypatch.setattr(
        sys_router, "run_power_command", lambda cmd: launched.setdefault("cmd", cmd)
    )

    r = client.post("/api/system/power/reboot", headers=admin_auth)
    assert r.status_code == 200
    body = r.json()
    assert body["started"] is True
    assert launched["cmd"] == "sudo systemctl reboot"

    # The status now reflects the enabled action, and the run is audited.
    assert client.get("/api/system", headers=admin_auth).json()["power_reboot_enabled"] is True
    events = client.get("/api/system/audit", headers=admin_auth).json()
    assert any(e["action"] == "system.power.reboot" for e in events)
