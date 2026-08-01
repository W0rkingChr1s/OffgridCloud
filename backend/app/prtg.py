"""PRTG-readable rendering of the health payload.

PRTG's *HTTP Data Advanced* sensor does not understand arbitrary JSON: it
expects a fixed envelope ``{"prtg": {"result": [...], "text": "..."}}`` where
every entry in ``result`` becomes one sensor channel, and thresholds travel with
the data (``limitmode`` + ``limit*``) so the sensor needs no manual setup in the
PRTG UI. This module maps the values ``/api/health`` already reports onto that
shape — see ``docs/BETRIEB.md`` §8.

Values are emitted as strings (accepted by every PRTG version) with ``.`` as the
decimal separator, which is what the sensor parses regardless of locale.
"""

from __future__ import annotations

from typing import Any

# Free-space thresholds for the "Speicher belegt" channel, in percent used.
DISK_WARN_PERCENT = 85.0
DISK_ERROR_PERCENT = 95.0


def _boolean_channel(name: str, ok: bool, error_message: str) -> dict[str, Any]:
    """A 0/1 channel that turns the sensor red when the value drops to 0."""
    return {
        "channel": name,
        "value": "1" if ok else "0",
        "unit": "Custom",
        "customunit": "",
        "limitmode": 1,
        "limitminerror": "1",
        "limiterrormsg": error_message,
    }


def health_to_prtg(health: dict[str, Any], disk: dict[str, Any] | None = None) -> dict[str, Any]:
    """Render a ``/api/health`` payload as an *HTTP Data Advanced* result set.

    ``disk`` is the ``admin_ops.disk_usage()`` mapping; it is optional so a
    failure to stat the buffer directory still yields a usable liveness sensor.
    """
    rclone = health.get("rclone") or {}
    rclone_ok = bool(rclone.get("available"))

    results: list[dict[str, Any]] = [
        _boolean_channel(
            "Status",
            health.get("status") == "ok",
            "OffgridCloud meldet keinen OK-Status.",
        ),
        _boolean_channel(
            "rclone verfügbar",
            rclone_ok,
            rclone.get("error") or "rclone ist nicht verfügbar — Uploads schlagen fehl.",
        ),
    ]

    if disk:
        results.append(
            {
                "channel": "Speicher belegt",
                "value": f"{float(disk.get('percent_used', 0.0)):.1f}",
                "unit": "Percent",
                "float": 1,
                "limitmode": 1,
                "limitmaxwarning": f"{DISK_WARN_PERCENT:.0f}",
                "limitmaxerror": f"{DISK_ERROR_PERCENT:.0f}",
                "limiterrormsg": "Puffer-Speicher fast voll — Uploads können stoppen.",
            }
        )
        results.append(
            {
                "channel": "Speicher frei",
                "value": str(int(disk.get("free", 0))),
                "unit": "BytesDisk",
            }
        )

    return {"prtg": {"result": results, "text": _summary(health, rclone, disk)}}


def _summary(health: dict[str, Any], rclone: dict[str, Any], disk: dict[str, Any] | None) -> str:
    """Short sensor message shown next to the channels in PRTG."""
    parts = [f"{health.get('app', 'OffgridCloud')} {health.get('version', '?')}"]
    version = rclone.get("version")
    parts.append(f"rclone: {version}" if version else "rclone: nicht verfügbar")
    if disk:
        parts.append(f"Speicher: {float(disk.get('percent_used', 0.0)):.1f}% belegt")
    return " | ".join(parts)
