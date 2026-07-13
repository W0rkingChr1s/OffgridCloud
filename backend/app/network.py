"""Network redundancy — access-point fallback ("Rückfallebene").

The appliance normally joins an existing network (Wi-Fi client or Ethernet) to
reach the cloud. If that upstream disappears, the field team must still be able
to hand media to the box. This module lets an admin:

* keep a list of **known Wi-Fi networks** the box should join automatically, and
* configure a **fallback access point** the box hosts itself when no known
  network is reachable — so devices can always connect and upload locally.

Design mirrors the opt-in self-update model: the app runs unprivileged and only
*reads* the live state via ``nmcli`` (read-only D-Bus queries work as any user)
and *exports* the desired configuration to a JSON file. Actually applying it —
creating NetworkManager connections and flipping the AP up/down — needs root, so
it is delegated to an opt-in privileged helper (``deploy/netfallback/apply.sh``)
and a root watchdog timer. Everything degrades gracefully: with no NetworkManager
and no wired-up helper, the API still answers with ``supported=False`` instead of
raising, exactly like the update check when the box is offline.

The subprocess call is kept separate from the pure parsers so the parsing logic
is unit-testable without a real ``nmcli`` on the test host.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .crypto import decrypt, encrypt
from .models import KnownNetwork, NetworkSettings

# NetworkManager connection name the box hosts as its fallback AP. The watchdog
# and apply helper share this convention; the app uses it to recognise when the
# AP (rather than a normal client link) is the active connection.
AP_CONNECTION_NAME = "offgridcloud-ap"
# Prefix for the client connections the apply helper creates from known networks.
CLIENT_CONNECTION_PREFIX = "ogc-wifi-"

# nmcli field separator (``-t`` terse mode escapes literal ':' as '\:').
_SEP = ":"


# --------------------------------------------------------------------------- #
# Validation helpers (pure)                                                    #
# --------------------------------------------------------------------------- #
def validate_ssid(ssid: str) -> str:
    ssid = ssid.strip()
    if not 1 <= len(ssid.encode("utf-8")) <= 32:
        raise ValueError("SSID muss 1–32 Bytes lang sein")
    return ssid


def validate_passphrase(psk: str, *, allow_empty: bool) -> str:
    """WPA2-PSK requires 8–63 ASCII characters. Empty = open network."""
    psk = psk or ""
    if psk == "":
        if allow_empty:
            return ""
        raise ValueError("Passwort erforderlich")
    if not 8 <= len(psk) <= 63:
        raise ValueError("WLAN-Passwort muss 8–63 Zeichen lang sein")
    return psk


def validate_country(code: str) -> str:
    code = (code or "").strip().upper()
    if code and not re.fullmatch(r"[A-Z]{2}", code):
        raise ValueError("Ländercode muss ISO 3166-1 alpha-2 sein, z. B. DE")
    return code


# --------------------------------------------------------------------------- #
# Live status (nmcli)                                                          #
# --------------------------------------------------------------------------- #
@dataclass
class NetworkStatus:
    """Snapshot of the box's connectivity for the admin UI."""

    supported: bool = False  # NetworkManager present → live control possible
    apply_wired: bool = False  # privileged apply helper configured
    mode: str = "unknown"  # ethernet | client | ap | offline | unknown
    online: bool = False  # NetworkManager reports full/limited connectivity
    connectivity: str = "unknown"  # full | limited | portal | none | unknown
    ethernet: bool = False  # a wired connection carries an address
    wifi_ssid: str | None = None  # joined client SSID (None in AP/offline mode)
    wifi_ip: str | None = None
    ap_active: bool = False  # the box is currently hosting its fallback AP
    ap_ssid: str | None = None
    detail: str = ""  # human-readable note (why unsupported, etc.)


def nmcli_available() -> bool:
    return shutil.which("nmcli") is not None


def _run_nmcli(args: list[str], *, timeout: float = 5.0) -> str | None:
    """Run a read-only ``nmcli`` query; return stdout or None on any failure."""
    try:
        proc = subprocess.run(  # noqa: S603 (fixed binary, no shell)
            ["nmcli", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _split_terse(line: str) -> list[str]:
    """Split an ``nmcli -t`` line, honouring its ``\\:`` escaping of literal ':'."""
    out: list[str] = []
    buf: list[str] = []
    escaped = False
    for ch in line:
        if escaped:
            buf.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == _SEP:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return out


def parse_device_status(raw: str) -> list[dict[str, str]]:
    """Parse ``nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status``."""
    rows: list[dict[str, str]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = _split_terse(line)
        if len(parts) < 4:
            continue
        rows.append(
            {"device": parts[0], "type": parts[1], "state": parts[2], "connection": parts[3]}
        )
    return rows


def parse_connectivity(raw: str) -> str:
    value = raw.strip().lower()
    return value or "unknown"


def build_status(
    *,
    devices: list[dict[str, str]],
    connectivity: str,
    wifi_ip: str | None = None,
    ap_connection_name: str = AP_CONNECTION_NAME,
) -> NetworkStatus:
    """Derive a :class:`NetworkStatus` from parsed nmcli output (pure)."""
    status = NetworkStatus(supported=True, connectivity=connectivity)
    status.online = connectivity in {"full", "limited", "portal"}

    for dev in devices:
        connected = dev["state"].startswith("connected")
        conn = dev["connection"]
        if dev["type"] == "ethernet" and connected:
            status.ethernet = True
        elif dev["type"] == "wifi" and connected and conn:
            if conn == ap_connection_name:
                status.ap_active = True
                status.ap_ssid = conn
            else:
                status.wifi_ssid = conn
                status.wifi_ip = wifi_ip

    if status.ap_active:
        status.mode = "ap"
    elif status.ethernet:
        status.mode = "ethernet"
    elif status.wifi_ssid:
        status.mode = "client"
    else:
        status.mode = "offline"
    return status


def _wifi_ip(devices: list[dict[str, str]], ap_connection_name: str) -> str | None:
    """Best-effort IPv4 of the active *client* Wi-Fi device."""
    for dev in devices:
        if (
            dev["type"] == "wifi"
            and dev["state"].startswith("connected")
            and dev["connection"] not in ("", ap_connection_name)
        ):
            raw = _run_nmcli(["-t", "-f", "IP4.ADDRESS", "device", "show", dev["device"]])
            if raw:
                for line in raw.splitlines():
                    _, _, value = line.partition(_SEP)
                    if value.strip():
                        return value.split("/")[0].strip()
    return None


def get_status(*, apply_wired: bool = False) -> NetworkStatus:
    """Query NetworkManager for the current connectivity, degrading gracefully."""
    if not nmcli_available():
        return NetworkStatus(
            supported=False,
            apply_wired=apply_wired,
            detail=(
                "NetworkManager (nmcli) nicht gefunden — die Live-Steuerung ist auf "
                "diesem Host nicht verfügbar. Auf Raspberry Pi OS (Bookworm) ist es "
                "der Standard; siehe docs/NETZWERK-REDUNDANZ.md."
            ),
        )
    dev_raw = _run_nmcli(["-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
    conn_raw = _run_nmcli(["networking", "connectivity", "check"])
    if dev_raw is None:
        return NetworkStatus(
            supported=False,
            apply_wired=apply_wired,
            detail="nmcli-Abfrage fehlgeschlagen (läuft der NetworkManager-Dienst?).",
        )
    devices = parse_device_status(dev_raw)
    connectivity = parse_connectivity(conn_raw) if conn_raw is not None else "unknown"
    status = build_status(
        devices=devices,
        connectivity=connectivity,
        wifi_ip=_wifi_ip(devices, AP_CONNECTION_NAME),
    )
    status.apply_wired = apply_wired
    return status


# --------------------------------------------------------------------------- #
# Configuration export + apply                                                 #
# --------------------------------------------------------------------------- #
def get_network_settings(db: Session) -> NetworkSettings:
    row = db.get(NetworkSettings, 1)
    if row is None:
        row = NetworkSettings(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def ensure_network_settings() -> None:
    from .db import SessionLocal

    with SessionLocal() as db:
        if db.get(NetworkSettings, 1) is None:
            db.add(NetworkSettings(id=1))
            db.commit()


def export_config(db: Session) -> dict:
    """Build the plaintext config the privileged helper + watchdog consume.

    Wi-Fi passphrases are decrypted here because NetworkManager needs them in
    the clear to create connections (it stores them plaintext under
    ``/etc/NetworkManager/system-connections`` anyway). The file is written
    ``0600`` in the data dir — see :func:`write_config_file`.
    """
    settings = get_network_settings(db)
    known = db.scalars(select(KnownNetwork).order_by(KnownNetwork.priority.desc())).all()
    return {
        "fallback_enabled": settings.fallback_enabled,
        "ap_connection_name": AP_CONNECTION_NAME,
        "client_prefix": CLIENT_CONNECTION_PREFIX,
        "ap": {
            "ssid": settings.ap_ssid,
            "passphrase": decrypt(settings.ap_psk_encrypted) if settings.ap_psk_encrypted else "",
            "hidden": settings.ap_hidden,
            "country": settings.country_code,
            "address": settings.ap_address,
        },
        "check_interval": settings.check_interval,
        "fail_threshold": settings.fail_threshold,
        "known_networks": [
            {
                "ssid": n.ssid,
                "passphrase": decrypt(n.psk_encrypted) if n.psk_encrypted else "",
                "priority": n.priority,
                "autoconnect": n.autoconnect,
            }
            for n in known
        ],
    }


def config_path() -> Path:
    return get_settings().network_config_path


def write_config_file(db: Session) -> Path:
    """Export the desired network config to disk (``0600``) for the helper."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(export_config(db), indent=2), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)
    return path


@dataclass
class ApplyResult:
    ok: bool
    message: str
    output: str = ""


def apply_config(db: Session) -> ApplyResult:
    """Write the config file and invoke the opt-in privileged apply helper.

    Without a wired-up ``OGC_NET_APPLY_COMMAND`` the config is still exported
    (so the root watchdog/timer can act on it), and we report that manual/root
    application is required — mirroring the self-update opt-in.
    """
    write_config_file(db)
    command = get_settings().net_apply_command.strip()
    if not command:
        return ApplyResult(
            ok=False,
            message=(
                "Konfiguration gespeichert. Automatisches Anwenden ist nicht "
                "eingerichtet — auf dem Server ausführen: "
                "sudo /opt/offgridcloud/src/deploy/netfallback/apply.sh"
            ),
        )
    try:
        proc = subprocess.run(  # noqa: S603 (admin-configured command)
            shlex.split(command),
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ApplyResult(ok=False, message=f"Anwenden fehlgeschlagen: {exc}")
    output = (proc.stdout + proc.stderr).strip()[-2000:]
    if proc.returncode != 0:
        return ApplyResult(ok=False, message="Anwenden fehlgeschlagen.", output=output)
    return ApplyResult(ok=True, message="Netzwerk-Konfiguration angewendet.", output=output)


def scan_wifi() -> list[str]:
    """Return visible SSIDs (best-effort). Empty list if unsupported."""
    if not nmcli_available():
        return []
    _run_nmcli(["device", "wifi", "rescan"], timeout=10.0)
    raw = _run_nmcli(["-t", "-f", "SSID,SIGNAL", "device", "wifi", "list"], timeout=10.0)
    if not raw:
        return []
    seen: dict[str, int] = {}
    for line in raw.splitlines():
        parts = _split_terse(line)
        if not parts or not parts[0].strip():
            continue
        ssid = parts[0].strip()
        signal = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        seen[ssid] = max(seen.get(ssid, 0), signal)
    return [ssid for ssid, _ in sorted(seen.items(), key=lambda kv: kv[1], reverse=True)]


# --------------------------------------------------------------------------- #
# Known-network CRUD helpers                                                   #
# --------------------------------------------------------------------------- #
def set_known_psk(network: KnownNetwork, psk: str, *, allow_empty: bool = True) -> None:
    psk = validate_passphrase(psk, allow_empty=allow_empty)
    network.psk_encrypted = encrypt(psk) if psk else ""


def status_dict(status: NetworkStatus) -> dict:
    return asdict(status)
