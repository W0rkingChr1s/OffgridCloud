"""HTTPS reverse-proxy config: helpers + endpoints (self-signed LAN + optional domain)."""

from __future__ import annotations

from app.config import Settings


def test_https_apply_command_defaults_empty():
    # Empty by default → feature counts as "not set up" (button hidden / 409),
    # exactly like restart_service_command et al. before the installer wires it.
    assert Settings().https_apply_command == ""
