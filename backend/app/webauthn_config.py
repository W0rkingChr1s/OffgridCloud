"""WebAuthn origin/RP-ID derivation, allowlist, and challenge store.

Pure logic, no HTTP — unit-testable in isolation (same split as power.py /
https_config.py). The RP-ID a passkey binds to is derived per-request from the
browser Origin and validated against an allowlist so a forged Host header can't
make us accept an arbitrary RP-ID.
"""

from __future__ import annotations

import secrets
import time
from urllib.parse import urlparse


class OriginNotAllowed(ValueError):
    """Raised when a request Origin is not in the configured allowlist."""


def parse_origin(origin: str) -> tuple[str, str]:
    """Return (rp_id, normalised_origin) for an ``https://host[:port]`` string.

    rp_id is the bare hostname (no scheme/port). Raises ValueError on anything
    that isn't an http(s) URL with a host.
    """
    parsed = urlparse(origin.strip())
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise ValueError(f"invalid origin: {origin!r}")
    normalised = f"{parsed.scheme}://{parsed.netloc}"
    return parsed.hostname, normalised


def build_allowlist(*, state: dict[str, str], extra_origins: str) -> set[str]:
    """Allowed RP-IDs: <hostname>.local + domain from https_state.json, always
    localhost (dev), plus any comma-separated OGC_WEBAUTHN_EXTRA_ORIGINS."""
    allow: set[str] = {"localhost"}
    hostname = (state.get("hostname") or "").strip()
    domain = (state.get("domain") or "").strip()
    if hostname:
        allow.add(f"{hostname}.local")
    if domain:
        allow.add(domain)
    for extra in extra_origins.split(","):
        extra = extra.strip()
        if extra:
            allow.add(extra)
    return allow


def resolve_rp(origin: str, *, allowlist: set[str]) -> tuple[str, str]:
    """Parse ``origin`` and confirm its RP-ID is allowed. Returns (rp_id, origin)."""
    rp_id, normalised = parse_origin(origin)
    if rp_id not in allowlist:
        raise OriginNotAllowed(f"origin not allowed: {rp_id}")
    return rp_id, normalised
