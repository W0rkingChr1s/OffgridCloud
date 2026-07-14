"""Best-effort host network facts: internal IP, external IP, online check.

These are used by the status announcements (startup / reconnect) — never on a
hot path — so every helper is short-timeout and swallows failures, returning a
sensible fallback rather than raising. Nothing here needs privileges or extra
dependencies (no ``ip``/``ifconfig`` parsing): the internal IP is discovered
with the classic connect-a-UDP-socket trick, the external IP via a couple of
public "what's my IP" endpoints, and connectivity with a plain TCP dial.
"""

from __future__ import annotations

import socket
import urllib.request

# Public endpoints that answer with the caller's external IP as plain text. A
# short list gives redundancy if one is blocked/down on a restricted uplink.
_EXTERNAL_IP_URLS = (
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
)

# TCP endpoints used purely as an "is the internet reachable" litmus. Well-known
# anycast resolvers on 443 answer near-instantly and are rarely firewalled.
_ONLINE_TARGETS = (
    ("1.1.1.1", 443),
    ("8.8.8.8", 443),
)


def internal_ip() -> str | None:
    """Best-effort LAN IPv4 of this host.

    Opens a UDP socket "towards" a public address and reads back the local
    address the OS would use — no packet is actually sent, and it works offline
    as long as a default route exists. Returns ``None`` if even that fails.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        return ip or None
    except OSError:
        return None
    finally:
        sock.close()


def external_ip(timeout: float = 4.0) -> str | None:
    """Best-effort public IPv4 as seen from the internet. ``None`` if offline."""
    for url in _EXTERNAL_IP_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "OffgridCloud"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                text = resp.read(64).decode("utf-8", "replace").strip()
            if _looks_like_ip(text):
                return text
        except OSError:
            continue
    return None


def _looks_like_ip(value: str) -> bool:
    """Cheap sanity check that a probe returned an address, not an error page."""
    try:
        socket.inet_aton(value)
        return True
    except OSError:
        return ":" in value and len(value) <= 45  # accept an IPv6 literal too


def is_online(timeout: float = 3.0) -> bool:
    """Best-effort connectivity check: can we open a TCP connection out?

    Tries a couple of well-known endpoints so one blocked target doesn't read as
    "offline". Cheap enough to poll on an interval.
    """
    for host, port in _ONLINE_TARGETS:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False
