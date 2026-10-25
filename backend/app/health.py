"""Health monitor: one machine-readable snapshot of the box, plus a 24 h history.

:func:`collect` gathers system (CPU, memory, temperature, power), storage,
network (uplink, latency, Wi-Fi, VPN), transfer and provider state, and rates
each area against fixed thresholds into ``checks`` with an overall status
(``ok`` / ``warning`` / ``error``). The same snapshot feeds the *Zustand* page,
the companion app (``/api/monitor``), Prometheus (:func:`to_prometheus`) and
PRTG (:func:`to_prtg`).

:func:`sample_loop` records one point per minute into an in-memory ring
(24 h). It lives in RAM on purpose: a write per minute would wear the SD card,
and long-term storage is what Prometheus/PRTG are for.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import __version__, announce, netinfo, sysmetrics
from .admin_ops import disk_usage
from .bandwidth import get_policy
from .models import CloudProvider, ProviderStatus, TransferJob, TransferStatus
from .prtg import DISK_ERROR_PERCENT, DISK_WARN_PERCENT
from .rclone import check_rclone

logger = logging.getLogger("offgridcloud.health")

OK, WARNING, ERROR = "ok", "warning", "error"
_RANK = {OK: 0, WARNING: 1, ERROR: 2}

# Thresholds (warning, error). The Pi firmware throttles from 80 °C (soft limit
# on the 3B+) and hard at 85 °C, so 70 °C is the "watch it" mark.
TEMP_C = (70.0, 80.0)
MEMORY_PERCENT = (90.0, 97.0)
DISK_PERCENT = (DISK_WARN_PERCENT, DISK_ERROR_PERCENT)
WIFI_DBM = (-75, -85)  # weaker than this
LATENCY_MS = (300.0, 1000.0)

HISTORY_INTERVAL = 60.0  # seconds between history points
HISTORY_POINTS = 24 * 60

_live_cpu = sysmetrics.CpuSampler()
_history_cpu = sysmetrics.CpuSampler()


# --- Cached slow probes ---------------------------------------------------


class _Cached:
    """A value refreshed at most every ``ttl`` seconds (probes that cost time)."""

    def __init__(self, ttl: float) -> None:
        self.ttl = ttl
        self._lock = threading.Lock()
        self._value: Any = None
        self._at = 0.0

    def get(self, fetch, now: float | None = None):
        now = time.monotonic() if now is None else now
        with self._lock:
            if self._at and now - self._at < self.ttl:
                return self._value
        value = fetch()
        with self._lock:
            self._value, self._at = value, now
        return value

    def peek(self):
        with self._lock:
            return self._value

    def set(self, value) -> None:
        with self._lock:
            self._value, self._at = value, time.monotonic()

    def clear(self) -> None:
        with self._lock:
            self._value, self._at = None, 0.0


_external_ip = _Cached(600)
_latency = _Cached(HISTORY_INTERVAL)


def measure_latency_ms(timeout: float = 3.0) -> float | None:
    """TCP connect time to the first reachable online target, in ms."""
    for host, port in netinfo._ONLINE_TARGETS:
        start = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return round((time.perf_counter() - start) * 1000, 1)
        except OSError:
            continue
    return None


# --- Rating ---------------------------------------------------------------


def _rate_high(value: float | None, limits: tuple[float, float]) -> str:
    if value is None:
        return OK
    warn, err = limits
    return ERROR if value >= err else WARNING if value >= warn else OK


def _check(check_id: str, label: str, status: str, message: str) -> dict:
    return {"id": check_id, "label": label, "status": status, "message": message}


def build_checks(snap: dict) -> list[dict]:
    """Rate a snapshot (pure). Only areas with data produce a check."""
    checks: list[dict] = []
    system, storage, network = snap["system"], snap["storage"], snap["network"]

    temp = system.get("temperature_c")
    if temp is not None:
        checks.append(
            _check("temperature", "Temperatur", _rate_high(temp, TEMP_C), f"CPU {temp:.0f} °C")
        )

    mem = system.get("memory")
    if mem:
        pct = mem["percent_used"]
        checks.append(
            _check(
                "memory", "Arbeitsspeicher", _rate_high(pct, MEMORY_PERCENT), f"{pct:.0f} % belegt"
            )
        )

    power = system.get("throttling")
    if power:
        now, past = power["now"], power["since_boot"]
        if now["under_voltage"]:
            status, msg = ERROR, "Unterspannung — Netzteil oder Kabel zu schwach"
        elif now["throttled"] or now["freq_capped"] or now["soft_temp_limit"]:
            status, msg = WARNING, "CPU wird gerade gedrosselt"
        elif past["under_voltage"]:
            status, msg = WARNING, "Seit dem Start gab es Unterspannung"
        elif any(past.values()):
            status, msg = WARNING, "Seit dem Start wurde gedrosselt"
        else:
            status, msg = OK, "Stromversorgung stabil"
        checks.append(_check("power", "Stromversorgung", status, msg))

    disk = storage.get("buffer")
    if disk:
        pct = disk["percent_used"]
        checks.append(
            _check("storage", "Speicher", _rate_high(pct, DISK_PERCENT), f"{pct:.0f} % belegt")
        )

    online = network.get("online")
    if online is not None:
        if not online:
            secs = network.get("offline_for_seconds")
            since = f" seit {announce.format_duration(secs)}" if secs else ""
            checks.append(_check("internet", "Internet", ERROR, f"Keine Verbindung{since}"))
        else:
            latency = network.get("latency_ms")
            status = _rate_high(latency, LATENCY_MS)
            outages = network.get("outages_24h", 0)
            if status == OK and outages >= 3:
                status = WARNING
            parts = [f"{latency:.0f} ms" if latency is not None else "verbunden"]
            if outages:
                parts.append(f"{outages} Ausfall/Ausfälle in 24 h")
            checks.append(_check("internet", "Internet", status, ", ".join(parts)))

    for wifi in network.get("wifi") or []:
        dbm = wifi["signal_dbm"]
        warn, err = WIFI_DBM
        status = ERROR if dbm <= err else WARNING if dbm <= warn else OK
        checks.append(
            _check(f"wifi_{wifi['interface']}", f"WLAN {wifi['interface']}", status, f"{dbm} dBm")
        )

    rclone = snap.get("rclone") or {}
    checks.append(
        _check(
            "rclone",
            "rclone",
            OK if rclone.get("available") else ERROR,
            rclone.get("version") or rclone.get("error") or "nicht verfügbar",
        )
    )

    prov = snap.get("providers") or {}
    if prov.get("total"):
        status = WARNING if prov.get("error") else OK
        checks.append(
            _check("providers", "Cloud-Ziele", status, f"{prov['ok']}/{prov['total']} verbunden")
        )

    transfers = snap.get("transfers") or {}
    if transfers.get("failed"):
        checks.append(
            _check(
                "transfers",
                "Übertragungen",
                WARNING,
                f"{transfers['failed']} fehlgeschlagen",
            )
        )
    else:
        checks.append(
            _check("transfers", "Übertragungen", OK, f"{transfers.get('queued', 0)} wartend")
        )
    return checks


def overall(checks: list[dict]) -> str:
    worst = OK
    for check in checks:
        if _RANK[check["status"]] > _RANK[worst]:
            worst = check["status"]
    return worst


# --- Collection -----------------------------------------------------------


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - one failing probe must not break the snapshot
        logger.debug("Health probe %s failed", getattr(fn, "__name__", fn), exc_info=True)
        return default


def _vpn_connected() -> bool | None:
    try:
        from . import vpn as vpnsvc

        return vpnsvc.active_id() is not None
    except Exception:  # noqa: BLE001
        return None


def _transfers(db: Session) -> dict:
    rows = db.execute(
        select(TransferJob.status, func.count(TransferJob.id)).group_by(TransferJob.status)
    ).all()
    counts = {status.value: n for status, n in rows}
    return {
        "queued": counts.get(TransferStatus.QUEUED.value, 0),
        "running": counts.get(TransferStatus.RUNNING.value, 0),
        "failed": counts.get(TransferStatus.FAILED.value, 0),
    }


def _providers(db: Session) -> dict:
    statuses = list(db.scalars(select(CloudProvider.status)))
    return {
        "total": len(statuses),
        "ok": sum(1 for s in statuses if s == ProviderStatus.OK),
        "error": sum(1 for s in statuses if s == ProviderStatus.ERROR),
    }


def collect(db: Session, *, probe_external_ip: bool = True) -> dict:
    """The full health snapshot (see module docstring)."""
    load = sysmetrics.load_average()
    cpu = _live_cpu.percent()
    if cpu is None:  # first call has no baseline: take a short one
        time.sleep(0.25)
        cpu = _live_cpu.percent()
    rclone = _safe(check_rclone)
    conn = announce.connectivity_state()
    latency = _latency.peek()
    if latency is None and conn["online"]:
        latency = _latency.get(measure_latency_ms)
    policy = _safe(lambda: get_policy(db))
    snap: dict[str, Any] = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "version": __version__,
        "hostname": socket.gethostname(),
        "system": {
            "cpu_percent": cpu,
            "cpu_count": os.cpu_count(),
            "load_average": list(load) if load else None,
            "memory": _safe(sysmetrics.memory),
            "temperature_c": _safe(sysmetrics.cpu_temperature),
            "uptime_seconds": _safe(sysmetrics.uptime_seconds),
            "throttling": _safe(sysmetrics.throttling),
        },
        "storage": {"buffer": _safe(disk_usage)},
        "network": {
            **conn,
            "latency_ms": latency,
            "bandwidth_kbps": round(policy.last_kbps, 1) if policy else None,
            "internal_ip": _safe(netinfo.internal_ip),
            "external_ip": (
                _external_ip.get(netinfo.external_ip)
                if probe_external_ip and conn["online"] is not False
                else _external_ip.peek()
            ),
            "wifi": _safe(sysmetrics.wireless, []),
            "vpn_connected": _vpn_connected(),
        },
        "transfers": _safe(lambda: _transfers(db), {}),
        "providers": _safe(lambda: _providers(db), {}),
        "rclone": (
            {"available": rclone.available, "version": rclone.version, "error": rclone.error}
            if rclone
            else {"available": False, "version": None, "error": "Prüfung fehlgeschlagen"}
        ),
    }
    checks = build_checks(snap)
    snap["checks"] = checks
    snap["status"] = overall(checks)
    return snap


# --- History --------------------------------------------------------------

_history_lock = threading.Lock()
_history: deque[dict] = deque(maxlen=HISTORY_POINTS)


def record_sample(db: Session) -> dict:
    """Take one history point (also refreshes the cached latency)."""
    conn = announce.connectivity_state()
    latency = measure_latency_ms() if conn["online"] is not False else None
    _latency.set(latency)
    mem = _safe(sysmetrics.memory)
    disk = _safe(disk_usage)
    policy = _safe(lambda: get_policy(db))
    point = {
        "t": int(time.time()),
        "cpu_percent": _history_cpu.percent(),
        "memory_percent": mem["percent_used"] if mem else None,
        "temperature_c": _safe(sysmetrics.cpu_temperature),
        "disk_percent": disk["percent_used"] if disk else None,
        "latency_ms": latency,
        "online": None if conn["online"] is None else int(bool(conn["online"])),
        "bandwidth_kbps": round(policy.last_kbps, 1) if policy else None,
    }
    with _history_lock:
        _history.append(point)
    return point


def history() -> list[dict]:
    with _history_lock:
        return list(_history)


def _sample_once() -> None:
    from .db import SessionLocal

    with SessionLocal() as db:
        record_sample(db)


async def sample_loop(stop: asyncio.Event, interval: float = HISTORY_INTERVAL) -> None:
    """Record a history point every ``interval`` seconds."""
    logger.info("Health sampler started (every %.0fs)", interval)
    _history_cpu.percent()  # baseline, so the first point already has a CPU value
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            break
        except TimeoutError:
            pass
        try:
            await asyncio.to_thread(_sample_once)
        except Exception:  # noqa: BLE001 - keep sampling
            logger.exception("Health sample failed")
    logger.info("Health sampler stopped")


def reset_state() -> None:
    """Forget history and cached probes (tests)."""
    with _history_lock:
        _history.clear()
    _latency.clear()
    _external_ip.clear()
    _live_cpu.reset()
    _history_cpu.reset()


# --- Prometheus -----------------------------------------------------------


def _label_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class _Prom:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self._seen: set[str] = set()

    def add(
        self,
        name: str,
        help_text: str,
        value: float | int | bool | None,
        labels: dict[str, str] | None = None,
        kind: str = "gauge",
    ) -> None:
        if value is None:
            return
        full = f"offgridcloud_{name}"
        if full not in self._seen:
            self._seen.add(full)
            self.lines.append(f"# HELP {full} {help_text}")
            self.lines.append(f"# TYPE {full} {kind}")
        label_str = ""
        if labels:
            label_str = "{" + ",".join(f'{k}="{_label_value(v)}"' for k, v in labels.items()) + "}"
        self.lines.append(f"{full}{label_str} {float(value):g}")

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def to_prometheus(snap: dict) -> str:
    """Render a snapshot in the Prometheus text exposition format (0.0.4)."""
    p = _Prom()
    system, storage, network = snap["system"], snap["storage"], snap["network"]
    p.add(
        "info", "Build information.", 1, {"version": snap["version"], "hostname": snap["hostname"]}
    )
    p.add("health_status", "Overall health: 0 = ok, 1 = warning, 2 = error.", _RANK[snap["status"]])
    for check in snap["checks"]:
        p.add(
            "check_status",
            "Per-check health: 0 = ok, 1 = warning, 2 = error.",
            _RANK[check["status"]],
            {"check": check["id"]},
        )

    p.add("cpu_usage_percent", "CPU utilisation since the previous scrape.", system["cpu_percent"])
    p.add("cpu_count", "Number of CPUs.", system["cpu_count"])
    for period, value in zip(("1m", "5m", "15m"), system["load_average"] or [], strict=False):
        p.add("load_average", "System load average.", value, {"period": period})
    mem = system["memory"]
    if mem:
        p.add("memory_total_bytes", "Total memory.", mem["total"])
        p.add("memory_available_bytes", "Memory available for new work.", mem["available"])
        p.add("memory_used_percent", "Memory in use.", mem["percent_used"])
        p.add("swap_used_bytes", "Swap in use.", mem["swap_used"])
    p.add("cpu_temperature_celsius", "CPU/SoC temperature.", system["temperature_c"])
    p.add("uptime_seconds", "Host uptime.", system["uptime_seconds"], kind="counter")
    power = system["throttling"]
    if power:
        for scope in ("now", "since_boot"):
            for flag, active in power[scope].items():
                p.add(
                    "throttled",
                    "Raspberry Pi power/throttling flags (1 = active).",
                    int(active),
                    {"flag": flag, "scope": scope},
                )

    disk = storage["buffer"]
    if disk:
        p.add("disk_total_bytes", "Media buffer size.", disk["total"])
        p.add("disk_free_bytes", "Free space in the media buffer.", disk["free"])
        p.add("disk_used_percent", "Media buffer usage.", disk["percent_used"])

    if network["online"] is not None:
        p.add("internet_up", "Uplink reachable (1) or not (0).", int(bool(network["online"])))
    if network["latency_ms"] is not None:
        p.add(
            "internet_latency_seconds",
            "TCP connect time to the probe target.",
            network["latency_ms"] / 1000,
        )
    p.add("internet_outages_24h", "Outages (>= ~1 min) in the last 24 h.", network["outages_24h"])
    p.add(
        "internet_downtime_24h_seconds",
        "Summed outage time in the last 24 h.",
        network["downtime_24h_seconds"],
    )
    if network["bandwidth_kbps"] is not None:
        p.add(
            "bandwidth_bytes_per_second",
            "Last measured upload bandwidth.",
            network["bandwidth_kbps"] * 1024,
        )
    for wifi in network["wifi"] or []:
        labels = {"interface": wifi["interface"]}
        p.add("wifi_signal_dbm", "Wi-Fi signal level.", wifi["signal_dbm"], labels)
        p.add("wifi_link_quality_percent", "Wi-Fi link quality.", wifi["quality_percent"], labels)
    if network["vpn_connected"] is not None:
        p.add("vpn_connected", "VPN tunnel up (1) or not (0).", int(network["vpn_connected"]))

    for state, n in (snap["transfers"] or {}).items():
        p.add("transfers", "Transfer jobs by state.", n, {"state": state})
    prov = snap["providers"] or {}
    if prov:
        p.add("providers", "Configured cloud targets.", prov["total"], {"state": "total"})
        p.add("providers", "Configured cloud targets.", prov["ok"], {"state": "ok"})
        p.add("providers", "Configured cloud targets.", prov["error"], {"state": "error"})
    p.add(
        "rclone_available",
        "rclone binary found (1) or not (0).",
        int(bool(snap["rclone"]["available"])),
    )
    return p.text()


# --- PRTG -----------------------------------------------------------------


def to_prtg(snap: dict) -> dict:
    """The extended PRTG *HTTP Data Advanced* result set (thresholds included)."""
    from .prtg import health_to_prtg

    base = health_to_prtg(
        {
            "status": "ok",
            "app": "OffgridCloud",
            "version": snap["version"],
            "rclone": snap["rclone"],
        },
        snap["storage"]["buffer"],
    )
    results = base["prtg"]["result"]
    system, network = snap["system"], snap["network"]

    def channel(name: str, value, unit: str, **extra) -> None:
        if value is None:
            return
        entry = {"channel": name, "value": f"{float(value):.1f}", "unit": unit, "float": 1}
        entry.update(extra)
        results.append(entry)

    results.insert(
        0,
        {
            "channel": "Gesamtzustand",
            "value": str(_RANK[snap["status"]]),
            "unit": "Custom",
            "customunit": "",
            "limitmode": 1,
            "limitmaxwarning": "0",
            "limitmaxerror": "1",
            "limitwarningmsg": "Mindestens eine Prüfung meldet eine Warnung.",
            "limiterrormsg": "Mindestens eine Prüfung meldet einen Fehler.",
        },
    )
    channel("CPU-Last", system["cpu_percent"], "Percent")
    mem = system["memory"]
    channel(
        "Arbeitsspeicher belegt",
        mem["percent_used"] if mem else None,
        "Percent",
        limitmode=1,
        limitmaxwarning=f"{MEMORY_PERCENT[0]:.0f}",
        limitmaxerror=f"{MEMORY_PERCENT[1]:.0f}",
    )
    channel(
        "CPU-Temperatur",
        system["temperature_c"],
        "Temperature",
        limitmode=1,
        limitmaxwarning=f"{TEMP_C[0]:.0f}",
        limitmaxerror=f"{TEMP_C[1]:.0f}",
    )
    power = system["throttling"]
    if power:
        results.append(
            {
                "channel": "Unterspannung",
                "value": "1" if power["now"]["under_voltage"] else "0",
                "unit": "Custom",
                "customunit": "",
                "limitmode": 1,
                "limitmaxerror": "0",
                "limiterrormsg": "Unterspannung — Netzteil oder Kabel prüfen.",
            }
        )
    if network["online"] is not None:
        results.append(
            {
                "channel": "Internet",
                "value": "1" if network["online"] else "0",
                "unit": "Custom",
                "customunit": "",
                "limitmode": 1,
                "limitminerror": "1",
                "limiterrormsg": "Keine Internetverbindung.",
            }
        )
    channel(
        "Latenz",
        network["latency_ms"],
        "TimeResponse",
        limitmode=1,
        limitmaxwarning=f"{LATENCY_MS[0]:.0f}",
        limitmaxerror=f"{LATENCY_MS[1]:.0f}",
    )
    results.append(
        {"channel": "Ausfälle (24 h)", "value": str(network["outages_24h"]), "unit": "Count"}
    )
    for wifi in network["wifi"] or []:
        channel(
            f"WLAN-Signal {wifi['interface']}",
            wifi["signal_dbm"],
            "Custom",
            customunit="dBm",
            limitmode=1,
            limitminwarning=str(WIFI_DBM[0]),
            limitminerror=str(WIFI_DBM[1]),
        )
    transfers = snap["transfers"] or {}
    results.append(
        {"channel": "Warteschlange", "value": str(transfers.get("queued", 0)), "unit": "Count"}
    )
    results.append(
        {
            "channel": "Fehlgeschlagen",
            "value": str(transfers.get("failed", 0)),
            "unit": "Count",
            "limitmode": 1,
            "limitmaxwarning": "0",
        }
    )
    bad = [c["label"] for c in snap["checks"] if c["status"] != OK]
    text = base["prtg"]["text"]
    if bad:
        text += " | Auffällig: " + ", ".join(bad)
    base["prtg"]["text"] = text
    return base
