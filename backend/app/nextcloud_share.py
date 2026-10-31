"""Nextcloud public share links as an upload target.

A public share link (``https://cloud.example.com/s/<token>``) is reachable over
WebDAV at ``<base>/public.php/dav/files/<token>/`` with the share token as user
name and the (optional) share password as password. The admin only pastes the
link; everything rclone needs is derived from it here.

The vendor is ``nextcloud`` so rclone uploads in chunks: it derives the upload
directory from the ``/dav/files/<user>`` URL, which for shares yields
``<base>/public.php/dav/uploads/<token>/`` — the endpoint Nextcloud offers for
chunked uploads into public shares. Without chunking each file is one PUT, and
proxies in front of the server reject large ones (Cloudflare: 413 above ~100 MB).

Two things must line up for that: rclone >= 1.63 (older releases ignore the
vendor's chunking and send one PUT) and Nextcloud >= 32 (older servers have no
``/public.php/dav/uploads/`` collection for shares).
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from . import rclone
from .rclone_obscure import obscure

# rclone learned Nextcloud chunked uploads in 1.63; older ones PUT whole files.
MIN_RCLONE = (1, 63, 0)

# ``/s/<token>`` or ``/index.php/s/<token>``, optionally below a sub path.
_SHARE_PATH = re.compile(r"^(?P<prefix>.*?)(?:/index\.php)?/s/(?P<token>[A-Za-z0-9]+)/?$")

_PROPFIND_BODY = (
    '<?xml version="1.0"?>'
    '<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
    "<d:prop><oc:permissions/></d:prop></d:propfind>"
)
_OC_PERMISSIONS = "{http://owncloud.org/ns}permissions"


def parse_share_link(link: str) -> tuple[str, str]:
    """Return ``(webdav_url, token)`` for a share link; ``ValueError`` if invalid."""
    parts = urlsplit(link.strip())
    match = _SHARE_PATH.match(parts.path)
    if parts.scheme not in ("http", "https") or not parts.netloc or match is None:
        raise ValueError(
            "Kein gültiger Freigabelink — erwartet z. B. https://cloud.example.com/s/AbC123"
        )
    base = f"{parts.scheme}://{parts.netloc}{match['prefix']}"
    token = match["token"]
    return f"{base}/public.php/dav/files/{token}/", token


def share_rclone_options(config: dict[str, str]) -> dict[str, str]:
    """Build the rclone webdav options (minus ``type``) for a share link."""
    url, token = parse_share_link(config.get("link", ""))
    opts = {"url": url, "vendor": "nextcloud", "user": token}
    password = config.get("pass", "")
    if password:
        opts["pass"] = obscure(password)
    return opts


def _propfind(url: str, token: str, password: str, body: str = "") -> bytes | None:
    """Depth-0 PROPFIND as the share; the response body, or ``None`` on any error."""
    auth = base64.b64encode(f"{token}:{password}".encode()).decode()
    req = urllib.request.Request(
        url,
        data=body.encode() or None,
        method="PROPFIND",
        headers={
            "Depth": "0",
            "Content-Type": "application/xml",
            "Authorization": f"Basic {auth}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _fetch_permissions(url: str, token: str, password: str) -> str | None:
    """Return the share root's ``oc:permissions`` (``None`` when unknown)."""
    body = _propfind(url, token, password, _PROPFIND_BODY)
    if body is None:
        return None
    try:
        node = ET.fromstring(body).find(f".//{_OC_PERMISSIONS}")
    except ET.ParseError:
        return None
    return (node.text or "") if node is not None else None


def _probe_chunk_endpoint(url: str, token: str, password: str) -> bool | None:
    """Whether ``/public.php/dav/uploads/<token>/`` exists (``None``: couldn't tell).

    Nextcloud < 32 has no such collection and answers 404; anything that
    isn't a clear "not there" (timeouts, 5xx) stays unknown.
    """
    uploads = url.replace("/public.php/dav/files/", "/public.php/dav/uploads/", 1)
    auth = base64.b64encode(f"{token}:{password}".encode()).decode()
    req = urllib.request.Request(
        uploads, method="PROPFIND",
        headers={"Depth": "0", "Authorization": f"Basic {auth}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15):
            return True
    except urllib.error.HTTPError as exc:
        return False if exc.code in (404, 405, 409, 501) else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _has_chunk_endpoint(url: str, token: str, password: str) -> bool:
    """Whether the server offers chunked uploads into public shares (unknown: yes)."""
    return _probe_chunk_endpoint(url, token, password) is not False


# Upload-time probe results per share URL, so each transfer doesn't re-ask.
_CHUNK_TTL = 3600.0
_chunk_cache: dict[str, tuple[bool, float]] = {}
_chunk_lock = threading.Lock()


def _chunking_supported(url: str, token: str, password: str) -> bool | None:
    now = time.monotonic()
    with _chunk_lock:
        hit = _chunk_cache.get(url)
        if hit is not None and now - hit[1] < _CHUNK_TTL:
            return hit[0]
    result = _probe_chunk_endpoint(url, token, password)
    if result is not None:
        with _chunk_lock:
            _chunk_cache[url] = (result, now)
    return result


def share_upload_options(config: dict[str, str], options: dict[str, str]) -> dict[str, str]:
    """Turn chunking off for servers without the public chunk endpoint.

    rclone would otherwise fail every file above one chunk (10 MiB) with
    "Parent node does not exist" (409). A single PUT still works up to
    whatever the proxy in front of the server allows.
    """
    try:
        url, token = parse_share_link(config.get("link", ""))
    except ValueError:
        return options
    if _chunking_supported(url, token, config.get("pass", "")) is False:
        return {**options, "nextcloud_chunk_size": "0"}
    return options


DEFAULT_SPLIT_MB = 90


def share_split_limit(config: dict[str, str]) -> int | None:
    """Largest single upload in bytes when the server can't take chunks, else ``None``.

    Files above it are cut into parts (app.video_split). The default keeps
    clear of Cloudflare's 100 MB request limit in front of Hetzner Storage
    Share; the admin can set another value per share.
    """
    try:
        url, token = parse_share_link(config.get("link", ""))
    except ValueError:
        return None
    if _chunking_supported(url, token, config.get("pass", "")) is not False:
        return None
    try:
        mb = int(str(config.get("split_mb", "")).strip() or DEFAULT_SPLIT_MB)
    except ValueError:
        mb = DEFAULT_SPLIT_MB
    return max(5, mb) * 1_000_000


_TOO_LARGE_MARKERS = ("413", "payload too large", "request entity too large")


def explain_upload_error(message: str) -> str:
    """Add what to do when a big file can't get through a share link."""
    lower = message.lower()
    if any(m in lower for m in _TOO_LARGE_MARKERS) or "parent node does not exist" in lower:
        return (
            f"{message} — Datei zu groß für einen Upload am Stück, und diese "
            "Nextcloud nimmt über Freigabelinks keine Chunks an (erst ab "
            "Nextcloud 32). Größere Dateien werden automatisch geteilt; "
            "scheitert es trotzdem, „Max. Größe am Stück“ senken, Nextcloud "
            "aktualisieren oder den Provider „Nextcloud“ mit Konto und "
            "App-Passwort nutzen"
        )
    return message


def _server_version(url: str) -> str | None:
    """The Nextcloud version from the public ``status.php`` (``None`` if unknown)."""
    status_url = url.split("/public.php/", 1)[0] + "/status.php"
    try:
        with urllib.request.urlopen(status_url, timeout=10) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None
    version = data.get("versionstring") if isinstance(data, dict) else None
    return str(version) if version else None


def rclone_problem() -> str | None:
    """Explain why the installed rclone can't chunk, or ``None`` if it can (or is unknown)."""
    version = rclone.rclone_version()
    if version is None or version >= MIN_RCLONE:
        return None
    have = ".".join(map(str, version))
    need = ".".join(map(str, MIN_RCLONE))
    return (
        f"rclone {have} ist zu alt für Chunked-Upload (ab {need}) — große Dateien "
        "scheitern mit „413 Payload Too Large“. rclone aktualisieren "
        "(Docker: neues Image ziehen, sonst: sudo rclone selfupdate)"
    )


def share_write_problem(config: dict[str, str]) -> str | None:
    """Explain why the share can't take uploads, or ``None`` if it can (or is unknown).

    Uploading needs ``C`` (create files) and ``K`` (create folders, for the
    per-folder target paths). A read-only link only carries ``G``/``R``; a
    file drop can't be listed at all, which the rclone test already reports.
    An rclone too old to chunk at all is flagged as well. Servers without the
    public chunk endpoint still pass (see share_test_note).
    """
    try:
        url, token = parse_share_link(config.get("link", ""))
    except ValueError as exc:
        return str(exc)
    perms = _fetch_permissions(url, token, config.get("pass", ""))
    if perms is None:
        return None  # couldn't determine — don't block on a best-effort check
    if "C" not in perms or "K" not in perms:
        return (
            "Freigabe ist schreibgeschützt — in Nextcloud bei der Freigabe "
            "„Hochladen und Bearbeiten erlauben“ aktivieren"
        )
    return rclone_problem()


def share_test_note(config: dict[str, str]) -> str | None:
    """What a passing test should still mention: no chunked upload on this server."""
    try:
        url, token = parse_share_link(config.get("link", ""))
    except ValueError:
        return None
    if _has_chunk_endpoint(url, token, config.get("pass", "")):
        return None
    version = _server_version(url)
    which = f"Nextcloud {version}" if version else "Diese Nextcloud"
    return (
        f"{which} bietet keinen Chunked-Upload für Freigaben (erst ab "
        "Nextcloud 32). Dateien über „Max. Größe am Stück“ werden in Teile "
        "geschnitten: Videos in abspielbare Clips, andere Dateien in "
        "Stücke zum Zusammenfügen"
    )
