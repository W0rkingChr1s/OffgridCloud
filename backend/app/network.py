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
import threading
import time
from dataclasses import asdict, dataclass, field
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

# USB tethering: a phone plugged into the box shows up as an ordinary
# ``ethernet`` device, so only its kernel driver tells it apart from a cable.
# ``cdc_ether`` is deliberately missing: plenty of USB-Ethernet dongles use it
# too, so it only counts as a phone when NetworkManager also reports the link as
# metered (Android sends the ANDROID_METERED DHCP option).
TETHER_DRIVERS = {
    "ipheth": "iphone",
    "rndis_host": "android",
    "cdc_ncm": "android",
}
TETHER_LABELS = {"iphone": "iPhone", "android": "Android"}

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
class Uplink:
    """One connected link that can reach the internet (cable, Wi-Fi, phone)."""

    device: str  # kernel name, e.g. eth0 / wlan0 / usb0 — what net-merge lists
    kind: str  # ethernet | client | tether
    label: str  # short German name for the UI, e.g. "WLAN MyRouter"
    ip: str | None = None
    metered: bool = False
    primary: bool = False  # carries the default route (single-link uploads)


@dataclass
class NetworkStatus:
    """Snapshot of the box's connectivity for the admin UI."""

    supported: bool = False  # NetworkManager present → live control possible
    apply_wired: bool = False  # privileged apply helper configured
    mode: str = "unknown"  # ethernet | client | tether | ap | offline | unknown
    online: bool = False  # NetworkManager reports full/limited connectivity
    connectivity: str = "unknown"  # full | limited | portal | none | unknown
    ethernet: bool = False  # a wired connection carries an address
    wifi_ssid: str | None = None  # joined client SSID (None in AP/offline mode)
    wifi_ip: str | None = None
    ap_active: bool = False  # the box is currently hosting its fallback AP
    ap_ssid: str | None = None
    tether: str | None = None  # "iphone" | "android" while a phone tethers via USB
    tether_ip: str | None = None
    metered: bool = False  # the uplink in use is metered (mobile data)
    metered_present: bool = False  # any connected link is metered, used or not
    uplink_ip: str | None = None  # IPv4 the box currently routes out through
    links: list[Uplink] = field(default_factory=list)  # every uplink, primary first
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


def parse_metered(value: str) -> bool:
    """``GENERAL.METERED`` reads ``yes``, ``yes (guessed)``, ``no (guessed)``, ``unknown``."""
    return value.strip().lower().startswith("yes")


def tether_kind(driver: str, *, metered: bool = False) -> str | None:
    """``"iphone"``/``"android"`` if ``driver`` belongs to a USB-tethered phone."""
    driver = driver.strip().lower()
    if driver in TETHER_DRIVERS:
        return TETHER_DRIVERS[driver]
    if driver == "cdc_ether" and metered:
        return "android"
    return None


def parse_device_details(raw: str) -> dict[str, str]:
    """Parse ``nmcli -t -f GENERAL.DRIVER,GENERAL.METERED,IP4.ADDRESS device show <dev>``.

    Returns ``driver``, ``metered`` (``"yes"``/``"no"``) and the first IPv4
    ``ip`` (without prefix length); missing fields come back empty.
    """
    out = {"driver": "", "metered": "no", "ip": ""}
    for line in raw.splitlines():
        key, sep, value = line.partition(_SEP)
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if key == "GENERAL.DRIVER":
            out["driver"] = value
        elif key == "GENERAL.METERED":
            out["metered"] = "yes" if parse_metered(value) else "no"
        elif key.startswith("IP4.ADDRESS") and value and not out["ip"]:
            out["ip"] = value.split("/")[0].strip()
    return out


def build_status(
    *,
    devices: list[dict[str, str]],
    connectivity: str,
    wifi_ip: str | None = None,
    ap_connection_name: str = AP_CONNECTION_NAME,
    device_info: dict[str, dict[str, str]] | None = None,
    uplink_ip: str | None = None,
) -> NetworkStatus:
    """Derive a :class:`NetworkStatus` from parsed nmcli output (pure).

    ``device_info`` maps device names to :func:`parse_device_details` output and
    is what tells a USB-tethered phone apart from a real cable. ``uplink_ip`` is
    the source address of the default route (see ``netinfo.internal_ip``); it
    decides which link actually carries the uploads when several are up.
    """
    info = device_info or {}
    status = NetworkStatus(supported=True, connectivity=connectivity, uplink_ip=uplink_ip)
    # Uplink candidates as (mode, device), so the one the default route uses
    # can be picked below.
    uplinks: list[tuple[str, str]] = []

    for dev in devices:
        connected = dev["state"].startswith("connected")
        conn = dev["connection"]
        details = info.get(dev["device"], {})
        if dev["type"] == "ethernet" and connected:
            kind = tether_kind(
                details.get("driver", ""), metered=details.get("metered") == "yes"
            )
            if kind:
                status.tether = kind
                status.tether_ip = details.get("ip") or None
                uplinks.append(("tether", dev["device"]))
            else:
                status.ethernet = True
                uplinks.append(("ethernet", dev["device"]))
        elif dev["type"] == "wifi" and connected and conn:
            if conn == ap_connection_name:
                status.ap_active = True
                status.ap_ssid = conn
            else:
                status.wifi_ssid = conn
                status.wifi_ip = wifi_ip or details.get("ip") or None
                uplinks.append(("client", dev["device"]))

    # "Online" means the box has a usable uplink so the fallback AP isn't needed.
    # An active client/Ethernet connection IS that uplink — don't rely solely on
    # NetworkManager's internet portal check, which returns "none"/"unknown" on
    # many setups (connectivity checking disabled, netplan/networkd-rendered
    # profiles) even while a real network with an IP is connected. That false
    # "offline" would otherwise read as a dead link in the UI.
    status.online = bool(uplinks) or connectivity in {"full", "limited", "portal"}

    # Which link carries the traffic: the one owning the default route's source
    # address. Without that, fall back to the route-metric order the netfallback
    # NetworkManager drop-in sets up (cable 100, Wi-Fi 600, phone 700).
    uplink = next(
        (u for u in uplinks if uplink_ip and info.get(u[1], {}).get("ip") == uplink_ip),
        None,
    )
    if uplink is None:
        order = {"ethernet": 0, "client": 1, "tether": 2}
        uplink = min(uplinks, key=lambda u: order[u[0]], default=None)
    if uplink is not None:
        details = info.get(uplink[1], {})
        status.metered = uplink[0] == "tether" or details.get("metered") == "yes"
    for mode, dev in uplinks:
        details = info.get(dev, {})
        if mode == "tether":
            label = TETHER_LABELS.get(status.tether or "", "Handy")
        elif mode == "client":
            label = "WLAN"
        else:
            label = "Kabel"
        status.links.append(
            Uplink(
                device=dev,
                kind=mode,
                label=label,
                ip=details.get("ip") or (status.wifi_ip if mode == "client" else None),
                metered=mode == "tether" or details.get("metered") == "yes",
                primary=(mode, dev) == uplink,
            )
        )
    status.links.sort(key=lambda link: not link.primary)
    # Link bundling uses every link, not just the default route's — so it has
    # to know about a phone sitting in reserve too.
    status.metered_present = any(
        mode == "tether" or info.get(dev, {}).get("metered") == "yes" for mode, dev in uplinks
    )

    if status.ap_active:
        status.mode = "ap"
    elif uplink is not None:
        status.mode = uplink[0]
    else:
        status.mode = "offline"
    return status


def describe_uplink(status: NetworkStatus) -> str:
    """Short German label of the link in use, e.g. ``USB-Tethering (iPhone)``."""
    if status.mode == "tether":
        return f"USB-Tethering ({TETHER_LABELS.get(status.tether or '', 'Handy')})"
    if status.mode == "client":
        return f"WLAN {status.wifi_ssid}" if status.wifi_ssid else "WLAN"
    if status.mode == "ethernet":
        return "Kabel (Ethernet)"
    if status.mode == "ap":
        return "Eigener Access Point"
    if status.mode == "offline":
        return "Offline"
    return "Unbekannt"


def _device_info(devices: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    """Driver, metered flag and IPv4 of every connected device (one nmcli call each)."""
    out: dict[str, dict[str, str]] = {}
    for dev in devices:
        if not dev["state"].startswith("connected") or dev["type"] in ("loopback", ""):
            continue
        raw = _run_nmcli(
            [
                "-t",
                "-f",
                "GENERAL.DRIVER,GENERAL.METERED,IP4.ADDRESS",
                "device",
                "show",
                dev["device"],
            ]
        )
        if raw:
            out[dev["device"]] = parse_device_details(raw)
    return out


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
    from .netinfo import internal_ip

    devices = parse_device_status(dev_raw)
    connectivity = parse_connectivity(conn_raw) if conn_raw is not None else "unknown"
    status = build_status(
        devices=devices,
        connectivity=connectivity,
        device_info=_device_info(devices),
        uplink_ip=internal_ip(),
    )
    if status.wifi_ssid:
        # build_status only sees the connection *name* (e.g. the Imager/netplan
        # profile "netplan-wlan0-MyRouter"); show the actual SSID instead.
        ssid = _wifi_details(status.wifi_ssid).get("ssid")
        if ssid:
            status.wifi_ssid = ssid
    for link in status.links:
        if link.kind == "client" and status.wifi_ssid:
            link.label = f"WLAN {status.wifi_ssid}"
    status.apply_wired = apply_wired
    return status


# The transfer worker asks before every job whether the uplink is metered. A few
# nmcli calls per poll would be noticeable on a Pi 3, and a phone isn't plugged
# in or out every second, so the answer is cached briefly.
METERED_CACHE_SECONDS = 30.0
_metered_cache: tuple[float, bool, str, bool] | None = None
_metered_lock = threading.Lock()


def _metered_state(max_age: float) -> tuple[bool, str, bool]:
    global _metered_cache
    with _metered_lock:
        now = time.monotonic()
        if _metered_cache is not None and now - _metered_cache[0] < max_age:
            return _metered_cache[1], _metered_cache[2], _metered_cache[3]
        status = get_status()
        result = (
            status.supported and status.metered,
            describe_uplink(status),
            status.supported and status.metered_present,
        )
        _metered_cache = (now, *result)
        return result


def metered_uplink(*, max_age: float = METERED_CACHE_SECONDS) -> tuple[bool, str]:
    """``(metered, label)`` of the uplink in use, cached for ``max_age`` seconds.

    Without NetworkManager the box can't tell, so it reports "not metered" and
    the metered-upload rules stay inactive instead of blocking every upload.
    """
    metered, label, _ = _metered_state(max_age)
    return metered, label


def metered_link_present(*, max_age: float = METERED_CACHE_SECONDS) -> bool:
    """True while any metered link (e.g. a phone in reserve) is connected."""
    return _metered_state(max_age)[2]


def reset_metered_cache() -> None:
    """Forget the cached metered state (used by tests)."""
    global _metered_cache
    with _metered_lock:
        _metered_cache = None


# --------------------------------------------------------------------------- #
# System Wi-Fi profiles (read-only)                                            #
# --------------------------------------------------------------------------- #
@dataclass
class SystemNetwork:
    """A Wi-Fi client profile NetworkManager knows that OffgridCloud doesn't manage.

    Typically the network entered in Raspberry Pi Imager (rendered via netplan as
    ``netplan-wlan0-<SSID>``) or one added by hand with ``nmcli``/``nmtui``.
    NetworkManager auto-joins these just like our own profiles, so the admin must
    see them — but the unprivileged app can't read their secrets or change them.
    """

    name: str  # NetworkManager connection name
    ssid: str
    priority: int = 0
    autoconnect: bool = True
    has_password: bool = False
    active: bool = False


def parse_connection_list(raw: str) -> list[dict[str, str]]:
    """Parse ``nmcli -t -f NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY,ACTIVE connection show``."""
    rows: list[dict[str, str]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = _split_terse(line)
        if len(parts) < 5:
            continue
        rows.append(
            {
                "name": parts[0],
                "type": parts[1],
                "autoconnect": parts[2],
                "priority": parts[3],
                "active": parts[4],
            }
        )
    return rows


def parse_connection_details(raw: str) -> dict[str, str]:
    """Parse ``nmcli -t -f <fields> connection show <name>`` (one ``key:value`` per line).

    Keys never contain ':' so everything after the first separator is the value.
    """
    out: dict[str, str] = {}
    for line in raw.splitlines():
        key, sep, value = line.partition(_SEP)
        if sep:
            out[key.strip()] = value.strip()
    return out


def _wifi_details(name: str) -> dict[str, str]:
    """SSID, mode and key management of a Wi-Fi profile (no secrets needed)."""
    raw = _run_nmcli(
        [
            "-t",
            "-f",
            "802-11-wireless.ssid,802-11-wireless.mode,802-11-wireless-security.key-mgmt",
            "connection",
            "show",
            name,
        ]
    )
    if not raw:
        return {}
    d = parse_connection_details(raw)
    return {
        "ssid": d.get("802-11-wireless.ssid", ""),
        "mode": d.get("802-11-wireless.mode", ""),
        "key_mgmt": d.get("802-11-wireless-security.key-mgmt", ""),
    }


def build_system_networks(
    rows: list[dict[str, str]],
    details: dict[str, dict[str, str]],
    *,
    ap_connection_name: str = AP_CONNECTION_NAME,
    client_prefix: str = CLIENT_CONNECTION_PREFIX,
) -> list[SystemNetwork]:
    """Pick the foreign Wi-Fi *client* profiles out of the connection list (pure)."""
    out: list[SystemNetwork] = []
    for row in rows:
        name = row["name"]
        if row["type"] not in ("802-11-wireless", "wifi"):
            continue
        if name == ap_connection_name or name.startswith(client_prefix):
            continue
        info = details.get(name, {})
        if info.get("mode") in ("ap", "adhoc", "mesh"):
            continue
        key_mgmt = info.get("key_mgmt", "")
        out.append(
            SystemNetwork(
                name=name,
                ssid=info.get("ssid") or name,
                priority=int(row["priority"]) if row["priority"].lstrip("-").isdigit() else 0,
                autoconnect=row["autoconnect"] == "yes",
                has_password=key_mgmt not in ("", "--", "none", "owe"),
                active=row["active"] == "yes",
            )
        )
    out.sort(key=lambda n: n.priority, reverse=True)
    return out


def get_system_networks() -> list[SystemNetwork]:
    """Wi-Fi profiles configured outside OffgridCloud (best-effort, [] if unsupported)."""
    if not nmcli_available():
        return []
    raw = _run_nmcli(
        ["-t", "-f", "NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY,ACTIVE", "connection", "show"]
    )
    if not raw:
        return []
    rows = parse_connection_list(raw)
    details = {
        r["name"]: _wifi_details(r["name"])
        for r in rows
        if r["type"] in ("802-11-wireless", "wifi")
        and r["name"] != AP_CONNECTION_NAME
        and not r["name"].startswith(CLIENT_CONNECTION_PREFIX)
    }
    return build_system_networks(rows, details)


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
        "ap_probe_interval": settings.ap_probe_interval,
        "ap_blind_probe_interval": settings.ap_blind_probe_interval,
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
                "eingerichtet — einmalig auf dem Server einrichten: "
                "sudo /opt/offgridcloud/src/deploy/netfallback/install.sh "
                "(danach wirkt „Anwenden“ automatisch)."
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


def wifi_interfaces() -> list[str]:
    """Kernel names of the Wi-Fi adapters NetworkManager sees, e.g. ``["wlan0"]``.

    Includes adapters NetworkManager currently leaves alone (``unmanaged``),
    such as one hosting the upload handover's Wi-Fi.
    """
    if not nmcli_available():
        return []
    raw = _run_nmcli(["-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
    if not raw:
        return []
    return [row["device"] for row in parse_device_status(raw) if row["type"] == "wifi"]


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
