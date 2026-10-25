"""Status announcements: startup, reconnect, and bandwidth pause/resume.

These complement the per-media notifications (upload received/done/failed) with
*operational* status blips the field team cares about:

* **Startup** — a comprehensive one-shot summary when the server comes up (time,
  storage, connected cloud targets, VPN, external/internal IP, queued transfers,
  measured bandwidth, pooled devices).
* **Reconnect** — a short "back online" ping after the uplink drops and returns,
  with the fresh bandwidth and IPs.
* **Bandwidth gate** — one message when the minimum-bandwidth gate pauses sending
  and another when it resumes.

Every announcement fans out to the same channels as :mod:`app.notify` (webhook /
Telegram / e-mail) *and* pushes an in-app :mod:`app.notices` toast, gated by the
matching ``notify_on_*`` system setting. All of it is best-effort: gathering the
report and sending are wrapped so a hiccup never disturbs startup or the worker.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import netinfo, notices, notify
from . import pool as pool_core
from .admin_ops import disk_usage, get_system_settings
from .bandwidth import get_policy
from .models import CloudProvider, PoolPeer, ProviderStatus, TransferJob, TransferStatus

logger = logging.getLogger("offgridcloud.announce")


# --- Formatting helpers (pure) --------------------------------------------


def _format_bytes(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def format_kbps(kbps: float) -> str:
    """Human throughput. ``kbps`` is KiB/s (as tracked by the bandwidth policy)."""
    if kbps <= 0:
        return "unbekannt"
    if kbps >= 1024:
        return f"{kbps / 1024:.1f} MB/s"
    return f"{kbps:.0f} KB/s"


def format_startup_lines(report: dict) -> list[str]:
    """Render the gathered startup ``report`` into human-readable lines (pure)."""
    disk = report["disk"]
    lines = [
        f"Start: {report['started_at']}",
        f"Speicher: {_format_bytes(disk['free'])} frei von "
        f"{_format_bytes(disk['total'])} (belegt {disk['percent_used']:.0f}%)",
    ]

    prov = report["providers"]
    if prov["total"]:
        names = ", ".join(prov["connected_names"]) if prov["connected_names"] else "keine"
        lines.append(
            f"Cloud-Ziele: {prov['connected']}/{prov['total']} verbunden ({names})"
        )
    else:
        lines.append("Cloud-Ziele: keine konfiguriert")

    lines.append(f"VPN: {'verbunden' if report['vpn_connected'] else 'getrennt'}")
    lines.append(f"Externe IP: {report['external_ip'] or 'unbekannt'}")
    lines.append(f"Interne IP: {report['internal_ip'] or 'unbekannt'}")
    lines.append(f"Warteschlange: {report['queued']} Übertragung(en)")
    lines.append(f"Bandbreite: {format_kbps(report['bandwidth_kbps'])}")

    pool = report["pool"]
    if pool["total"]:
        lines.append(f"Pool: {pool['online']}/{pool['total']} Geräte verbunden")
    return lines


def format_duration(seconds: float) -> str:
    """Compact German duration: ``45 s``, ``4 min``, ``1 h 12 min``."""
    secs = max(0, int(round(seconds)))
    if secs < 60:
        return f"{secs} s"
    minutes = secs // 60
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def format_reconnect_message(
    kbps: float,
    external_ip: str | None,
    internal_ip: str | None,
    outage_seconds: float | None = None,
) -> str:
    head = (
        f"Verbindung nach {format_duration(outage_seconds)} wiederhergestellt."
        if outage_seconds is not None
        else "Verbindung wiederhergestellt."
    )
    return (
        f"{head} Bandbreite: {format_kbps(kbps)}. "
        f"Externe IP: {external_ip or 'unbekannt'}, interne IP: {internal_ip or 'unbekannt'}."
    )


# --- Report gathering -----------------------------------------------------


def gather_startup_report(db: Session) -> dict:
    """Collect the comprehensive status snapshot for the startup announcement.

    Best-effort: individual probes (external IP, VPN, pool poll) are guarded so
    one failing source degrades to a placeholder rather than aborting the whole
    report.
    """
    providers = list(db.scalars(select(CloudProvider).order_by(CloudProvider.name)))
    connected = [p.name for p in providers if p.status == ProviderStatus.OK]

    queued = (
        db.scalar(
            select(func.count(TransferJob.id)).where(
                TransferJob.status == TransferStatus.QUEUED
            )
        )
        or 0
    )

    return {
        "started_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "disk": disk_usage(),
        "providers": {
            "total": len(providers),
            "connected": len(connected),
            "connected_names": connected,
        },
        "vpn_connected": _vpn_connected(),
        "external_ip": netinfo.external_ip(),
        "internal_ip": netinfo.internal_ip(),
        "queued": int(queued),
        "bandwidth_kbps": round(get_policy(db).last_kbps, 1),
        "pool": _pool_summary(db),
    }


def _vpn_connected() -> bool:
    try:
        from . import vpn as vpnsvc

        return vpnsvc.active_id() is not None
    except Exception:  # noqa: BLE001 - VPN is optional; never let it break the report
        logger.debug("VPN status probe failed", exc_info=True)
        return False


def _pool_summary(db: Session) -> dict:
    """Reachable/total count of enabled pool peers (best-effort poll)."""
    try:
        peers = list(db.scalars(select(PoolPeer).where(PoolPeer.enabled.is_(True))))
        if not peers:
            return {"total": 0, "online": 0}
        polled = pool_core.poll_peers(peers)
        online = sum(1 for data in polled.values() if data.get("reachable"))
        return {"total": len(peers), "online": online}
    except Exception:  # noqa: BLE001 - pool poll must never break the report
        logger.debug("Pool summary failed", exc_info=True)
        return {"total": 0, "online": 0}


# --- Dispatch -------------------------------------------------------------


def _announce(
    db: Session, *, event: str, level: str, title: str, message: str, payload: dict
) -> None:
    """Push an in-app notice and fan out to the notify channels, gated by the
    event's ``notify_on_*`` toggle so an admin can silence each category."""
    settings = get_system_settings(db)
    flag = notify.EVENT_TOGGLE.get(event)
    if flag is not None and not getattr(settings, flag, False):
        return
    notices.push(level, title, message)
    notify.dispatch(settings, event, title, message, payload)


def announce_startup() -> None:
    """Gather and send the comprehensive startup summary. Safe to call in a
    background thread — opens its own session and swallows every error."""
    from .db import SessionLocal

    try:
        with SessionLocal() as db:
            report = gather_startup_report(db)
            lines = format_startup_lines(report)
            _announce(
                db,
                event="server.startup",
                level="info",
                title="OffgridCloud gestartet",
                message="\n".join(lines),
                payload={"event": "server.startup", **report},
            )
    except Exception:  # noqa: BLE001 - startup notice must never break boot
        logger.warning("Startup announcement failed", exc_info=True)


def announce_reconnect(db: Session, outage_seconds: float | None = None) -> None:
    """Send the short "back online" ping with fresh bandwidth + IPs."""
    kbps = round(get_policy(db).last_kbps, 1)
    ext = netinfo.external_ip()
    internal = netinfo.internal_ip()
    _announce(
        db,
        event="server.online",
        level="success",
        title="Wieder online",
        message=format_reconnect_message(kbps, ext, internal, outage_seconds),
        payload={
            "event": "server.online",
            "bandwidth_kbps": kbps,
            "external_ip": ext,
            "internal_ip": internal,
            "outage_seconds": None if outage_seconds is None else round(outage_seconds),
        },
    )


def announce_bandwidth_paused(db: Session, reason: str) -> None:
    _announce(
        db,
        event="transfer.paused",
        level="warning",
        title="Übertragung pausiert",
        message=f"Senden wegen zu geringer Bandbreite gestoppt. {reason}".strip(),
        payload={"event": "transfer.paused", "reason": reason},
    )


def announce_bandwidth_resumed(db: Session, kbps: float) -> None:
    _announce(
        db,
        event="transfer.resumed",
        level="success",
        title="Übertragung fortgesetzt",
        message=f"Bandbreite wieder ausreichend ({format_kbps(kbps)}) — Senden läuft weiter.",
        payload={"event": "transfer.resumed", "bandwidth_kbps": round(kbps, 1)},
    )


# --- Connectivity monitor -------------------------------------------------

# Consecutive failed probes before the uplink counts as down. One probe is two
# 3 s TCP dials, so a short Wi-Fi hiccup (power save, roaming, a lost SYN) used
# to read as an outage and produced a "Wieder online" ping on every blip. With
# the 20 s poll interval, 3 misses ≈ one minute of real downtime.
OFFLINE_THRESHOLD = 3

_conn_lock = threading.Lock()
_conn_online: bool | None = None
_conn_fails = 0
_offline_since: float | None = None  # monotonic time of the first failed probe
_last_outage: float | None = None  # seconds, set on the offline→online edge
# Finished outages as (ended_at wall clock, seconds) for the health monitor.
_outages: deque[tuple[float, float]] = deque(maxlen=200)


def _now() -> float:
    return time.monotonic()


def note_online(online: bool, now: float | None = None) -> str | None:
    """Fold an observed online state into the tracked state (pure transition).

    Returns ``"offline"`` once ``OFFLINE_THRESHOLD`` probes in a row failed,
    ``"reconnect"`` on the following offline→online edge (its duration is then
    available from :func:`last_outage_seconds`), otherwise ``None``. Failures
    below the threshold are absorbed silently. The first observation only sets
    the baseline — startup already reports the initial state, so it never
    fires; a box that boots offline is treated as down right away.
    """
    global _conn_online, _conn_fails, _offline_since, _last_outage
    now = _now() if now is None else now
    with _conn_lock:
        if _conn_online is None:
            _conn_online = online
            _conn_fails = 0 if online else OFFLINE_THRESHOLD
            _offline_since = None if online else now
            return None
        if online:
            was_down = not _conn_online
            if was_down and _offline_since is not None:
                _last_outage = now - _offline_since
                _outages.append((time.time(), _last_outage))
            _conn_online = True
            _conn_fails = 0
            _offline_since = None
            return "reconnect" if was_down else None
        _conn_fails += 1
        if _conn_fails == 1:
            _offline_since = now
        if _conn_online and _conn_fails >= OFFLINE_THRESHOLD:
            _conn_online = False
            return "offline"
        return None


def last_outage_seconds() -> float | None:
    """Duration of the outage that ended with the last ``"reconnect"``."""
    with _conn_lock:
        return _last_outage


def connectivity_state(window: float = 86400.0) -> dict:
    """Uplink state for the health monitor: current state, running outage, history.

    ``online`` is ``None`` until the monitor took its first probe.
    """
    wall = time.time()
    with _conn_lock:
        online = _conn_online
        offline_for = _now() - _offline_since if online is False and _offline_since else None
        recent = [(end, secs) for end, secs in _outages if wall - end <= window]
        last = _outages[-1] if _outages else None
    return {
        "online": online,
        "offline_for_seconds": round(offline_for) if offline_for is not None else None,
        "outages_24h": len(recent),
        "downtime_24h_seconds": round(sum(secs for _, secs in recent)),
        "last_outage": (
            {"ended_at": datetime.fromtimestamp(last[0]).isoformat(timespec="seconds"),
             "seconds": round(last[1])}
            if last
            else None
        ),
    }


def check_connectivity_once() -> None:
    """One connectivity poll; announce a reconnect on the offline→online edge."""
    from .db import SessionLocal

    online = netinfo.is_online()
    transition = note_online(online)
    if transition == "offline":
        logger.warning(
            "Uplink down — %d connectivity checks in a row failed", OFFLINE_THRESHOLD
        )
    elif transition == "reconnect":
        outage = last_outage_seconds()
        logger.info(
            "Uplink recovered after %s — sending reconnect announcement",
            format_duration(outage) if outage is not None else "unknown time",
        )
        from . import notify_queue

        notify_queue.kick()
        try:
            with SessionLocal() as db:
                announce_reconnect(db, outage)
        except Exception:  # noqa: BLE001
            logger.warning("Reconnect announcement failed", exc_info=True)
        # Held-back notifications (and the ping above, if it had to queue
        # behind them) go out now instead of on the next outbox round.
        try:
            notify_queue.flush(force=True)
        except Exception:  # noqa: BLE001
            logger.warning("Outbox flush after reconnect failed", exc_info=True)
    elif not online:
        logger.debug("Connectivity check failed (%d/%d)", _conn_fails, OFFLINE_THRESHOLD)


CONNECTIVITY_INTERVAL = 20.0  # seconds between uplink checks


async def connectivity_loop(stop: asyncio.Event, interval: float = CONNECTIVITY_INTERVAL) -> None:
    """Poll connectivity so a dropped-then-restored uplink emits a reconnect ping."""
    logger.info("Connectivity monitor started (every %.0fs)", interval)
    # Establish the baseline immediately so a subsequent drop→recover is caught.
    try:
        await asyncio.to_thread(lambda: note_online(netinfo.is_online()))
    except Exception:  # noqa: BLE001
        logger.debug("Initial connectivity baseline failed", exc_info=True)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            break  # stop signalled during the wait
        except TimeoutError:
            pass
        try:
            await asyncio.to_thread(check_connectivity_once)
        except Exception:  # noqa: BLE001 - keep the monitor alive
            logger.exception("Connectivity monitor error")
    logger.info("Connectivity monitor stopped")


# --- Bandwidth gate transitions -------------------------------------------

_bw_lock = threading.Lock()
_bw_paused = False


def note_bandwidth_gate(
    db: Session, *, gated: bool, reason: str, has_queued: bool, last_kbps: float
) -> None:
    """Detect and announce bandwidth-gate transitions.

    "Paused" fires when the gate first blocks *while work is waiting*; "resumed"
    fires when the gate later opens. Idempotent between edges, so the worker can
    call it on every poll.
    """
    global _bw_paused
    transition: str | None = None
    with _bw_lock:
        paused_now = gated and has_queued
        if paused_now and not _bw_paused:
            _bw_paused = True
            transition = "paused"
        elif _bw_paused and not gated:
            _bw_paused = False
            transition = "resumed"
    if transition == "paused":
        announce_bandwidth_paused(db, reason)
    elif transition == "resumed":
        announce_bandwidth_resumed(db, last_kbps)


def reset_state() -> None:
    """Reset the module's transition state (used by tests)."""
    global _conn_online, _conn_fails, _offline_since, _last_outage, _bw_paused
    with _conn_lock:
        _conn_online = None
        _conn_fails = 0
        _offline_since = None
        _last_outage = None
        _outages.clear()
    with _bw_lock:
        _bw_paused = False
