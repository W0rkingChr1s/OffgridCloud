"""Link bundling ("Leitungsbündelung") via net-merge.

`net-merge <https://github.com/net-merge/net-merge>`_ spreads connections over
every uplink the box has — cable, Wi-Fi, a USB-tethered phone — instead of
letting the default route pick one. OffgridCloud runs it as a plain SOCKS5
proxy on loopback (``deploy/netmerge/install.sh``) and routes only rclone
through it. Nothing else on the box changes: the web UI, the fallback AP, the
VPN and local devices keep using the normal routing table.

What it buys and what not:

* net-merge balances per *connection*, not per packet. A single-stream upload
  (WebDAV, SFTP, a small S3 object) still uses one link. Multipart uploads —
  rclone sends S3/Azure/B2 chunks of large files over several connections by
  default — spread over all links and get faster.
* It only helps when the links reach the internet independently (LTE phone +
  Wi-Fi). Cable and Wi-Fi into the same router share one line.

When the proxy is bypassed (rclone goes direct, exactly as without net-merge):

* bundling switched off, or net-merge not installed / not answering — uploads
  must never fail because the helper is down;
* the destination resolves to a private, loopback or link-local address (a NAS
  in the LAN or behind the WireGuard tunnel): net-merge pins its sockets to the
  physical links, so such a target would be unreachable through it;
* an OpenVPN tunnel is up: it may carry the default route, and bundling would
  silently route the uploads around it;
* a metered link (USB-tethered phone) is connected while the metered rule is
  "pause" or "limit": net-merge would push part of every upload through the
  phone and the daily budget couldn't be accounted. With "allow" the phone is
  bundled like any other link.
"""

from __future__ import annotations

import ipaddress
import logging
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .config import get_settings

logger = logging.getLogger(__name__)

# rclone backends that honour the standard HTTPS_PROXY variable (Go's
# http.ProxyFromEnvironment accepts socks5:// URLs).
HTTP_BACKENDS = frozenset(
    {"s3", "b2", "gcs", "azureblob", "drive", "dropbox", "onedrive", "box", "pcloud",
     "mega", "webdav"}
)
# Raw-TCP backends with their own ``socks_proxy`` option (host:port).
SOCKS_OPTION_BACKENDS = frozenset({"sftp", "ftp"})
# Option keys that carry the destination host, in order of preference.
_HOST_KEYS = ("endpoint", "url", "host", "hostname")


@dataclass(frozen=True)
class Decision:
    """Whether one upload goes through net-merge — and why not, if it doesn't."""

    proxy: str = ""  # socks5://host:port, empty = direct
    reason: str = ""


def proxy_url() -> str:
    """The configured net-merge SOCKS5 address as ``socks5://host:port`` (or ``""``)."""
    raw = get_settings().netmerge_proxy.strip()
    if not raw:
        return ""
    return raw if "://" in raw else f"socks5://{raw}"


def _host_port(url: str) -> tuple[str, int] | None:
    parts = urlsplit(url)
    if not parts.hostname:
        return None
    return parts.hostname, parts.port or 1080


def proxy_alive(url: str, timeout: float = 0.5) -> bool:
    """True if something accepts connections on the proxy address."""
    hp = _host_port(url)
    if hp is None:
        return False
    try:
        with socket.create_connection(hp, timeout=timeout):
            return True
    except OSError:
        return False


def destination_host(options: dict[str, str]) -> str:
    """Hostname of the upload target from rclone remote options (``""`` = provider default)."""
    for key in _HOST_KEYS:
        value = (options.get(key) or "").strip()
        if not value:
            continue
        if "://" in value:
            return urlsplit(value).hostname or ""
        # Bare "host" or "host:port" (IPv6 literals come bracketed or bare).
        if value.startswith("["):
            return value[1:].split("]", 1)[0]
        return value.rsplit(":", 1)[0] if value.count(":") == 1 else value
    return ""


def _is_internal(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return True  # unparsable → don't risk it
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def is_internal_host(host: str, resolver=socket.getaddrinfo) -> bool:
    """True if ``host`` is (or resolves to) a non-public address.

    A name that doesn't resolve also counts: it may only exist in the VPN's or
    the LAN's DNS, and the direct route is what worked before bundling.
    """
    if not host:
        return False
    try:
        infos = resolver(host, None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return True
    addrs = {info[4][0] for info in infos}
    return not addrs or any(_is_internal(a) for a in addrs)


def decide(
    *,
    enabled: bool,
    options: dict[str, str],
    metered_mode: str,
    metered_present: bool,
    openvpn_active: bool,
    proxy: str | None = None,
    alive=None,
    resolver=socket.getaddrinfo,
) -> Decision:
    """Pick direct or bundled for one upload (see the module docstring)."""
    if not enabled:
        return Decision(reason="aus")
    proxy = proxy_url() if proxy is None else proxy
    if not proxy:
        return Decision(reason="net-merge nicht eingerichtet")
    backend = options.get("type", "")
    if backend not in HTTP_BACKENDS and backend not in SOCKS_OPTION_BACKENDS:
        return Decision(reason=f"Ziel-Typ {backend or '?'} unterstützt keinen Proxy")
    if metered_present and metered_mode in ("pause", "limit"):
        return Decision(reason="getaktete Verbindung angeschlossen")
    if openvpn_active:
        return Decision(reason="OpenVPN aktiv")
    if is_internal_host(destination_host(options), resolver):
        return Decision(reason="internes Ziel (LAN/VPN)")
    if not (alive or proxy_alive)(proxy):
        return Decision(reason="net-merge antwortet nicht")
    return Decision(proxy=proxy)


def apply_proxy(env: dict[str, str], remote: str, options: dict[str, str], proxy: str) -> None:
    """Point one rclone run at ``proxy`` (mutates ``env``)."""
    if not proxy:
        return
    if options.get("type") in SOCKS_OPTION_BACKENDS:
        hp = _host_port(proxy)
        if hp:
            env[f"RCLONE_CONFIG_{remote.upper()}_SOCKS_PROXY"] = f"{hp[0]}:{hp[1]}"
        return
    env["HTTPS_PROXY"] = proxy
    env["HTTP_PROXY"] = proxy
    env.pop("NO_PROXY", None)
    env.pop("no_proxy", None)


# --------------------------------------------------------------------------- #
# Status for the UI: which links net-merge would use (``net-merge list``)      #
# --------------------------------------------------------------------------- #
@dataclass
class Link:
    name: str
    used: bool
    detail: str = ""  # address (used) or skip reason


@dataclass
class Status:
    installed: bool = False
    running: bool = False
    links: list[Link] = field(default_factory=list)


def parse_list(raw: str) -> list[Link]:
    """Parse ``net-merge list``: ``USE   eth0  v4 1.2.3.4  (index 2)`` / ``skip  wg0: reason``."""
    links: list[Link] = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("USE "):
            rest = line[4:].split("(index", 1)[0].strip()
            name, _, detail = rest.partition(" ")
            links.append(Link(name, True, " ".join(detail.split())))
        elif line.startswith("skip "):
            name, _, reason = line[5:].strip().partition(":")
            links.append(Link(name.strip(), False, reason.strip()))
    return links


STATUS_CACHE_SECONDS = 10.0
_status_cache: tuple[float, Status] | None = None
_status_lock = threading.Lock()


def get_status() -> Status:
    """Installed/running state plus the links net-merge sees (cached briefly)."""
    global _status_cache
    with _status_lock:
        now = time.monotonic()
        if _status_cache is not None and now - _status_cache[0] < STATUS_CACHE_SECONDS:
            return _status_cache[1]
        st = Status()
        binary = shutil.which(get_settings().netmerge_binary)
        proxy = proxy_url()
        st.installed = bool(binary and proxy)
        if st.installed:
            st.running = proxy_alive(proxy)
            try:
                res = subprocess.run(
                    [binary, "list", "-ipv6", "off"],
                    capture_output=True, text=True, timeout=10,
                )
                st.links = parse_list(res.stdout)
            except (subprocess.SubprocessError, OSError):
                logger.debug("net-merge list failed", exc_info=True)
        _status_cache = (now, st)
        return st


def reset_status_cache() -> None:
    global _status_cache
    with _status_lock:
        _status_cache = None
