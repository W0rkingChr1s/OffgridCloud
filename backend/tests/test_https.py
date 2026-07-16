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
