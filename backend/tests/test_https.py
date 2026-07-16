"""HTTPS reverse-proxy config: helpers + endpoints (self-signed LAN + optional domain)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import https_config
from app.config import Settings


def test_https_apply_command_defaults_empty():
    # Empty by default → feature counts as "not set up" (button hidden / 409),
    # exactly like restart_service_command et al. before the installer wires it.
    assert Settings().https_apply_command == ""


def test_normalise_hostname_strips_local_suffix_and_lowercases():
    assert https_config.normalise_hostname("OffgridCloud.local") == "offgridcloud"
    assert https_config.normalise_hostname("  box1  ") == "box1"


@pytest.mark.parametrize(
    "bad", ["", "   ", "has space", "under_score", "-lead", "trail-", "a" * 64]
)
def test_validate_hostname_rejects_bad(bad):
    with pytest.raises(ValueError):
        https_config.validate_hostname(https_config.normalise_hostname(bad))


def test_validate_hostname_accepts_good():
    assert https_config.validate_hostname("offgridcloud") == "offgridcloud"
    assert https_config.validate_hostname("box-1") == "box-1"


@pytest.mark.parametrize("bad", ["no dots", "-lead.com", "http://x.com", "a..b.com", "space .com"])
def test_validate_domain_rejects_bad(bad):
    with pytest.raises(ValueError):
        https_config.validate_domain(bad)


def test_validate_domain_accepts_good_and_empty():
    # Empty domain is valid → "no public domain, LAN only".
    assert https_config.validate_domain("") == ""
    assert https_config.validate_domain("  Cloud.Example.COM ") == "cloud.example.com"


def test_read_state_missing_file_returns_defaults(tmp_path: Path):
    state = https_config.read_state(tmp_path)
    assert state == {"hostname": "", "domain": ""}


def test_read_state_reads_written_file(tmp_path: Path):
    (tmp_path / "https_state.json").write_text('{"hostname": "box1", "domain": "x.com"}')
    assert https_config.read_state(tmp_path) == {"hostname": "box1", "domain": "x.com"}


def test_read_state_tolerates_garbage(tmp_path: Path):
    (tmp_path / "https_state.json").write_text("not json{")
    assert https_config.read_state(tmp_path) == {"hostname": "", "domain": ""}


def test_run_apply_builds_command_and_succeeds():
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs

        class R:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return R()

    https_config.run_apply(
        "sudo /opt/offgridcloud/deploy/https/apply.sh",
        hostname="box1",
        domain="cloud.example.com",
        run=fake_run,
    )

    # The command string is split (trusted, operator-configured) and the two
    # flags appended. Domain passed through because it's non-empty.
    assert captured["argv"] == [
        "sudo",
        "/opt/offgridcloud/deploy/https/apply.sh",
        "--hostname",
        "box1",
        "--domain",
        "cloud.example.com",
    ]
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["timeout"] == 30


def test_run_apply_omits_domain_flag_when_empty():
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    https_config.run_apply("sudo apply.sh", hostname="box1", domain="", run=fake_run)
    assert "--domain" not in captured["argv"]
    assert captured["argv"] == ["sudo", "apply.sh", "--hostname", "box1"]


def test_run_apply_raises_with_stderr_tail_on_failure():
    def fake_run(argv, **kwargs):
        class R:
            returncode = 1
            stdout = ""
            stderr = "caddy validate failed: bad domain\n"

        return R()

    with pytest.raises(RuntimeError) as exc:
        https_config.run_apply("sudo apply.sh", hostname="box1", domain="", run=fake_run)
    assert "caddy validate failed" in str(exc.value)


def test_run_apply_rejects_empty_command():
    with pytest.raises(ValueError):
        https_config.run_apply("   ", hostname="box1", domain="")


def test_https_status_disabled_by_default(client, admin_auth):
    # No https_apply_command configured in the test settings → enabled False.
    body = client.get("/api/system/https", headers=admin_auth).json()
    assert body["enabled"] is False
    assert body["hostname"] == ""
    assert body["lan_url"] == ""


def test_https_status_requires_admin(client, admin_auth):
    # No user_auth fixture exists — create a plain user inline (pattern from
    # test_users.test_non_admin_cannot_manage_users).
    client.post(
        "/api/users",
        headers=admin_auth,
        json={"email": "plain@test.local", "password": "userpass123", "role": "user"},
    )
    token = client.post(
        "/api/auth/login", json={"email": "plain@test.local", "password": "userpass123"}
    ).json()["access_token"]
    user_auth = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/system/https", headers=user_auth).status_code == 403


def test_https_put_returns_409_when_not_configured(client, admin_auth):
    resp = client.put("/api/system/https", headers=admin_auth, json={"hostname": "box1"})
    assert resp.status_code == 409
    assert "Installer" in resp.json()["detail"]


def test_https_status_reports_urls_from_state(client, admin_auth, monkeypatch):
    import app.routers.https as https_router

    monkeypatch.setattr(
        https_router.https_config,
        "read_state",
        lambda data_dir: {"hostname": "box1", "domain": "cloud.example.com"},
    )
    body = client.get("/api/system/https", headers=admin_auth).json()
    assert body["hostname"] == "box1"
    assert body["lan_url"] == "https://box1.local"
    assert body["public_url"] == "https://cloud.example.com"


def test_https_put_runs_apply_and_returns_new_state(client, admin_auth, monkeypatch):
    import app.routers.https as https_router
    from app.config import get_settings

    # Pretend the box is wired up.
    settings = get_settings()
    monkeypatch.setattr(settings, "https_apply_command", "sudo apply.sh", raising=False)

    calls = {}

    def fake_run_apply(command, *, hostname, domain, run=None):
        calls["hostname"] = hostname
        calls["domain"] = domain
        return "ok"

    monkeypatch.setattr(https_router.https_config, "run_apply", fake_run_apply)
    monkeypatch.setattr(
        https_router.https_config,
        "read_state",
        lambda data_dir: {"hostname": "box2", "domain": ""},
    )

    resp = client.put(
        "/api/system/https", headers=admin_auth, json={"hostname": "Box2.local", "domain": ""}
    )
    assert resp.status_code == 200
    # Hostname was normalised (.local stripped, lowercased) before apply.
    assert calls["hostname"] == "box2"
    assert calls["domain"] == ""
    assert resp.json()["hostname"] == "box2"


def test_https_put_rejects_bad_hostname(client, admin_auth, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "https_apply_command", "sudo apply.sh", raising=False)
    resp = client.put(
        "/api/system/https", headers=admin_auth, json={"hostname": "bad host!"}
    )
    assert resp.status_code == 422 or resp.status_code == 400
