"""Bandwidth-aware scheduling.

Two controls:
  * **Throttle + schedules** (always effective): an rclone --bwlimit, optionally
    overridden by time windows (e.g. full speed at night, 50% by day).
  * **Minimum-bandwidth gate** (best-effort): pause starting new uploads while the
    last *observed* throughput is below a threshold. Self-correcting via a
    cooldown so a fresh transfer can re-measure the link.

Pure helpers take plain values so they're easy to unit-test.
"""

from __future__ import annotations

import json
import time as _time
import urllib.request
from collections.abc import Callable
from datetime import datetime, time, timedelta

from sqlalchemy.orm import Session

from .models import BandwidthPolicy

# After a measurement, keep gating decisions stable for this long; once stale,
# allow one transfer through so the link can be re-measured.
MEASUREMENT_COOLDOWN = timedelta(seconds=120)


def _utcnow_naive() -> datetime:
    # Stored timestamps are naive UTC; keep comparisons consistent.
    return datetime.utcnow()


def ensure_policy() -> None:
    from .db import SessionLocal

    with SessionLocal() as db:
        if db.get(BandwidthPolicy, 1) is None:
            db.add(BandwidthPolicy(id=1))
            db.commit()


def get_policy(db: Session) -> BandwidthPolicy:
    policy = db.get(BandwidthPolicy, 1)
    if policy is None:
        policy = BandwidthPolicy(id=1)
        db.add(policy)
        db.commit()
        db.refresh(policy)
    return policy


def parse_schedule(schedule_json: str) -> list[dict]:
    try:
        data = json.loads(schedule_json or "[]")
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _parse_hhmm(value: str) -> time | None:
    try:
        hh, mm = value.split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        return None


def _window_active(start: time, end: time, now: time) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= now < end
    # Window wraps past midnight (e.g. 22:00 -> 06:00).
    return now >= start or now < end


def effective_bwlimit(schedule: list[dict], base_kbps: int, now: datetime) -> int:
    """Return the bwlimit (KiB/s, 0 = unlimited) for ``now``.

    The first matching schedule window wins; otherwise the base limit applies.
    """
    now_t = now.time()
    for win in schedule:
        start = _parse_hhmm(str(win.get("start", "")))
        end = _parse_hhmm(str(win.get("end", "")))
        if start is None or end is None:
            continue
        if _window_active(start, end, now_t):
            try:
                return max(0, int(win.get("kbps", 0)))
            except (TypeError, ValueError):
                return base_kbps
    return base_kbps


def should_start(
    enabled: bool,
    min_kbps: int,
    last_kbps: float,
    last_measured_at: datetime | None,
    now: datetime,
) -> tuple[bool, str]:
    """Decide whether the worker may start a new transfer."""
    if not enabled or min_kbps <= 0:
        return True, ""
    if last_measured_at is None:
        return True, ""  # never measured -> let a transfer measure the link
    if now - last_measured_at > MEASUREMENT_COOLDOWN:
        return True, ""  # stale -> re-measure
    if last_kbps < min_kbps:
        return False, f"Bandbreite {last_kbps:.0f} KB/s unter Minimum {min_kbps} KB/s"
    return True, ""


def record_measurement(db: Session, kbps: float) -> None:
    if kbps <= 0:
        return
    policy = get_policy(db)
    policy.last_kbps = float(kbps)
    policy.last_measured_at = _utcnow_naive()
    db.commit()


def _http_download_size(url: str, timeout: float = 20.0) -> int:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (admin-set URL)
        return len(resp.read())


def active_probe(url: str, fetcher: Callable[[str], int] = _http_download_size) -> float:
    """Actively measure throughput by downloading ``url``. Returns KiB/s (0 on error)."""
    if not url:
        return 0.0
    start = _time.monotonic()
    try:
        size = fetcher(url)
    except Exception:  # noqa: BLE001 - any network error -> no measurement
        return 0.0
    elapsed = max(_time.monotonic() - start, 0.001)
    return size / 1024.0 / elapsed
