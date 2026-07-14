"""System VPN client management (WireGuard / OpenVPN).

Lets an off-site OffgridCloud dial into a home LAN so internal-only targets — a
NAS reachable via SMB on a private ``192.168.x.y`` address, for instance — become
usable. Once a tunnel is up, a normal SMB provider pointed at the private IP just
works.

Bringing up a tunnel is a *system-level* operation: it creates a TUN interface
and manipulates the routing table, which requires the ``NET_ADMIN`` capability
and access to ``/dev/net/tun``. Those are not granted by default, so
:func:`capabilities` reports exactly what's missing and the API surfaces a clear
message instead of failing opaquely. How you grant them differs by deployment —
Docker needs ``--cap-add`` / ``--device`` flags, a native systemd install needs
``AmbientCapabilities`` on the unit — so the guidance is tailored to the detected
environment (see :func:`in_docker` and :meth:`Capabilities.blocker`).

Only one tunnel is active at a time (a single default path into the remote LAN).
Connecting a profile tears down any other active tunnel first.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import get_settings

logger = logging.getLogger("offgridcloud.vpn")

# Fixed WireGuard interface name. We bring the tunnel up with ``ip`` + ``wg``
# directly (never ``wg-quick``): wg-quick's ``auto_su`` re-execs itself through
# ``sudo`` whenever ``$UID != 0``, so under the service's ambient CAP_NET_ADMIN
# (non-root) it fails with "sudo: a password is required". ``ip``/``wg`` honour
# CAP_NET_ADMIN without any UID check, so the shipped capability model works.
_WG_IFACE = "ogc-wg"
_IP_BIN = "ip"

# wg-quick config directives that the kernel/``wg`` don't understand — they are
# wg-quick's own interface/routing sugar, applied here with ``ip`` instead and
# stripped before the config is handed to ``wg setconf``.
_WG_QUICK_ONLY_KEYS = frozenset(
    {"address", "dns", "mtu", "table", "preup", "postup", "predown", "postdown", "saveconfig"}
)
_CAP_NET_ADMIN = 12  # capability bit index for CAP_NET_ADMIN

# Path the native install helper uses to grant the systemd service NET_ADMIN.
_NATIVE_ENABLE_CMD = "sudo /opt/offgridcloud/src/deploy/vpn/install.sh"


# --- Environment detection ------------------------------------------------


def in_docker() -> bool:
    """Best-effort: are we running inside a container rather than natively?

    Governs which remediation to show — Docker flags vs. a systemd capability
    drop-in. ``OGC_IN_DOCKER`` (set in the image) is authoritative; otherwise we
    look for the classic markers.
    """
    override = os.environ.get("OGC_IN_DOCKER")
    if override is not None:
        return override.strip().lower() in {"1", "true", "yes"}
    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text()
        if "docker" in cgroup or "containerd" in cgroup or "kubepods" in cgroup:
            return True
    except OSError:  # pragma: no cover - non-Linux / unreadable
        pass
    return False


# --- Capability detection -------------------------------------------------


@dataclass(frozen=True)
class Capabilities:
    net_admin: bool
    tun_device: bool
    wireguard: bool  # wg + wg-quick present
    openvpn: bool  # openvpn present

    def supports(self, vpn_type: str) -> bool:
        base = self.net_admin and self.tun_device
        if vpn_type == "wireguard":
            return base and self.wireguard
        if vpn_type == "openvpn":
            return base and self.openvpn
        return False

    def blocker(self, vpn_type: str, *, docker: bool | None = None) -> str:
        """Human-readable reason a tunnel of ``vpn_type`` can't start (or "").

        The remediation is tailored to the deployment: inside Docker the fix is
        run-flags, on a native systemd install it's the enable helper. ``docker``
        is auto-detected when not given.
        """
        if docker is None:
            docker = in_docker()
        if not self.tun_device:
            return (
                "Kein /dev/net/tun im Container (mit --device=/dev/net/tun starten)."
                if docker
                else "Kein /dev/net/tun — TUN-Modul laden: sudo modprobe tun "
                f"(dauerhaft richtet {_NATIVE_ENABLE_CMD} es ein)."
            )
        if not self.net_admin:
            return (
                "Fehlende NET_ADMIN-Berechtigung (Container mit --cap-add=NET_ADMIN starten)."
                if docker
                else "Dem Dienst fehlt die NET_ADMIN-Berechtigung — nativ aktivieren mit: "
                f"{_NATIVE_ENABLE_CMD}"
            )
        if vpn_type == "wireguard" and not self.wireguard:
            return "wireguard-tools (wg/wg-quick) nicht installiert."
        if vpn_type == "openvpn" and not self.openvpn:
            return "openvpn nicht installiert."
        return ""


def _has_net_admin() -> bool:
    """Read the effective capability set from /proc and check CAP_NET_ADMIN."""
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("CapEff:"):
                caps = int(line.split()[1], 16)
                return bool((caps >> _CAP_NET_ADMIN) & 1)
    except (OSError, ValueError):  # pragma: no cover - non-Linux / unreadable
        pass
    return False


def capabilities() -> Capabilities:
    return Capabilities(
        net_admin=_has_net_admin(),
        tun_device=Path("/dev/net/tun").exists(),
        # We drive WireGuard with ``wg`` + ``ip`` (not ``wg-quick``), so those
        # two — not wg-quick — are what a WireGuard tunnel actually needs.
        wireguard=bool(shutil.which("wg") and shutil.which(_IP_BIN)),
        openvpn=bool(shutil.which("openvpn")),
    )


# --- Runtime state --------------------------------------------------------


@dataclass
class Result:
    ok: bool
    message: str = ""


@dataclass
class TunnelStatus:
    active_id: int | None = None
    state: str = "down"  # down | up | error
    detail: str = ""
    endpoint: str = ""
    last_handshake: str = ""
    extra: dict = field(default_factory=dict)


def _runtime_dir() -> Path:
    d = get_settings().data_dir / "vpn"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:  # pragma: no cover
        pass
    return d


def _write_private(path: Path, content: str) -> None:
    path.write_text(content)
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover
        pass


def _wg_conf() -> Path:
    return _runtime_dir() / f"{_WG_IFACE}.conf"


def _ovpn_conf() -> Path:
    return _runtime_dir() / "ogc.ovpn"


def _ovpn_auth() -> Path:
    return _runtime_dir() / "ogc.auth"


def _ovpn_pid() -> Path:
    return _runtime_dir() / "ogc.pid"


def _ovpn_log() -> Path:
    return _runtime_dir() / "ogc.log"


def _marker() -> Path:
    return _runtime_dir() / "active.json"


def _run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _stderr_tail(result: subprocess.CompletedProcess) -> str:
    lines = [ln for ln in (result.stderr or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


# --- WireGuard ------------------------------------------------------------


def parse_wg_config(config: str) -> tuple[str, list[str], str | None, list[str]]:
    """Split a wg-quick config into the parts ``wg`` and ``ip`` each need.

    A wg-quick config mixes two things: keys the kernel understands (PrivateKey,
    ListenPort, FwMark and the ``[Peer]`` blocks) and wg-quick's own directives
    (Address, MTU, DNS, Table, Pre/Post hooks). ``wg setconf`` only accepts the
    former, so we strip the latter and apply the interface address / MTU / routes
    ourselves with ``ip``.

    Returns ``(wg_conf, addresses, mtu, routes)``:

    * ``wg_conf`` — the config with wg-quick-only keys removed, ready for
      ``wg setconf``;
    * ``addresses`` — the ``Address =`` CIDRs to assign to the interface;
    * ``mtu`` — the ``MTU`` value if given, else ``None``;
    * ``routes`` — the peers' ``AllowedIPs`` CIDRs to route into the tunnel.

    Pure (no I/O) so it can be unit-tested without a real interface.
    """
    addresses: list[str] = []
    mtu: str | None = None
    routes: list[str] = []
    kept: list[str] = []
    section = ""
    for raw in config.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            kept.append(raw)
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            kept.append(raw)
            continue
        key, sep, value = line.partition("=")
        if not sep:
            kept.append(raw)
            continue
        name = key.strip().lower()
        val = value.strip()
        if section == "interface" and name == "address":
            addresses += [a.strip() for a in val.split(",") if a.strip()]
            continue
        if section == "interface" and name == "mtu":
            mtu = val or None
            continue
        if section == "peer" and name == "allowedips":
            routes += [a.strip() for a in val.split(",") if a.strip()]
            kept.append(raw)  # wg setconf needs AllowedIPs too
            continue
        if name in _WG_QUICK_ONLY_KEYS:
            continue  # drop DNS/Table/hooks — not our job (or needs full root)
        kept.append(raw)
    return "\n".join(kept) + "\n", addresses, mtu, routes


def _wg_teardown_iface() -> None:
    """Remove the WireGuard link (idempotent; device routes go with it)."""
    try:
        _run([_IP_BIN, "link", "del", "dev", _WG_IFACE], timeout=10)
    except (subprocess.SubprocessError, OSError):  # pragma: no cover
        pass


def _wg_up(config: str) -> Result:
    wg_conf, addresses, mtu, routes = parse_wg_config(config)
    conf = _wg_conf()
    _write_private(conf, wg_conf)
    _wg_teardown_iface()  # clear any stale interface from a previous run
    try:
        res = _run([_IP_BIN, "link", "add", "dev", _WG_IFACE, "type", "wireguard"])
        if res.returncode != 0:
            return Result(False, _stderr_tail(res) or "WireGuard-Interface anlegen fehlgeschlagen")
        res = _run(["wg", "setconf", _WG_IFACE, str(conf)])
        if res.returncode != 0:
            _wg_teardown_iface()
            return Result(False, _stderr_tail(res) or "wg setconf fehlgeschlagen")
        for addr in addresses:
            family = "-6" if ":" in addr else "-4"
            res = _run([_IP_BIN, family, "address", "add", addr, "dev", _WG_IFACE])
            if res.returncode != 0:
                _wg_teardown_iface()
                return Result(False, _stderr_tail(res) or f"Adresse {addr} setzen fehlgeschlagen")
        if mtu:
            _run([_IP_BIN, "link", "set", "mtu", mtu, "dev", _WG_IFACE])
        res = _run([_IP_BIN, "link", "set", "up", "dev", _WG_IFACE])
        if res.returncode != 0:
            _wg_teardown_iface()
            return Result(False, _stderr_tail(res) or "Interface aktivieren fehlgeschlagen")
        _wg_add_routes(routes)
    except (subprocess.SubprocessError, OSError) as exc:  # pragma: no cover
        _wg_teardown_iface()
        return Result(False, str(exc))
    return Result(True, "WireGuard-Tunnel aktiv")


def _wg_add_routes(routes: list[str]) -> None:
    """Route each AllowedIPs subnet through the tunnel device.

    Catch-all defaults (``0.0.0.0/0`` / ``::/0``) are skipped: full-tunnel needs
    the fwmark policy-routing wg-quick sets up as real root, which isn't available
    under CAP_NET_ADMIN alone. The documented use case is reaching an internal
    subnet by IP (split tunnel), which a plain device route covers — see
    docs/VPN.md.
    """
    for cidr in routes:
        if cidr in ("0.0.0.0/0", "::/0"):
            logger.warning(
                "Ignoriere Standard-Route AllowedIPs %s — nur Split-Tunnel wird "
                "unterstützt (siehe docs/VPN.md).",
                cidr,
            )
            continue
        family = "-6" if ":" in cidr else "-4"
        res = _run([_IP_BIN, family, "route", "add", cidr, "dev", _WG_IFACE])
        if res.returncode != 0:  # non-fatal: log and keep the tunnel up
            logger.warning("Route %s konnte nicht gesetzt werden: %s", cidr, _stderr_tail(res))


def _wg_down() -> Result:
    _wg_teardown_iface()
    _wg_conf().unlink(missing_ok=True)
    return Result(True, "")


def _wg_live() -> bool:
    if shutil.which("wg") is None:
        return False
    try:
        result = _run(["wg", "show", _WG_IFACE], timeout=10)
    except (subprocess.SubprocessError, OSError):  # pragma: no cover
        return False
    return result.returncode == 0


def _wg_status() -> TunnelStatus:
    st = TunnelStatus(state="up")
    try:
        dump = _run(["wg", "show", _WG_IFACE, "dump"], timeout=10)
    except (subprocess.SubprocessError, OSError):  # pragma: no cover
        return st
    lines = [ln for ln in dump.stdout.splitlines() if ln.strip()]
    # First line = interface, subsequent lines = peers. Peer dump columns:
    # pubkey, psk, endpoint, allowed-ips, latest-handshake, rx, tx, keepalive
    if len(lines) >= 2:
        cols = lines[1].split("\t")
        if len(cols) >= 5:
            st.endpoint = cols[2] if cols[2] != "(none)" else ""
            handshake = cols[4]
            if handshake and handshake != "0":
                st.last_handshake = handshake
    return st


# --- OpenVPN --------------------------------------------------------------


def _ovpn_alive() -> bool:
    pid_file = _ovpn_pid()
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _ovpn_up(config: str, username: str, password: str) -> Result:
    conf = _ovpn_conf()
    _write_private(conf, config)
    cmd = [
        "openvpn",
        "--config", str(conf),
        "--daemon", "ogc-openvpn",
        "--writepid", str(_ovpn_pid()),
        "--log", str(_ovpn_log()),
        "--connect-timeout", "15",
        "--connect-retry-max", "1",
    ]
    if username:
        _write_private(_ovpn_auth(), f"{username}\n{password}\n")
        cmd += ["--auth-user-pass", str(_ovpn_auth())]
    try:
        result = _run(cmd, timeout=20)
    except (subprocess.SubprocessError, OSError) as exc:  # pragma: no cover
        return Result(False, str(exc))
    if result.returncode != 0:
        return Result(False, _stderr_tail(result) or "openvpn-Start fehlgeschlagen")
    # --daemon forks immediately; wait briefly for the init sequence in the log.
    return Result(True, "OpenVPN-Tunnel gestartet")


def _ovpn_down() -> Result:
    pid_file = _ovpn_pid()
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
        except (OSError, ValueError):
            pass
        pid_file.unlink(missing_ok=True)
    _ovpn_auth().unlink(missing_ok=True)
    return Result(True, "")


def _ovpn_status() -> TunnelStatus:
    st = TunnelStatus(state="up")
    log = _ovpn_log()
    if log.exists():
        try:
            tail = log.read_text().splitlines()[-40:]
        except OSError:  # pragma: no cover
            tail = []
        if any("Initialization Sequence Completed" in ln for ln in tail):
            st.detail = "Verbunden"
        else:
            errs = [ln for ln in tail if "ERROR" in ln or "error" in ln]
            st.detail = errs[-1] if errs else "Verbindungsaufbau…"
    return st


# --- Public orchestration -------------------------------------------------


def _clear_marker() -> None:
    _marker().unlink(missing_ok=True)


def _read_marker() -> dict:
    try:
        return json.loads(_marker().read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def disconnect_all() -> None:
    """Tear down whichever tunnel might be up. Safe to call unconditionally."""
    if _wg_live() or _wg_conf().exists():
        _wg_down()
    if _ovpn_alive() or _ovpn_pid().exists():
        _ovpn_down()
    _clear_marker()


def connect(tunnel_id: int, vpn_type: str, config: str, username: str, password: str) -> Result:
    caps = capabilities()
    if not caps.supports(vpn_type):
        return Result(False, caps.blocker(vpn_type) or "VPN wird nicht unterstützt")
    disconnect_all()
    if vpn_type == "wireguard":
        res = _wg_up(config)
    elif vpn_type == "openvpn":
        res = _ovpn_up(config, username, password)
    else:
        return Result(False, f"Unbekannter VPN-Typ '{vpn_type}'")
    if res.ok:
        _write_private(_marker(), json.dumps({"id": tunnel_id, "type": vpn_type}))
    return res


def disconnect() -> Result:
    disconnect_all()
    return Result(True, "Getrennt")


def active_id() -> int | None:
    """The id of the currently-connected tunnel, reconciled with the system."""
    marker = _read_marker()
    if not marker:
        return None
    vpn_type = marker.get("type")
    live = _wg_live() if vpn_type == "wireguard" else _ovpn_alive()
    if not live:
        _clear_marker()
        return None
    return marker.get("id")


def status() -> TunnelStatus:
    """Live status of the active tunnel (if any)."""
    marker = _read_marker()
    vpn_type = marker.get("type")
    if vpn_type == "wireguard" and _wg_live():
        st = _wg_status()
        st.active_id = marker.get("id")
        return st
    if vpn_type == "openvpn" and _ovpn_alive():
        st = _ovpn_status()
        st.active_id = marker.get("id")
        return st
    if marker:
        _clear_marker()
    return TunnelStatus(state="down")
