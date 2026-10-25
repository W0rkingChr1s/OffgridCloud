"""Host metrics for the health monitor: CPU, memory, temperature, power, Wi-Fi.

Read straight from ``/proc`` and ``/sys`` — no psutil, no privileges. Every
reader is best-effort and returns ``None`` (or an empty value) where the source
is missing, so the same code runs on a Pi, an x86 box and inside Docker. The
parsers are pure functions over the file contents so tests need no real host.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

_PROC = Path("/proc")
_SYS = Path("/sys")

# Bits of the Raspberry Pi firmware's get_throttled mask. The low half is the
# state *now*, the high half is sticky "has happened since boot".
_THROTTLE_BITS = {
    0: "under_voltage",
    1: "freq_capped",
    2: "throttled",
    3: "soft_temp_limit",
}
_THROTTLE_STICKY_SHIFT = 16


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# --- CPU ------------------------------------------------------------------


def parse_cpu_times(stat: str) -> tuple[int, int] | None:
    """``(busy, total)`` jiffies from the aggregate ``cpu`` line of /proc/stat."""
    for line in stat.splitlines():
        if line.startswith("cpu "):
            fields = [int(v) for v in line.split()[1:]]
            idle = fields[3] + (fields[4] if len(fields) > 4 else 0)  # idle + iowait
            total = sum(fields[:8])  # guest time is already counted in user/nice
            return total - idle, total
    return None


class CpuSampler:
    """CPU utilisation between two calls (the first call has no baseline)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: tuple[int, int] | None = None

    def percent(self, stat: str | None = None) -> float | None:
        stat = stat if stat is not None else _read(_PROC / "stat")
        sample = parse_cpu_times(stat) if stat else None
        if sample is None:
            return None
        with self._lock:
            prev, self._last = self._last, sample
        if prev is None:
            return None
        busy, total = sample[0] - prev[0], sample[1] - prev[1]
        if total <= 0:
            return None
        return round(max(0.0, min(100.0, busy / total * 100)), 1)

    def reset(self) -> None:
        with self._lock:
            self._last = None


def load_average() -> tuple[float, float, float] | None:
    try:
        return os.getloadavg()
    except OSError:
        return None


# --- Memory ---------------------------------------------------------------


def parse_meminfo(text: str) -> dict[str, int]:
    """/proc/meminfo as ``{key: bytes}``."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if not parts:
            continue
        try:
            value = int(parts[0])
        except ValueError:
            continue
        out[key.strip()] = value * 1024 if len(parts) > 1 and parts[1] == "kB" else value
    return out


def memory(text: str | None = None) -> dict | None:
    text = text if text is not None else _read(_PROC / "meminfo")
    if not text:
        return None
    info = parse_meminfo(text)
    total = info.get("MemTotal", 0)
    if not total:
        return None
    available = info.get("MemAvailable", info.get("MemFree", 0))
    used = max(0, total - available)
    swap_total = info.get("SwapTotal", 0)
    swap_used = max(0, swap_total - info.get("SwapFree", 0))
    return {
        "total": total,
        "used": used,
        "available": available,
        "percent_used": round(used / total * 100, 1),
        "swap_total": swap_total,
        "swap_used": swap_used,
    }


# --- Temperature ----------------------------------------------------------


def cpu_temperature(root: Path = _SYS) -> float | None:
    """CPU/SoC temperature in °C, from the first plausible thermal zone."""
    zones = sorted((root / "class" / "thermal").glob("thermal_zone*"))
    preferred = [z for z in zones if "cpu" in (_read(z / "type") or "").lower()]
    for zone in preferred + [z for z in zones if z not in preferred]:
        raw = _read(zone / "temp")
        try:
            milli = int((raw or "").strip())
        except ValueError:
            continue
        celsius = milli / 1000
        if -40 < celsius < 150:  # skip disconnected sensors reporting junk
            return round(celsius, 1)
    return None


# --- Uptime ---------------------------------------------------------------


def uptime_seconds(text: str | None = None) -> float | None:
    text = text if text is not None else _read(_PROC / "uptime")
    try:
        return float((text or "").split()[0])
    except (IndexError, ValueError):
        return None


# --- Raspberry Pi power / throttling --------------------------------------


def parse_throttled(value: int) -> dict:
    """Decode the firmware's get_throttled bitmask."""
    now = {name: bool(value & (1 << bit)) for bit, name in _THROTTLE_BITS.items()}
    since_boot = {
        name: bool(value & (1 << (bit + _THROTTLE_STICKY_SHIFT)))
        for bit, name in _THROTTLE_BITS.items()
    }
    return {"raw": hex(value), "now": now, "since_boot": since_boot}


def _throttled_raw(root: Path = _SYS) -> int | None:
    # Newer Pi kernels expose it read-only in sysfs; vcgencmd needs /dev/vchiq.
    sysfs = _read(root / "devices" / "platform" / "soc" / "soc:firmware" / "get_throttled")
    if sysfs and sysfs.strip():
        try:
            return int(sysfs.strip(), 16)
        except ValueError:
            pass
    if root != _SYS or not shutil.which("vcgencmd"):
        return None
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=2, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    _, _, hexval = out.strip().partition("=")
    try:
        return int(hexval, 16)
    except ValueError:
        return None


def throttling(root: Path = _SYS) -> dict | None:
    """Under-voltage / throttling state, or ``None`` when not on a Raspberry Pi."""
    raw = _throttled_raw(root)
    return parse_throttled(raw) if raw is not None else None


# --- Wi-Fi ----------------------------------------------------------------


def parse_wireless(text: str) -> list[dict]:
    """Interfaces from /proc/net/wireless with link quality (0–100) and dBm."""
    out = []
    for line in text.splitlines()[2:]:
        name, _, rest = line.partition(":")
        fields = rest.split()
        if len(fields) < 3:
            continue
        try:
            link = float(fields[1].rstrip("."))
            level = float(fields[2].rstrip("."))
        except ValueError:
            continue
        if level > 0:  # some drivers report unsigned 8-bit dBm
            level -= 256
        out.append(
            {
                "interface": name.strip(),
                # Most drivers scale link quality to 70.
                "quality_percent": round(max(0.0, min(100.0, link / 70 * 100))),
                "signal_dbm": int(level),
            }
        )
    return out


def wireless(text: str | None = None) -> list[dict]:
    text = text if text is not None else _read(_PROC / "net" / "wireless")
    return parse_wireless(text) if text else []
