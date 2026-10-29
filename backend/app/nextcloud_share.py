"""Nextcloud/ownCloud public share links as an upload target.

A public share link (``https://cloud.example.com/s/<token>``) is reachable over
WebDAV at ``<base>/public.php/webdav/`` with the share token as user name and
the (optional) share password as password. The admin only pastes the link;
everything rclone needs is derived from it here.

The rclone vendor stays ``other``: ``vendor=nextcloud`` switches rclone to
chunked uploads via ``/remote.php/dav/uploads/<user>``, which does not exist
for public shares.
"""

from __future__ import annotations

import base64
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from .rclone_obscure import obscure

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
    return f"{base}/public.php/webdav/", match["token"]


def share_rclone_options(config: dict[str, str]) -> dict[str, str]:
    """Build the rclone webdav options (minus ``type``) for a share link."""
    url, token = parse_share_link(config.get("link", ""))
    opts = {"url": url, "vendor": "other", "user": token}
    password = config.get("pass", "")
    if password:
        opts["pass"] = obscure(password)
    return opts


def _fetch_permissions(url: str, token: str, password: str) -> str | None:
    """Return the share root's ``oc:permissions`` (``None`` when unknown)."""
    auth = base64.b64encode(f"{token}:{password}".encode()).decode()
    req = urllib.request.Request(
        url,
        data=_PROPFIND_BODY.encode(),
        method="PROPFIND",
        headers={
            "Depth": "0",
            "Content-Type": "application/xml",
            "Authorization": f"Basic {auth}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
    except (urllib.error.URLError, OSError, ValueError):
        return None
    try:
        node = ET.fromstring(body).find(f".//{_OC_PERMISSIONS}")
    except ET.ParseError:
        return None
    return (node.text or "") if node is not None else None


def share_write_problem(config: dict[str, str]) -> str | None:
    """Explain why the share can't take uploads, or ``None`` if it can (or is unknown).

    Uploading needs ``C`` (create files) and ``K`` (create folders, for the
    per-folder target paths). A read-only link only carries ``G``/``R``; a
    file drop can't be listed at all, which the rclone test already reports.
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
    return None
