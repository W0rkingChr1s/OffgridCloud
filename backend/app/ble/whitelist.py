"""Which REST endpoints the BLE bridge forwards (BLE-PROTOKOLL.md §7.5).

Authoritative for the box; the companion app mirrors it only to grey out
features. Patterns match whole path segments.
"""

from __future__ import annotations

import re

READ = frozenset({"GET"})
WRITE = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
POST = frozenset({"POST"})
HANDOVER = frozenset({"GET", "POST", "DELETE"})
READ_PUT = frozenset({"GET", "PUT"})

_DENY = (
    # Formatting over the air is ruled out.
    re.compile(r"^/api/storage/format$"),
    # File downloads never go over BLE.
    re.compile(r"^/api/folders/[^/]+/download$"),
)

_ALLOW: tuple[tuple[frozenset[str], re.Pattern[str]], ...] = (
    (READ, re.compile(r"^/api/health$")),
    (READ, re.compile(r"^/api/monitor(/history)?$")),
    (WRITE, re.compile(r"^/api/network(/.*)?$")),
    (WRITE, re.compile(r"^/api/transfers(/.*)?$")),
    (WRITE, re.compile(r"^/api/bandwidth(/.*)?$")),
    (WRITE, re.compile(r"^/api/vpn(/.*)?$")),
    (WRITE, re.compile(r"^/api/storage(/.*)?$")),
    (POST, re.compile(r"^/api/system/power/[^/]+$")),
    (WRITE, re.compile(r"^/api/updates(/.*)?$")),
    (READ, re.compile(r"^/api/folders(/.*)?$")),
    (READ, re.compile(r"^/api/media/search$")),
    (HANDOVER, re.compile(r"^/api/uplink/handover$")),
    # Pool: only what is needed in the field next to the hub. Access management
    # (tokens, peers by URL + token) stays in the web UI.
    (READ, re.compile(r"^/api/pool/overview$")),
    (POST, re.compile(r"^/api/pool/peers/\d+/control$")),
    (READ, re.compile(r"^/api/pool/peers/\d+/jobs$")),
    (POST, re.compile(r"^/api/pool/peers/\d+/jobs/\d+/retry$")),
    (HANDOVER, re.compile(r"^/api/pool/pairing$")),
    (READ, re.compile(r"^/api/pool/discovery$")),
    (POST, re.compile(r"^/api/pool/peers/pair$")),
    (READ_PUT, re.compile(r"^/api/pool/priority$")),
)

METHODS = WRITE


def api_allowed(method: str, path: str) -> bool:
    if ".." in path or "//" in path or any(p.match(path) for p in _DENY):
        return False
    return any(method in methods and pattern.match(path) for methods, pattern in _ALLOW)
