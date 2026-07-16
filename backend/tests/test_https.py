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


@pytest.mark.parametrize("bad", ["", "   ", "has space", "under_score", "-lead", "trail-", "a" * 64])
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
