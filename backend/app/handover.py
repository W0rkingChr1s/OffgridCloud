"""Upload handover for the companion app (Companion PLAN.md §3.6).

Photos never travel over Bluetooth — far too slow. Instead the app asks over
the authenticated BLE channel for a Wi-Fi link to the box and uploads over it
with the regular chunk protocol (``routers/uploads.py``):

1. ``POST /api/uplink/handover`` via BLE → the box picks a way (:func:`plan`),
   raises its upload Wi-Fi if needed and returns SSID, key, address, the root
   CA to pin and a short-lived **upload-only** token.
2. The app joins the Wi-Fi (one OS dialog) and uploads.
3. ``DELETE`` via BLE, or idle/maximum time on the box → the Wi-Fi goes down.

Ways, first one that fits:

* the **fallback AP** is up anyway → hand over its credentials, nothing to start;
* a **spare Wi-Fi adapter** (USB stick, or ``wlan0`` while the box is on cable
  or a tethered phone and not on Wi-Fi) → upload Wi-Fi there;
* every adapter is **on a Wi-Fi uplink** → the upload Wi-Fi runs on a second,
  virtual interface of that adapter, next to the client and on its channel
  (the Pi's Wi-Fi chip can do one AP beside one client). The box stays online.
  If the adapter can't, the handover is refused — it never takes the box
  offline.

The upload Wi-Fi is hostapd + dnsmasq run by a root helper
(``deploy/netfallback/handover.sh``), not NetworkManager: only hostapd can give
every paired phone its own key on one stable SSID. One session at a time; the
state lives in memory (one process), and a leftover Wi-Fi from a crashed
backend is torn down on the next start.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import shlex
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from . import https_config, network
from .admin_ops import audit
from .config import get_settings
from .crypto import decrypt, encrypt
from .deps import UPLOAD_SCOPE
from .models import BleDevice, User
from .pool import node_name
from .security import create_access_token

logger = logging.getLogger(__name__)

# Upload Wi-Fi goes down this long after the last sign of life from the app
# (an upload request or a status poll over BLE) …
IDLE_TIMEOUT = 300
# … and after this long in any case, so a forgotten session never keeps the
# box offline for hours.
MAX_DURATION = 3600
CHECK_INTERVAL = 15.0
# Its own subnet, apart from the fallback AP's 10.42.0.1/24.
AP_ADDRESS = "10.43.0.1/24"
AP_CHANNEL = 6
# Virtual AP interface next to a Wi-Fi client (handover.sh creates it).
VIRTUAL_IFACE = "ogcup0"
CONFIG_FILENAME = "handover.json"
# Below the BLE bridge's timeout for this endpoint (ble/bridge.py).
HELPER_TIMEOUT = 45
# 2.4 GHz is what every Pi can do; realistic upload rate for the app's estimate.
# Next to a client the AP takes the client's channel, the helper reports it.
BAND = "2.4GHz"
EST_MBIT = 20


class HandoverError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class Plan:
    possible: bool
    mode: str = ""  # "fallback" | "ap"
    interface: str | None = None
    # Adapter whose Wi-Fi client the virtual AP ``interface`` runs beside.
    parent: str | None = None
    reason: str = ""


def plan(status: network.NetworkStatus, wifi: list[str], *, helper: bool) -> Plan:
    """Pick the way for a new handover (pure; see the module docstring)."""
    if status.ap_active:
        return Plan(True, "fallback")
    if not status.supported:
        return Plan(False, reason=status.detail or "NetworkManager fehlt auf der Box.")
    if not helper:
        return Plan(
            False,
            reason=(
                "Das Upload-WLAN ist auf der Box nicht eingerichtet "
                "(sudo deploy/netfallback/install.sh)."
            ),
        )
    wifi = [dev for dev in wifi if dev != VIRTUAL_IFACE]
    if not wifi:
        return Plan(False, reason="Die Box hat keinen WLAN-Adapter.")
    clients = {link.device for link in status.links if link.kind == "client"}
    spare = [dev for dev in wifi if dev not in clients]
    if spare:
        return Plan(True, "ap", spare[0])
    # Never take a Wi-Fi uplink away: run the AP beside it instead.
    return Plan(True, "ap", VIRTUAL_IFACE, parent=wifi[0])


def upload_ssid(name: str) -> str:
    """Stable SSID of this box's upload Wi-Fi, at most 32 bytes."""
    prefix, suffix = "OGC-", "-up"
    room = 32 - len(prefix) - len(suffix)
    short = name.encode("utf-8")[:room].decode("utf-8", "ignore") or "box"
    return f"{prefix}{short}{suffix}"


@dataclass
class _Session:
    device_id: int
    user_id: int
    jti: str
    mode: str
    interface: str | None
    ssid: str
    passphrase: str
    address: str  # IPv4 of the box in that Wi-Fi
    band: str
    started: float  # time.monotonic()
    last_activity: float


_lock = threading.Lock()
# Held for a whole start, so two phones can't both raise the Wi-Fi.
_start_lock = threading.Lock()
_active: _Session | None = None


def _helper_command() -> str:
    return get_settings().net_handover_command.strip()


def _config_file() -> Path:
    return get_settings().data_dir / CONFIG_FILENAME


def _run_helper(*args: str) -> tuple[bool, str]:
    if not _helper_command():
        return False, "OGC_NET_HANDOVER_COMMAND not set"
    try:
        proc = subprocess.run(  # noqa: S603 (admin-configured command)
            [*shlex.split(_helper_command()), *args],
            capture_output=True,
            text=True,
            timeout=HELPER_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()[-2000:]


def _device_passphrase(db: Session, device: BleDevice) -> str:
    if not device.wifi_psk_encrypted:
        device.wifi_psk_encrypted = encrypt(secrets.token_urlsafe(18))
        db.commit()
    return decrypt(device.wifi_psk_encrypted)


def _tls(db: Session) -> dict:
    """How the app reaches the API inside the Wi-Fi, and which CA to pin.

    Caddy's leaf certificate rotates every ~12 h, so the app pins the box's
    root CA, which it gets here over the authenticated BLE channel — no
    trust-on-first-use. Without HTTPS set up it is plain HTTP on the backend
    port, still behind the phone's own WPA2 key.
    """
    data_dir = get_settings().data_dir
    der = https_config.read_root_ca(data_dir) if https_config.is_active(data_dir) else None
    if der is None:
        port = get_settings().mdns_port
        return {"scheme": "http", "port": port, "ca_sha256": None, "ca_der": None}
    return {
        "scheme": "https",
        "port": 443,
        "ca_sha256": https_config.ca_fingerprint(der).replace(":", "").lower(),
        "ca_der": base64.b64encode(der).decode("ascii"),
    }


def _current_plan() -> tuple[Plan, network.NetworkStatus]:
    status = network.get_status()
    return plan(status, network.wifi_interfaces(), helper=bool(_helper_command())), status


def _connection(db: Session, user: User, session: _Session) -> dict:
    ttl = MAX_DURATION - int(time.monotonic() - session.started)
    token = create_access_token(
        user_id=user.id,
        role=user.role.value,
        ttl=timedelta(seconds=max(ttl, 1)),
        claims={"scope": UPLOAD_SCOPE, "dev": session.device_id, "jti": session.jti},
    )
    return {
        "mode": session.mode,
        "ssid": session.ssid,
        "passphrase": session.passphrase,
        "address": session.address,
        **_tls(db),
        "upload_token": token,
        "expires_in": max(ttl, 0),
        "idle_timeout": IDLE_TIMEOUT,
        # Kept for older apps: the box never goes offline for a handover.
        "uplink_pause": False,
        "band": session.band,
        "est_mbit": EST_MBIT,
    }


def status(db: Session, user: User, device_id: int) -> dict:
    """What ``GET`` answers: can this phone start a handover, is one running?

    Polling it over BLE counts as activity of the phone's own session, and
    returns that session's connection data again (app restarted mid-upload).
    """
    with _lock:
        session = _active
        if session is not None and session.device_id == device_id:
            session.last_activity = time.monotonic()
    if (
        session is not None
        and session.mode == "fallback"
        and session.device_id == device_id
        and not network.get_status().ap_active
    ):
        # The watchdog took the fallback AP down (uplink is back): nothing
        # left to hand over, look for a new way below.
        stop(db, "Rückfall-WLAN beendet", device_id=device_id)
        session = None
    if session is not None:
        mine = session.device_id == device_id
        return {
            "active": True,
            "mine": mine,
            "possible": mine,
            "uplink_pause": False,
            "allowed": True,
            "reason": "" if mine else _BUSY,
            "connection": _connection(db, user, session) if mine else None,
        }
    chosen, net = _current_plan()
    return {
        "active": False,
        "mine": False,
        "possible": chosen.possible,
        "uplink_pause": False,
        "allowed": True,
        "reason": chosen.reason,
        # Same network already? The app can try these first (no handover).
        "lan_addresses": [link.ip for link in net.links if link.ip],
        "connection": None,
    }


_BUSY = "Das Upload-WLAN der Box wird gerade von einem anderen Gerät benutzt."


def start(db: Session, user: User, device_id: int) -> dict:
    """Start (or resume) this phone's handover and return the connection data."""
    with _start_lock:
        return _start(db, user, device_id)


def _start(db: Session, user: User, device_id: int) -> dict:
    global _active
    device = db.get(BleDevice, device_id)
    if device is None or device.user_id != user.id:
        raise HandoverError(403, "Gerät ist nicht (mehr) gekoppelt.")
    with _lock:
        session = _active
        if session is not None:
            if session.device_id != device_id:
                raise HandoverError(409, _BUSY)
            session.last_activity = time.monotonic()
    if session is not None:
        return _connection(db, user, session)

    chosen, _ = _current_plan()
    if not chosen.possible:
        raise HandoverError(409, chosen.reason)

    band = BAND
    if chosen.mode == "fallback":
        ap = network.get_network_settings(db)
        ssid = ap.ap_ssid
        passphrase = decrypt(ap.ap_psk_encrypted) if ap.ap_psk_encrypted else ""
        address = ap.ap_address.split("/")[0]
    else:
        ssid = upload_ssid(node_name())
        passphrase = _device_passphrase(db, device)
        address = AP_ADDRESS.split("/")[0]
        _write_config(
            chosen.interface or "",
            chosen.parent or "",
            ssid,
            passphrase,
            network.get_network_settings(db),
        )
        ok, output = _run_helper("up", str(_config_file()))
        if not ok:
            _run_helper("down")
            _config_file().unlink(missing_ok=True)
            logger.warning("Upload Wi-Fi did not come up: %s", output)
            if chosen.parent:
                raise HandoverError(
                    502,
                    "Die Box kann neben ihrer WLAN-Verbindung kein Upload-WLAN öffnen. "
                    "Handy ins selbe Netz wie die Box bringen, oder die Box per Kabel "
                    "bzw. zweitem WLAN-Stick anbinden.",
                )
            raise HandoverError(502, "Das Upload-WLAN ließ sich nicht starten.")
        band = _reported_band(output)

    now = time.monotonic()
    session = _Session(
        device_id=device_id,
        user_id=user.id,
        jti=uuid.uuid4().hex,
        mode=chosen.mode,
        interface=chosen.interface,
        ssid=ssid,
        passphrase=passphrase,
        address=address,
        band=band,
        started=now,
        last_activity=now,
    )
    with _lock:
        _active = session
    where = chosen.interface or "Rückfall-WLAN"
    beside = f" neben {chosen.parent}" if chosen.parent else ""
    audit(db, user, "uplink.handover_start", f"{device.name} über {where}{beside}")
    return _connection(db, user, session)


def _reported_band(output: str) -> str:
    """The helper prints ``band=5GHz`` when the AP had to follow a 5 GHz client."""
    for line in output.splitlines():
        if line.startswith("band=") and line[5:] in {"2.4GHz", "5GHz"}:
            return line[5:]
    return BAND


def _write_config(interface: str, parent: str, ssid: str, passphrase: str, net) -> None:
    path = _config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {
                "interface": interface,
                # Set: create ``interface`` as a virtual AP beside this
                # adapter's Wi-Fi client, on the client's channel.
                "parent": parent,
                "ssid": ssid,
                "passphrase": passphrase,
                "address": AP_ADDRESS,
                "channel": AP_CHANNEL,
                "country": net.country_code,
                # Root-side backstop: the watchdog drops the Wi-Fi after this
                # even if the backend is gone.
                "deadline": int(time.time()) + MAX_DURATION,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    tmp.chmod(0o600)
    tmp.replace(path)


def stop(db: Session, reason: str, *, device_id: int | None = None) -> bool:
    """End the running handover (only ``device_id``'s, if given). False if none."""
    global _active
    with _lock:
        session = _active
        if session is None or (device_id is not None and session.device_id != device_id):
            return False
        _active = None
    if session.mode == "ap":
        ok, output = _run_helper("down")
        if not ok:
            logger.warning("Upload Wi-Fi did not go down cleanly: %s", output)
        _config_file().unlink(missing_ok=True)
    minutes = (time.monotonic() - session.started) / 60
    audit(
        db,
        db.get(User, session.user_id),
        "uplink.handover_end",
        f"{reason}, nach {minutes:.0f} min",
    )
    return True


def token_valid(db: Session, payload: dict) -> bool:
    """Upload token of the running session, from a device that is still paired."""
    with _lock:
        session = _active
        if (
            session is None
            or payload.get("jti") != session.jti
            or payload.get("dev") != session.device_id
        ):
            return False
        session.last_activity = time.monotonic()
    return db.get(BleDevice, session.device_id) is not None


def expire(db: Session, now: float | None = None) -> None:
    """Drop the Wi-Fi after the idle timeout or the maximum duration."""
    session = _active
    if session is None:
        return
    now = time.monotonic() if now is None else now
    if now - session.started >= MAX_DURATION:
        stop(db, "Höchstdauer erreicht", device_id=session.device_id)
    elif now - session.last_activity >= IDLE_TIMEOUT:
        stop(db, "Leerlauf", device_id=session.device_id)


def cleanup_on_startup() -> None:
    """A Wi-Fi left over from before a backend restart has no session any more."""
    if _config_file().exists():
        if _helper_command():
            _run_helper("down")
        _config_file().unlink(missing_ok=True)


def _expire_tick() -> None:
    from .db import SessionLocal

    with SessionLocal() as db:
        expire(db)


async def expiry_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=CHECK_INTERVAL)
            break
        except TimeoutError:
            pass
        try:
            await asyncio.to_thread(_expire_tick)
        except Exception:  # pragma: no cover - keep the loop alive
            logger.exception("Handover expiry check failed")
    # Shutting down: don't leave the box offline behind us.
    await asyncio.to_thread(_shutdown)


def _shutdown() -> None:
    from .db import SessionLocal

    if _active is None:
        return
    with SessionLocal() as db:
        stop(db, "Dienst beendet")
