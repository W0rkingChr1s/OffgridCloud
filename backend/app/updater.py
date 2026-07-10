"""Update checks against GitHub Releases.

The instance knows its own version (``app.__version__``) and asks the GitHub
Releases API for the latest published release. Pure helpers (version parsing /
comparison) are separated from the network call so they're easy to unit-test and
so the whole thing degrades gracefully when the box is offline (the common case
for an off-grid appliance).
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

_CACHE_TTL = 900.0  # seconds — don't hammer the API; releases change rarely


def parse_version(value: str) -> tuple[int, ...]:
    """Parse a version/tag like ``v1.2.3`` or ``0.0.1`` into a comparable tuple.

    Non-numeric suffixes (``-rc1``) are ignored for ordering; unknown formats
    return ``(0,)`` so they never look newer than a real release.
    """
    if not value:
        return (0,)
    cleaned = value.strip().lstrip("vV")
    match = re.match(r"(\d+(?:\.\d+)*)", cleaned)
    if not match:
        return (0,)
    return tuple(int(p) for p in match.group(1).split("."))


def is_newer(latest: str, current: str) -> bool:
    """True if ``latest`` is a strictly newer version than ``current``."""
    a, b = parse_version(latest), parse_version(current)
    length = max(len(a), len(b))
    a += (0,) * (length - len(a))
    b += (0,) * (length - len(b))
    return a > b


@dataclass
class UpdateInfo:
    current: str
    latest: str | None = None
    update_available: bool = False
    release_url: str = ""
    release_name: str = ""
    published_at: str = ""
    notes: str = ""
    error: str = ""
    checked_at: float = field(default_factory=lambda: 0.0)


_cache: dict[str, tuple[float, UpdateInfo]] = {}


def _fetch_latest_release(repo: str, timeout: float = 6.0) -> dict:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "OffgridCloud"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed GitHub host)
        return json.loads(resp.read().decode("utf-8"))


def check_for_update(
    current: str,
    repo: str,
    *,
    now: float | None = None,
    fetcher=_fetch_latest_release,
    use_cache: bool = True,
) -> UpdateInfo:
    """Return update status, caching successful lookups for a while.

    Never raises: on any error (offline, rate-limited, no releases yet) it
    returns an ``UpdateInfo`` with ``update_available=False`` and a message.
    """
    now = time.time() if now is None else now
    if use_cache:
        cached = _cache.get(repo)
        if cached and now - cached[0] < _CACHE_TTL:
            return cached[1]

    info = UpdateInfo(current=current, checked_at=now)
    try:
        data = fetcher(repo)
    except urllib.error.HTTPError as exc:
        # 404 = the repo simply has no published release yet — not a failure.
        info.error = (
            "Noch keine Releases veröffentlicht."
            if exc.code == 404
            else f"Update-Check fehlgeschlagen (HTTP {exc.code})."
        )
        return info
    except Exception:  # noqa: BLE001 - offline / rate-limited / DNS
        info.error = "Kein Update-Check möglich (offline?)."
        return info

    tag = str(data.get("tag_name") or data.get("name") or "")
    info.latest = tag or None
    info.release_url = str(data.get("html_url") or "")
    info.release_name = str(data.get("name") or tag)
    info.published_at = str(data.get("published_at") or "")
    info.notes = str(data.get("body") or "")[:4000]
    info.update_available = bool(tag) and is_newer(tag, current)

    _cache[repo] = (now, info)
    return info


def clear_cache() -> None:
    _cache.clear()
