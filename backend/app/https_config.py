"""HTTPS reverse-proxy config helpers.

Pure logic (validation + reading the state file that ``deploy/https/apply.sh``
writes) kept separate from the FastAPI router so it's unit-testable without
HTTP — same split as ``power.py`` vs. ``routers/system.py``. The privileged
work (rendering the Caddyfile, reloading Caddy, setting the hostname) lives in
the bash script; Python only validates input and shells out to it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_STATE_FILENAME = "https_state.json"

# mDNS short name: DNS label rules — letters/digits/hyphen, no leading/trailing
# hyphen, 1–63 chars. Avahi appends ".local" itself.
_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
# Public domain: one or more dot-separated DNS labels (needs at least one dot).
_DOMAIN_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN_RE = re.compile(rf"^{_DOMAIN_LABEL}(\.{_DOMAIN_LABEL})+$")


def normalise_hostname(value: str) -> str:
    """Lowercase, trim, and strip a trailing ``.local`` (avahi adds it back)."""
    cleaned = value.strip().lower()
    if cleaned.endswith(".local"):
        cleaned = cleaned[: -len(".local")]
    return cleaned


def validate_hostname(value: str) -> str:
    """Return ``value`` if it's a valid mDNS short name, else raise ValueError."""
    if not _HOSTNAME_RE.match(value):
        raise ValueError(
            "Ungültiger Hostname. Erlaubt: Buchstaben, Ziffern und Bindestriche "
            "(kein Bindestrich am Anfang/Ende), 1–63 Zeichen."
        )
    return value


def validate_domain(value: str) -> str:
    """Normalise + validate a public domain. Empty string means 'no domain'."""
    cleaned = value.strip().lower()
    if cleaned == "":
        return ""
    if not _DOMAIN_RE.match(cleaned):
        raise ValueError(
            "Ungültige Domain. Erwartet z. B. cloud.example.com "
            "(ohne http://, mindestens ein Punkt)."
        )
    return cleaned


def read_state(data_dir: Path) -> dict[str, str]:
    """Read ``<data_dir>/https_state.json`` written by apply.sh.

    Missing or unreadable → defaults, so the endpoint never fails hard on a box
    where HTTPS was never set up.
    """
    path = Path(data_dir) / _STATE_FILENAME
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"hostname": "", "domain": ""}
    return {
        "hostname": str(raw.get("hostname", "")),
        "domain": str(raw.get("domain", "")),
    }
