#!/usr/bin/env python3
"""OffgridCloud local console — the on-box "OffgridCloud OS" menu.

Runs full-screen on the primary console (tty1) via ``offgrid-kiosk.service``.
When a screen is attached to the box, this is *all* that is visible: a branded
menu with live status and appliance actions (restart the service, reboot, shut
down, open the web UI). An "Einstellungen (Admin)" area configures the box fully
on-box — system settings, notifications, cloud targets, VPN and network — by
logging into the SAME local REST API the web UI uses (no duplicated logic).
Dropping into the underlying Raspberry Pi OS shell is deliberately gated behind
an admin PIN, so an attached keyboard alone does not hand out a root prompt.

The program is pure Python 3 standard library (``curses``) — no third-party
dependencies, so it runs on a stock Raspberry Pi OS without touching the app's
virtualenv. It is intentionally frugal: it shells out to ``systemctl`` /
``hostname`` / ``ip`` and reads the local ``/api/health`` endpoint for status.

Command-line modes (used by the installer / helpers, not day-to-day):

    offgrid-console.py                run the full-screen menu (default)
    offgrid-console.py --hash-pin PIN print a PIN hash line (installer uses this)
    offgrid-console.py --set-pin      prompt for a new PIN and store it (root)

The service runs as root so that the PIN-gated "drop to shell" can spawn a login
shell and the power actions can call ``systemctl`` directly — no extra sudoers
rules beyond what the main installer already sets up.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# --- Locations --------------------------------------------------------------
# Installed layout: <PREFIX>/deploy/kiosk/offgrid-console.py, so PREFIX is three
# parents up. OGC_PREFIX (set by the systemd unit) wins when present.
_DEFAULT_PREFIX = Path(__file__).resolve().parents[2]
PREFIX = Path(os.environ.get("OGC_PREFIX", str(_DEFAULT_PREFIX)))
ENV_FILE = PREFIX / ".env"
PIN_FILE = Path(os.environ.get("OGC_KIOSK_PIN_FILE", str(PREFIX / "data" / "kiosk.pin")))

SERVICE_NAME = "offgridcloud"
NETWATCH_SERVICE = "offgridcloud-netwatch"
MDNS_ALIAS_SERVICE = "offgridcloud-mdns-alias"  # publishes offgridcloud.local
CHROMIUM_LAUNCHER = Path(__file__).resolve().parent / "chromium-kiosk.sh"

# PIN hashing — PBKDF2-HMAC-SHA256, all stdlib. Stored as one line:
#   pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
_PBKDF2_ITERATIONS = 200_000


# --- PIN storage ------------------------------------------------------------
def hash_pin(pin: str, iterations: int = _PBKDF2_ITERATIONS) -> str:
    """Return a self-describing PBKDF2 hash line for ``pin``."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_pin(pin: str, stored: str) -> bool:
    """Constant-time check of ``pin`` against a stored hash line."""
    try:
        algo, iter_s, salt_hex, hash_hex = stored.strip().split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", pin.encode(), bytes.fromhex(salt_hex), int(iter_s)
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def load_pin_hash() -> str | None:
    try:
        text = PIN_FILE.read_text(encoding="utf-8").strip()
        return text or None
    except OSError:
        return None


# --- Environment / status helpers ------------------------------------------
def read_env() -> dict[str, str]:
    """Parse the app's ``.env`` (KEY=VALUE lines) into a dict. Best-effort."""
    env: dict[str, str] = {}
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    except OSError:
        pass
    return env


def _run(cmd: list[str], timeout: float = 5.0) -> str:
    """Run a command, returning trimmed stdout (empty string on any failure)."""
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def service_state(name: str) -> str:
    state = _run(["systemctl", "is-active", name]) or "unknown"
    return state


def service_since(name: str) -> str:
    """Monotonic timestamp of the unit's last start — changes on every restart."""
    return _run(["systemctl", "show", "-p", "ActiveEnterTimestampMonotonic", "--value", name])


def ip_addresses() -> list[str]:
    out = _run(["hostname", "-I"])
    return [ip for ip in out.split() if ":" not in ip] or ["—"]


def vpn_interface() -> str | None:
    """Return the first WireGuard/OpenVPN-looking interface that is up, if any."""
    out = _run(["ip", "-o", "link", "show", "up"])
    for line in out.splitlines():
        # "3: wg0: <...>" — the name is the second colon-separated field.
        parts = line.split(":")
        if len(parts) < 2:
            continue
        name = parts[1].strip().split("@")[0]
        if name.startswith(("wg", "tun", "tap")):
            return name
    return None


def health(port: str) -> dict | None:
    try:
        with urllib.request.urlopen(  # noqa: S310 - localhost only
            f"http://127.0.0.1:{port}/api/health", timeout=2.5
        ) as resp:
            return json.load(resp)
    except (OSError, ValueError):
        return None


def disk_line(buffer_dir: str) -> str:
    target = buffer_dir or str(PREFIX / "data")
    path = target if os.path.isdir(target) else str(PREFIX)
    try:
        usage = shutil.disk_usage(path)
        used = usage.total - usage.free
        pct = (used / usage.total * 100) if usage.total else 0
        return f"{_h(used)} / {_h(usage.total)} belegt ({pct:.0f} %), {_h(usage.free)} frei"
    except OSError:
        return "unbekannt"


def _h(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def gather_status() -> dict[str, str]:
    """Collect everything the status panel shows. All local, no auth needed."""
    env = read_env()
    port = env.get("OGC_PORT", "8000")
    ips = ip_addresses()
    hz = health(port)
    svc = service_state(SERVICE_NAME)
    vpn = vpn_interface()
    return {
        "host": socket.gethostname(),
        "ips": ", ".join(ips),
        "url": f"http://{ips[0]}:{port}" if ips and ips[0] != "—" else f"Port {port}",
        "alias": ("https://offgridcloud.local"
                  if service_state(MDNS_ALIAS_SERVICE) == "active" else "—"),
        "admin_email": env.get("OGC_INITIAL_ADMIN_EMAIL", "admin@offgrid.local"),
        "service": svc,
        "version": (hz or {}).get("version", "?"),
        "rclone": "ja" if (hz or {}).get("rclone", {}).get("available") else "nein",
        "reachable": "ja" if hz else "nein",
        "disk": disk_line(env.get("OGC_BUFFER_DIR", "")),
        "vpn": f"aktiv ({vpn})" if vpn else "aus",
        "apfallback": service_state(NETWATCH_SERVICE),
    }


# --- Local API client -------------------------------------------------------
# The settings screens don't reimplement the web UI's logic — they drive the
# SAME local REST API (validation, encryption, apply-commands all live there).
# The only cost is an admin login, exactly like the web UI. Pure urllib, so the
# console stays dependency-free.
def api_base() -> str:
    return f"http://127.0.0.1:{read_env().get('OGC_PORT', '8000')}"


class ApiError(Exception):
    def __init__(self, code: int, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class Api:
    def __init__(self, base: str):
        self.base = base
        self.token: str | None = None

    @property
    def authed(self) -> bool:
        return self.token is not None

    def _request(self, method: str, path: str, data=None, auth: bool = True):
        headers = {"Accept": "application/json"}
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - localhost
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and auth:
                self.token = None  # expired / restarted with a new key: log in again
            detail = str(exc.reason)
            try:
                detail = json.loads(exc.read()).get("detail", detail)
            except (ValueError, OSError):
                pass
            raise ApiError(exc.code, str(detail)) from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ApiError(0, str(exc)) from None

    def login(self, email: str, password: str) -> None:
        resp = self._request(
            "POST", "/api/auth/login", {"email": email, "password": password}, auth=False
        )
        self.token = resp.get("access_token")
        if not self.token:
            raise ApiError(0, "Kein Token erhalten.")

    def get_system(self) -> dict:
        return self._request("GET", "/api/system")

    def patch_system(self, patch: dict) -> dict:
        return self._request("PUT", "/api/system", patch)

    # Providers (cloud targets)
    def get_providers(self) -> list:
        return self._request("GET", "/api/providers")

    def get_provider_types(self) -> list:
        return self._request("GET", "/api/providers/types")

    def create_provider(self, payload: dict) -> dict:
        return self._request("POST", "/api/providers", payload)

    def test_provider(self, provider_id: int) -> dict:
        return self._request("POST", f"/api/providers/{provider_id}/test")

    def delete_provider(self, provider_id: int) -> dict:
        return self._request("DELETE", f"/api/providers/{provider_id}")

    # VPN
    def get_vpn_tunnels(self) -> list:
        return self._request("GET", "/api/vpn")

    def get_vpn_status(self) -> dict:
        return self._request("GET", "/api/vpn/status")

    def create_vpn(self, payload: dict) -> dict:
        return self._request("POST", "/api/vpn", payload)

    def vpn_connect(self, tunnel_id: int) -> dict:
        return self._request("POST", f"/api/vpn/{tunnel_id}/connect")

    def vpn_disconnect(self, tunnel_id: int) -> dict:
        return self._request("POST", f"/api/vpn/{tunnel_id}/disconnect")

    def delete_vpn(self, tunnel_id: int) -> dict:
        return self._request("DELETE", f"/api/vpn/{tunnel_id}")

    # Network (Wi-Fi / fallback AP)
    def get_network(self) -> dict:
        return self._request("GET", "/api/network")

    def put_network_settings(self, patch: dict) -> dict:
        return self._request("PUT", "/api/network/settings", patch)

    def add_known_network(self, payload: dict) -> dict:
        return self._request("POST", "/api/network/known", payload)

    def delete_known_network(self, network_id: int) -> dict:
        return self._request("DELETE", f"/api/network/known/{network_id}")

    def network_apply(self) -> dict:
        return self._request("POST", "/api/network/apply")

    def network_scan(self) -> dict:
        return self._request("POST", "/api/network/scan")

    # Bandwidth
    def get_bandwidth(self) -> dict:
        return self._request("GET", "/api/bandwidth")

    def put_bandwidth(self, patch: dict) -> dict:
        return self._request("PUT", "/api/bandwidth", patch)

    # Pool (multi-server)
    def get_pool_overview(self) -> dict:
        return self._request("GET", "/api/pool/overview")

    def get_pool_tokens(self) -> list:
        return self._request("GET", "/api/pool/tokens")

    def create_pool_token(self, name: str, scope: str) -> dict:
        return self._request("POST", "/api/pool/tokens", {"name": name, "scope": scope})

    def set_pool_token_scope(self, token_id: int, scope: str) -> dict:
        return self._request("PATCH", f"/api/pool/tokens/{token_id}", {"scope": scope})

    def delete_pool_token(self, token_id: int) -> dict:
        return self._request("DELETE", f"/api/pool/tokens/{token_id}")

    def open_pool_pairing(self) -> dict:
        return self._request("POST", "/api/pool/pairing")

    def get_pool_peers(self) -> list:
        return self._request("GET", "/api/pool/peers")

    def add_pool_peer(self, payload: dict) -> dict:
        return self._request("POST", "/api/pool/peers", payload)

    def delete_pool_peer(self, peer_id: int) -> dict:
        return self._request("DELETE", f"/api/pool/peers/{peer_id}")

    # Users
    def get_users(self) -> list:
        return self._request("GET", "/api/users")

    def create_user(self, payload: dict) -> dict:
        return self._request("POST", "/api/users", payload)

    def update_user(self, user_id: int, patch: dict) -> dict:
        return self._request("PATCH", f"/api/users/{user_id}", patch)

    def delete_user(self, user_id: int) -> dict:
        return self._request("DELETE", f"/api/users/{user_id}")

    # Groups
    def get_groups(self) -> list:
        return self._request("GET", "/api/groups")

    def create_group(self, payload: dict) -> dict:
        return self._request("POST", "/api/groups", payload)

    def delete_group(self, group_id: int) -> dict:
        return self._request("DELETE", f"/api/groups/{group_id}")

    def set_group_members(self, group_id: int, user_ids: list) -> dict:
        return self._request("PUT", f"/api/groups/{group_id}/members", {"user_ids": user_ids})

    def power(self, action: str) -> dict:
        return self._request("POST", f"/api/system/power/{action}")

    # Updates
    def get_update_info(self, force: bool = False) -> dict:
        return self._request("GET", "/api/updates" + ("?force=true" if force else ""))

    def apply_update(self) -> dict:
        return self._request("POST", "/api/updates/apply", {})

    def get_update_progress(self) -> dict:
        return self._request("GET", "/api/updates/progress")

    def dismiss_update(self) -> dict:
        return self._request("POST", "/api/updates/dismiss")

    # Storage media (cards, USB drives, cache drives)
    def get_storage(self) -> dict:
        return self._request("GET", "/api/storage")

    def storage_action(self, action: str, device: str) -> dict:
        """mount | unmount | eject — all take just the device path."""
        return self._request("POST", f"/api/storage/{action}", {"device": device})

    def storage_format(self, device: str, fstype: str, label: str, confirm: str) -> dict:
        return self._request("POST", "/api/storage/format",
                             {"device": device, "fstype": fstype, "label": label,
                              "confirm": confirm})

    def storage_browse(self, device: str, path: str = "") -> dict:
        query = urllib.parse.urlencode({"device": device, "path": path})
        return self._request("GET", f"/api/storage/browse?{query}")

    def get_imports(self) -> list:
        return self._request("GET", "/api/storage/imports")

    def start_import(self, payload: dict) -> dict:
        return self._request("POST", "/api/storage/imports", payload)

    def cancel_import(self, job_id: str) -> dict:
        return self._request("POST", f"/api/storage/imports/{urllib.parse.quote(job_id)}/cancel")

    def clear_imports(self) -> dict:
        return self._request("DELETE", "/api/storage/imports")

    def get_caches(self) -> list:
        return self._request("GET", "/api/storage/caches")

    def mark_cache(self, device: str) -> dict:
        return self._request("POST", "/api/storage/caches", {"device": device})

    def unmark_cache(self, uuid: str) -> dict:
        return self._request("DELETE", f"/api/storage/caches/{urllib.parse.quote(uuid)}")

    def cache_backup(self, payload: dict) -> dict:
        return self._request("POST", "/api/storage/caches/backup", payload)

    def delete_snapshot(self, uuid: str, name: str) -> dict:
        return self._request("DELETE", f"/api/storage/caches/{urllib.parse.quote(uuid)}"
                                       f"/snapshots/{urllib.parse.quote(name)}")

    # Upload folders (import target)
    def get_folders(self) -> list:
        return self._request("GET", "/api/folders")


# System settings, grouped. ("group", title) is a non-selectable header; every
# other row is (key, label, kind). kind: bool | text | int | secret:<status_key>
# (secrets are write-only — the API returns only a *_configured flag).
SYSTEM_FIELDS = [
    ("group", "Uploads & Sync"),
    ("delete_local_after_upload", "Lokale Kopie nach Upload löschen", "bool"),
    ("delete_remote_on_local_delete", "Beim lokalen Löschen auch Cloud löschen", "bool"),
    ("auto_resync", "Automatischer Resync (Selbstheilung)", "bool"),
    ("group", "Bandbreite"),
    ("probe_url", "Mess-URL (Bandbreiten-Probe)", "text"),
    ("group", "Benachrichtigungen — Kanäle"),
    ("webhook_url", "Webhook-URL", "text"),
    ("telegram_chat_id", "Telegram Chat-ID", "text"),
    ("telegram_bot_token", "Telegram Bot-Token", "secret:telegram_configured"),
    ("smtp_host", "SMTP Host", "text"),
    ("smtp_port", "SMTP Port", "int"),
    ("smtp_username", "SMTP Benutzer", "text"),
    ("smtp_password", "SMTP Passwort", "secret:smtp_configured"),
    ("smtp_from", "Absender (From)", "text"),
    ("smtp_to", "Empfänger (To)", "text"),
    ("smtp_tls", "SMTP TLS", "bool"),
    ("group", "Benachrichtigungen — Ereignisse"),
    ("notify_on_received", "Bei Empfang (Upload angekommen)", "bool"),
    ("notify_on_done", "Bei Transfer fertig", "bool"),
    ("notify_on_failed", "Bei Transfer fehlgeschlagen", "bool"),
    ("notify_on_low_space", "Bei wenig Speicher", "bool"),
    ("notify_on_startup", "Beim Start", "bool"),
    ("notify_on_reconnect", "Bei Wieder-Online", "bool"),
    ("notify_on_bandwidth", "Bei Bandbreiten-Drosselung", "bool"),
]

# USB-tethered phone as reported in the network status ("tether").
TETHER_NAMES = {"iphone": "iPhone", "android": "Android"}

# Fallback-AP / network-redundancy settings (PUT /api/network/settings).
NETWORK_AP_FIELDS = [
    ("group", "Fallback-Access-Point"),
    ("fallback_enabled", "Rückfall-WLAN aktiv", "bool"),
    ("ap_ssid", "AP-Name (SSID)", "text"),
    ("ap_password", "AP-Passwort", "secret:ap_has_password"),
    ("ap_hidden", "SSID verstecken", "bool"),
    ("ap_address", "AP-IP-Adresse", "text"),
    ("country_code", "Ländercode (z. B. DE)", "text"),
    ("group", "Watchdog"),
    ("check_interval", "Prüf-Intervall (Sekunden)", "int"),
    ("fail_threshold", "Fehlversuche bis AP", "int"),
]

# Bandwidth policy (PUT /api/bandwidth). The time-window schedule stays web-only.
BANDWIDTH_FIELDS = [
    ("group", "Bandbreiten-Steuerung"),
    ("enabled", "Steuerung aktiv", "bool"),
    ("bwlimit_kbps", "Upload-Limit (KiB/s, 0 = unbegrenzt)", "int"),
    ("min_bandwidth_kbps", "Mindest-Bandbreite-Gate (KiB/s)", "int"),
    ("group", "Getaktete Verbindung (USB-Tethering)"),
    ("metered_mode", "Uploads: allow / pause / limit", "text"),
    ("metered_budget_mb", "Tagesvolumen bei limit (MB)", "int"),
    ("group", "Mehrere Leitungen & parallele Uploads"),
    ("bonding_enabled", "Über alle Leitungen bündeln (net-merge)", "bool"),
    ("parallel_uploads", "Parallele Uploads (1-4, Pi 3: max. 2)", "int"),
]

# Top-level tabs: (key, label, short label). The dashboard is the landing view
# (also shown on SSH login). The short labels kick in when the full tab bar would
# not fit, e.g. on an 80-column console.
TABS = [
    ("dashboard", "Dashboard", "Status"),
    ("providers", "Provider", "Cloud"),
    ("storage", "Speicher", "Speicher"),
    ("network", "Netz & Verbindung", "Netz"),
    ("pool", "Pool", "Pool"),
    ("users", "Benutzer & Gruppen", "Benutzer"),
    ("system", "System", "System"),
]

# Power actions: key -> (API slug, local command, confirmation question).
# Restart uses --no-block so the console never hangs while the service stops;
# the wait screen then watches it come back.
POWER_ACTIONS = {
    "restart": ("restart-service", ["systemctl", "--no-block", "restart", SERVICE_NAME],
                "OffgridCloud-Dienst neu starten?"),
    "reboot": ("reboot", ["systemctl", "reboot"], "Box wirklich NEU STARTEN?"),
    "shutdown": ("shutdown", ["systemctl", "poweroff"], "Box wirklich HERUNTERFAHREN?"),
}

JOB_STATUS = {"pending": "wartet", "running": "läuft", "done": "fertig",
              "failed": "fehlgeschlagen", "cancelled": "abgebrochen"}
UPDATE_PHASE = {"running": "läuft", "success": "erfolgreich", "failed": "fehlgeschlagen",
                "unknown": "unbekannt", "idle": "—"}


# --- Curses UI --------------------------------------------------------------
# "OffgridCloud" in the figlet "cybermedium" font — compact (3 rows) so it fits
# even an 80x24 console, and reads clearly on the framebuffer console font.
LOGO = [
    "____ ____ ____ ____ ____ _ ___  ____ _    ____ _  _ ___",
    "|  | |___ |___ | __ |__/ | |  \\ |    |    |  | |  | |  \\",
    "|__| |    |    |__] |  \\ | |__/ |___ |___ |__| |__| |__/",
]

class Console:
    def __init__(self, stdscr):
        self.scr = stdscr
        self.tab = 0
        self.sel = 0
        self.running = True
        # Kiosk mode (tty1, set by the systemd unit) locks the box in: no
        # quit-to-shell except the PIN gate. Any other launch (SSH / `sudo
        # offgrid-console`) is already an authenticated session, so it may quit.
        self.kiosk = os.environ.get("OGC_KIOSK") == "1"
        self.status = gather_status()
        self.message = ("←→ Tab · ↑↓ Auswahl · Enter · q beenden" if not self.kiosk
                        else "←→ Tab wechseln · ↑↓ Auswahl · Enter: öffnen")
        self.api = Api(api_base())
        self.admin_email = read_env().get("OGC_INITIAL_ADMIN_EMAIL", "admin@offgrid.local")
        self._init_colors()

    def _init_colors(self):
        self.color = lambda *_: 0
        try:
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_CYAN, -1)
                curses.init_pair(2, curses.COLOR_GREEN, -1)
                curses.init_pair(3, curses.COLOR_RED, -1)
                curses.init_pair(4, curses.COLOR_YELLOW, -1)
                self.color = curses.color_pair
        except curses.error:
            pass

    # -- drawing (tabbed dashboard) --
    def draw(self):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        self._draw_tabbar(0, w)
        if TABS[self.tab][0] == "dashboard":
            self._draw_dashboard(3, w)
        else:
            self._draw_tabpage(3, w, TABS[self.tab][1], self._tab_actions())
        self._addstr(h - 1, 1, self.message[: w - 2], self.color(4))
        self.scr.noutrefresh()
        curses.doupdate()

    def _draw_tabbar(self, y, w):
        x = 1
        full = sum(len(f" {i + 1} {t[1]} ") + 1 for i, t in enumerate(TABS))
        col = 1 if full <= w - 2 else 2
        for i, tab in enumerate(TABS):
            seg = f" {i + 1} {tab[col]} "
            active = i == self.tab
            self._addstr(y, x, seg,
                         (curses.A_REVERSE | curses.A_BOLD) if active else self.color(1))
            x += len(seg)
            if i < len(TABS) - 1:
                self._addstr(y, x, "│", curses.A_DIM)
                x += 1
        self._addstr(y + 1, 1, "─" * (w - 2), curses.A_DIM)

    def _center(self, y, text, attr=0):
        _, w = self.scr.getmaxyx()
        x = max(0, (w - len(text)) // 2)
        self._addstr(y, x, text, attr)

    def _addstr(self, y, x, text, attr=0):
        h, w = self.scr.getmaxyx()
        if 0 <= y < h:
            try:
                self.scr.addnstr(y, x, text, max(0, w - x - 1), attr)
            except curses.error:
                pass

    def _draw_dashboard(self, y, w):
        for line in LOGO:
            self._center(y, line, curses.A_BOLD | self.color(1))
            y += 1
        self._center(y + 1, "OS · Lokale Konsole", self.color(4))
        y = self._draw_status(y + 3, w)
        self._addstr(y + 1, max(2, (w - 64) // 2),
                     f"Tabs: ←/→ oder 1–{len(TABS)} · Details öffnen: Tab wählen, ↑↓/Enter",
                     curses.A_DIM)

    def _draw_status(self, y, w):
        s = self.status
        svc_ok = s["service"] == "active"
        rows = [
            ("Gerät", s["host"], 0),
            ("IP-Adresse", s["ips"], 0),
            ("Web-Oberfläche", s["url"], self.color(1)),
            ("Kurzadresse", s["alias"], self.color(1) if s["alias"] != "—" else 0),
            ("Dienst", s["service"], self.color(2 if svc_ok else 3)),
            ("Erreichbar", s["reachable"], self.color(2 if s["reachable"] == "ja" else 3)),
            ("Version", s["version"], 0),
            ("Cloud-Engine (rclone)", s["rclone"], 0),
            ("Speicher (Puffer)", s["disk"], 0),
            ("VPN", s["vpn"], 0),
            ("WLAN-Rückfallebene", s["apfallback"], 0),
        ]
        label_w = max(len(r[0]) for r in rows)
        x = max(2, (w - 64) // 2)
        for label, value, attr in rows:
            self._addstr(y, x, f"{label:<{label_w}} : ", curses.A_DIM)
            self._addstr(y, x + label_w + 3, str(value), attr)
            y += 1
        return y

    def _draw_tabpage(self, y, w, title, actions):
        self._center(y, title, curses.A_BOLD | self.color(1))
        x = max(2, (w - 56) // 2)
        yy = y + 2
        if not self.api.authed:
            self._addstr(yy, x, "Admin-Login nötig — Enter auf einer Aktion.", curses.A_DIM)
            yy += 2
        for i, (_, label) in enumerate(actions):
            sel = i == self.sel
            self._addstr(yy, x, f"{' ▶ ' if sel else '   '}{label}",
                         (curses.A_REVERSE | curses.A_BOLD) if sel else 0)
            yy += 1

    # -- top-level navigation --
    def _tab_actions(self):
        key = TABS[self.tab][0]
        if key == "providers":
            return [("providers", "Cloud-Ziele verwalten")]
        if key == "storage":
            return [("drives", "Laufwerke & Speicherkarten"),
                    ("jobs", "Vorgänge (Upload & Sicherung)"),
                    ("caches", "Kartensicherungen (Cache-Laufwerke)"),
                    ("buffer", "Puffer-Belegung")]
        if key == "network":
            return [("wifi", "WLAN & Fallback-AP"), ("bandwidth", "Bandbreite"), ("vpn", "VPN")]
        if key == "pool":
            return [("pool_ov", "Pool-Übersicht"),
                    ("pool_pair", "Kopplungscode für einen Hub anzeigen"),
                    ("pool_token", "Zugriff für Hubs (Tokens)"),
                    ("pool_peers", "Peers (andere Boxen) verwalten")]
        if key == "users":
            return [("users", "Benutzer"), ("groups", "Gruppen")]
        if key == "system":
            acts = [("updates", "Version & Updates"),
                    ("syscfg", "Systemeinstellungen & Benachrichtigungen"),
                    ("restart", "OffgridCloud-Dienst neu starten"),
                    ("reboot", "Box neu starten"),
                    ("shutdown", "Box herunterfahren")]
            if CHROMIUM_LAUNCHER.exists() and _has_chromium():
                acts.append(("browser", "Web-Oberfläche im Browser"))
            acts.append(("shell", "Zur Raspberry-Pi-Shell (PIN)") if self.kiosk
                        else ("quit", "Beenden (zur Shell)"))
            return acts
        return []

    def run(self):
        self.scr.timeout(5000)  # auto-refresh status every 5s
        while self.running:
            self.draw()
            try:
                ch = self.scr.getch()
            except KeyboardInterrupt:
                continue  # Ctrl-C must not drop out of the kiosk
            if ch == -1:
                self.status = gather_status()
                continue
            actions = self._tab_actions()
            if actions and self.sel >= len(actions):
                self.sel = len(actions) - 1
            if ch in (curses.KEY_LEFT, ord("h")):
                self.tab = (self.tab - 1) % len(TABS)
                self.sel = 0
            elif ch in (curses.KEY_RIGHT, ord("l")):
                self.tab = (self.tab + 1) % len(TABS)
                self.sel = 0
            elif ord("1") <= ch <= ord("0") + len(TABS):
                self.tab = ch - ord("1")
                self.sel = 0
            elif ch in (curses.KEY_UP, ord("k")):
                if actions:
                    self.sel = (self.sel - 1) % len(actions)
            elif ch in (curses.KEY_DOWN, ord("j")):
                if actions:
                    self.sel = (self.sel + 1) % len(actions)
            elif ch in (curses.KEY_ENTER, 10, 13):
                if actions:
                    self._run_tab_action(actions[self.sel][0])
            elif ch in (ord("q"), ord("Q")):
                if self.kiosk:
                    self.message = "Kiosk-Modus: Shell nur über System → PIN."
                else:
                    self.running = False

    def _run_tab_action(self, key):
        no_auth = {"restart", "reboot", "shutdown", "shell", "browser", "quit"}
        if key not in no_auth and not self._ensure_login():
            return
        if key in POWER_ACTIONS:
            self._power(key)
        elif key == "quit":
            self.running = False
        elif key == "syscfg":
            self._live_editor("System & Benachrichtigungen", SYSTEM_FIELDS,
                              self.api.get_system, self.api.patch_system)
        else:
            handler = {
                "providers": self._providers_area,
                "wifi": self._network_area,
                "bandwidth": self._bandwidth_editor,
                "vpn": self._vpn_area,
                "pool_ov": self._pool_overview,
                "pool_pair": self._pool_pair,
                "pool_token": self._pool_token,
                "pool_peers": self._pool_peers,
                "users": self._users_area,
                "groups": self._groups_area,
                "updates": self._updates_area,
                "drives": self._drives_area,
                "jobs": self._jobs_view,
                "caches": self._caches_area,
                "buffer": self._storage_view,
                "browser": self._launch_browser,
                "shell": self._drop_to_shell,
            }.get(key)
            if handler:
                handler()

    # -- dialogs --
    def _prompt(self, question, mask=False, default=""):
        """Single-line prompt at the bottom; returns the typed string or None.

        A non-empty ``default`` pre-fills the line so the user can accept it with
        Enter or edit it (secrets are never pre-filled)."""
        h, w = self.scr.getmaxyx()
        self.scr.timeout(-1)
        curses.curs_set(1)
        buf = "" if mask else str(default)
        while True:
            self._addstr(h - 1, 1, " " * (w - 2))
            shown = ("*" * len(buf)) if mask else buf
            self._addstr(h - 1, 1, f"{question} {shown}", self.color(4))
            self.scr.move(h - 1, min(w - 2, 1 + len(question) + 1 + len(shown)))
            self.scr.refresh()
            ch = self.scr.getch()
            if ch in (curses.KEY_ENTER, 10, 13):
                result = buf
                break
            if ch == 27:  # ESC
                result = None
                break
            if ch in (curses.KEY_BACKSPACE, 127, 8):
                buf = buf[:-1]
            elif 32 <= ch < 127:
                buf += chr(ch)
        curses.curs_set(0)
        self.scr.timeout(5000)
        return result

    def _confirm(self, question):
        answer = self._prompt(f"{question} [j/N]")
        return bool(answer) and answer.strip().lower() in ("j", "ja", "y", "yes")

    def _busy(self, text):
        """Show a one-line "working …" note at the bottom of the current screen."""
        h, w = self.scr.getmaxyx()
        self._addstr(h - 2, 1, " " * (w - 2))
        self._addstr(h - 2, 1, text[: w - 2], self.color(4))
        self.scr.refresh()

    @staticmethod
    def _run_local(cmd):
        """Run a privileged command; returns an error text or None on success."""
        try:
            # Capture output so a chatty command can't bleed onto the curses screen.
            proc = subprocess.run(cmd, capture_output=True, text=True,  # noqa: S603
                                  timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            return str(exc)
        if proc.returncode == 0:
            return None
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return f"Code {proc.returncode}: {detail[-1] if detail else 'journalctl -xe'}"

    def _power(self, key):
        """Restart the service / reboot / power off.

        As root (the tty1 kiosk) we call systemctl directly. An SSH session runs
        as a normal user, where a bare systemctl fails — there we try
        ``sudo -n`` (Pi OS users usually have passwordless sudo) and otherwise
        fall back to the API, which runs the same action through the service's
        own sudoers rules (needs the admin login)."""
        api_action, cmd, question = POWER_ACTIONS[key]
        if not self._confirm(question):
            self.message = "Abgebrochen."
            return
        self.message = "Aktion läuft …"
        self.draw()
        since = service_since(SERVICE_NAME)
        if os.geteuid() == 0:
            err = self._run_local(cmd)
        else:
            err = self._run_local(["sudo", "-n", *cmd])
            if err is not None:
                err = self._power_via_api(api_action, err)
                if err == "":  # login cancelled
                    self.message = "Abgebrochen."
                    return
        if err is not None:
            self.message = f"Fehlgeschlagen ({err}). Tipp: sudo offgrid-console"
            return
        if key == "restart":
            self._wait_for_service(since)
        else:
            self.message = ("Box startet neu …" if key == "reboot"
                            else "Box fährt herunter …")

    def _power_via_api(self, action, sudo_err):
        """Power action through the local API; returns an error text, "" if the
        login was cancelled, or None on success."""
        if health(read_env().get("OGC_PORT", "8000")) is None:
            return f"kein root, sudo: {sudo_err}; API nicht erreichbar"
        if not self._ensure_login():
            return ""
        try:
            self.api.power(action)
        except ApiError as exc:
            return exc.detail
        return None

    def _wait_for_service(self, since_before, timeout=180):
        """Live screen while the service restarts; returns when it answers again."""
        port = read_env().get("OGC_PORT", "8000")
        start = time.monotonic()
        saw_down = False
        spinner = "|/-\\"
        self.scr.timeout(1000)
        try:
            while True:
                elapsed = time.monotonic() - start
                state = service_state(SERVICE_NAME)
                since = service_since(SERVICE_NAME)
                hz = health(port)
                # "unknown" = no systemctl to ask; then the API alone decides.
                up = hz is not None and state in ("active", "unknown")
                if not up:
                    saw_down = True
                restarted = saw_down or (since and since_before and since != since_before)
                if restarted and up:
                    self.status = gather_status()
                    self.message = (f"Dienst läuft wieder (Version {hz.get('version', '?')}, "
                                    f"nach {elapsed:.0f} s).")
                    return
                if state == "failed":
                    self.message = ("Dienst startet nicht (failed) — "
                                    "Logs: journalctl -u offgridcloud -n 50")
                    return
                if elapsed > timeout:
                    self.message = (f"Dienst nach {timeout} s noch nicht erreichbar "
                                    f"(Status: {state}).")
                    return
                self.scr.erase()
                h, w = self.scr.getmaxyx()
                self._center(1, "OffgridCloud-Dienst wird neu gestartet",
                             curses.A_BOLD | self.color(1))
                x = max(2, (w - 56) // 2)
                self._addstr(4, x, f"Dienst      : {state}",
                             self.color(2 if state == "active" else 4))
                self._addstr(5, x, f"API         : {'erreichbar' if hz else 'nicht erreichbar'}",
                             self.color(2 if hz else 4))
                self._addstr(6, x, f"Vergangen   : {elapsed:.0f} s  "
                                   f"{spinner[int(elapsed) % len(spinner)]}")
                self._addstr(8, x, "Warte, bis der Dienst wieder antwortet …", curses.A_DIM)
                self._addstr(h - 1, 1, "Esc: nicht mehr warten (Neustart läuft weiter)",
                             curses.A_DIM)
                self.scr.noutrefresh()
                curses.doupdate()
                if self.scr.getch() in (27, ord("q"), ord("Q")):
                    self.message = "Neustart läuft im Hintergrund weiter."
                    return
        finally:
            self.scr.timeout(5000)

    def _launch_browser(self):
        if not (CHROMIUM_LAUNCHER.exists() and _has_chromium()):
            self.message = "Kein Browser-Kiosk installiert (Installer mit --with-chromium)."
            return
        env = read_env()
        url = f"http://127.0.0.1:{env.get('OGC_PORT', '8000')}"
        self.message = "Browser startet … (schließen führt zurück ins Menü)"
        self.draw()
        with _suspended():
            subprocess.run(["/bin/bash", str(CHROMIUM_LAUNCHER), url], check=False)  # noqa: S603
        self.status = gather_status()
        self.message = "Zurück im Menü."

    def _drop_to_shell(self):
        stored = load_pin_hash()
        if not stored:
            self.message = (
                "Keine PIN gesetzt — Shell gesperrt. Auf tty2 (Strg+Alt+F2) anmelden "
                "oder PIN setzen: sudo offgrid-console.py --set-pin"
            )
            return
        pin = self._prompt("Admin-PIN:", mask=True)
        if pin is None:
            self.message = "Abgebrochen."
            return
        if not verify_pin(pin, stored):
            self.message = "Falsche PIN."
            return
        with _suspended():
            os.system("clear")  # noqa: S605 - constant
            print("OffgridCloud — Raspberry Pi OS Shell. 'exit' bringt zum Menü zurück.\n")
            shell = os.environ.get("SHELL") or "/bin/bash"
            subprocess.run([shell, "--login"], check=False)  # noqa: S603
        self.status = gather_status()
        self.message = "Zurück im OffgridCloud-Menü."

    # -- settings (admin-authenticated, drives the local API) --
    def _ensure_login(self) -> bool:
        """Prompt for the admin login (once per session) and get an API token."""
        if self.api.authed:
            return True
        email = self._prompt("Admin-E-Mail:", default=self.admin_email)
        if email is None:
            return False
        password = self._prompt("Passwort:", mask=True)
        if password is None:
            return False
        self.message = "Anmeldung läuft …"
        self.draw()
        try:
            self.api.login(email.strip(), password)
        except ApiError as exc:
            if exc.code in (401, 403):
                self.message = "Anmeldung fehlgeschlagen: falsche Zugangsdaten."
            elif exc.code == 0:
                self.message = f"Keine Verbindung zur API ({self.api.base}). Läuft der Dienst?"
            else:
                self.message = f"Anmeldung fehlgeschlagen: {exc.detail}"
            return False
        self.admin_email = email.strip()
        self.message = f"Angemeldet als {self.admin_email}."
        return True

    def _submenu(self, title, items, selected=0, hint=""):
        """Modal list screen; returns the chosen index, or None on Esc/q."""
        self.scr.timeout(-1)
        try:
            while True:
                self.scr.erase()
                h, w = self.scr.getmaxyx()
                self._center(1, title, curses.A_BOLD | self.color(1))
                x = max(2, (w - 56) // 2)
                y = 4
                for i, (_, label) in enumerate(items):
                    is_sel = i == selected
                    marker = " ▶ " if is_sel else "   "
                    attr = curses.A_REVERSE | curses.A_BOLD if is_sel else 0
                    self._addstr(y, x, f"{marker}{label}", attr)
                    y += 1
                for i, line in enumerate(hint.splitlines() if hint else []):
                    self._addstr(y + 1 + i, x, line, curses.A_DIM)
                if self.message:
                    self._addstr(h - 2, 1, self.message[: w - 2], self.color(4))
                self._addstr(h - 1, 1, "↑↓ · Enter: wählen · Esc: zurück", curses.A_DIM)
                self.scr.noutrefresh()
                curses.doupdate()
                ch = self.scr.getch()
                if ch in (curses.KEY_UP, ord("k")):
                    selected = (selected - 1) % len(items)
                elif ch in (curses.KEY_DOWN, ord("j")):
                    selected = (selected + 1) % len(items)
                elif ch in (curses.KEY_ENTER, 10, 13):
                    return selected
                elif ch in (27, ord("q"), ord("Q")):
                    return None
        finally:
            self.scr.timeout(5000)

    def _fmt_value(self, data, key, kind):
        """Return (text, attr) for a settings value."""
        if kind == "bool":
            return ("● an", self.color(2)) if data.get(key) else ("○ aus", self.color(3))
        if kind.startswith("secret"):
            status_key = kind.split(":", 1)[1]
            return ("gesetzt", self.color(2)) if data.get(status_key) else ("leer", curses.A_DIM)
        val = data.get(key)
        if val in (None, ""):
            return ("—", curses.A_DIM)
        return (str(val), 0)

    def _live_editor(self, title, fields, load_fn, save_fn):
        """Scrollable editor that reads/writes settings live via the API.

        ``fields`` are ("group", title) headers or (key, label, kind) rows;
        ``load_fn()`` returns the current data dict, ``save_fn(patch)`` applies
        one change and returns the updated dict."""
        try:
            data = load_fn()
        except ApiError as exc:
            self.message = f"Konnte Daten nicht laden: {exc.detail}"
            return
        selectable = [i for i, f in enumerate(fields) if f[0] != "group"]
        pos = 0
        top = 0
        self.scr.timeout(-1)
        try:
            while True:
                sel_row = selectable[pos]
                h, w = self.scr.getmaxyx()
                rows = max(3, h - 4)
                if sel_row < top:
                    top = sel_row
                elif sel_row >= top + rows:
                    top = sel_row - rows + 1
                self.scr.erase()
                self._center(0, title, curses.A_BOLD | self.color(1))
                x = max(2, (w - 64) // 2)
                label_w = 38
                y = 2
                for idx in range(top, min(len(fields), top + rows)):
                    if fields[idx][0] == "group":
                        self._addstr(y, x, fields[idx][1].upper(), curses.A_BOLD | self.color(4))
                    else:
                        key, label, kind = fields[idx]
                        txt, attr = self._fmt_value(data, key, kind)
                        is_sel = idx == sel_row
                        self._addstr(y, x, f"{'▶' if is_sel else ' '} {label:<{label_w}}",
                                     curses.A_REVERSE if is_sel else 0)
                        self._addstr(y, x + label_w + 3, txt, attr)
                    y += 1
                if self.message:
                    self._addstr(h - 2, 1, self.message[: w - 2], self.color(4))
                self._addstr(h - 1, 1, "↑↓ · Enter: ändern · Esc: zurück", curses.A_DIM)
                self.scr.noutrefresh()
                curses.doupdate()
                ch = self.scr.getch()
                if ch in (curses.KEY_UP, ord("k")):
                    pos = (pos - 1) % len(selectable)
                elif ch in (curses.KEY_DOWN, ord("j")):
                    pos = (pos + 1) % len(selectable)
                elif ch in (curses.KEY_ENTER, 10, 13):
                    data = self._edit_live_field(data, fields[sel_row], save_fn)
                elif ch in (27, ord("q"), ord("Q")):
                    return
        finally:
            self.scr.timeout(5000)

    def _edit_live_field(self, data, field, save_fn):
        """Edit one field via ``save_fn``; returns the (possibly) new data."""
        key, label, kind = field
        try:
            if kind == "bool":
                patch = {key: not bool(data.get(key))}
            elif kind == "int":
                cur = data.get(key)
                raw = self._prompt(f"{label}:", default="" if cur is None else str(cur))
                if raw is None:
                    return data
                raw = raw.strip()
                if raw == "":
                    self.message = "Leer — unverändert."
                    return data
                try:
                    patch = {key: int(raw)}
                except ValueError:
                    self.message = "Bitte eine Zahl eingeben."
                    return data
            elif kind.startswith("secret"):
                raw = self._prompt(f"{label} (neu eingeben, leer = abbrechen):", mask=True)
                if not raw:
                    self.message = "Unverändert."
                    return data
                patch = {key: raw}
            else:  # text
                raw = self._prompt(f"{label}:", default=str(data.get(key) or ""))
                if raw is None:
                    return data
                patch = {key: raw.strip()}
            updated = save_fn(patch)
            self.message = f"Gespeichert: {label}."
            return updated if isinstance(updated, dict) else data
        except ApiError as exc:
            self.message = f"Speichern fehlgeschlagen: {exc.detail}"
            return data

    # -- generic record form (for creating providers / VPN / Wi-Fi entries) --
    def _fmt_form_value(self, field, val):
        t = field.get("type", "text")
        if t == "bool":
            return ("● an", self.color(2)) if val else ("○ aus", self.color(3))
        if t == "password" or field.get("secret"):
            return ("gesetzt", self.color(2)) if str(val) else ("leer", curses.A_DIM)
        return (str(val), 0) if str(val) else ("—", curses.A_DIM)

    def _collect_form(self, title, fields):
        """Modal create-form. ``fields``: dicts with key/label/type/required/
        options/default. Returns a dict of native values, or None if cancelled."""
        values = {}
        for f in fields:
            d = f.get("default", "")
            values[f["key"]] = (str(d).lower() in ("true", "1", "yes", "on")
                                if f.get("type") == "bool" else d)
        rows = list(fields) + [{"key": "__save__", "label": "✓ Speichern", "type": "__save__"}]
        pos = 0
        top = 0
        note = ""
        self.scr.timeout(-1)
        try:
            while True:
                h, w = self.scr.getmaxyx()
                rows_vis = max(3, h - 4)
                if pos < top:
                    top = pos
                elif pos >= top + rows_vis:
                    top = pos - rows_vis + 1
                self.scr.erase()
                self._center(0, title, curses.A_BOLD | self.color(1))
                x = max(2, (w - 60) // 2)
                label_w = 30
                y = 2
                for idx in range(top, min(len(rows), top + rows_vis)):
                    f = rows[idx]
                    is_sel = idx == pos
                    if f["type"] == "__save__":
                        self._addstr(y, x, f"{'▶' if is_sel else ' '} {f['label']}",
                                     (curses.A_REVERSE | curses.A_BOLD) if is_sel else self.color(2))
                    else:
                        txt, attr = self._fmt_form_value(f, values[f["key"]])
                        req = "*" if f.get("required") else " "
                        self._addstr(y, x, f"{'▶' if is_sel else ' '}{req}{f['label']:<{label_w}}",
                                     curses.A_REVERSE if is_sel else 0)
                        self._addstr(y, x + label_w + 4, txt, attr)
                    y += 1
                if note:
                    self._addstr(h - 2, 1, note[: w - 2], self.color(3))
                self._addstr(h - 1, 1, "↑↓ · Enter: ändern/speichern · Esc: abbrechen · * Pflicht",
                             curses.A_DIM)
                self.scr.noutrefresh()
                curses.doupdate()
                ch = self.scr.getch()
                if ch in (curses.KEY_UP, ord("k")):
                    pos = (pos - 1) % len(rows)
                elif ch in (curses.KEY_DOWN, ord("j")):
                    pos = (pos + 1) % len(rows)
                elif ch == 27:
                    return None
                elif ch in (curses.KEY_ENTER, 10, 13):
                    f = rows[pos]
                    if f["type"] == "__save__":
                        missing = [ff["label"] for ff in fields
                                   if ff.get("required") and not str(values[ff["key"]]).strip()]
                        if missing:
                            note = "Pflichtfelder fehlen: " + ", ".join(missing)
                            continue
                        return values
                    self._edit_form_value(f, values)
        finally:
            self.scr.timeout(5000)

    def _edit_form_value(self, field, values):
        key = field["key"]
        t = field.get("type", "text")
        if t == "bool":
            values[key] = not values[key]
        elif t == "select" and field.get("options"):
            opts = list(field["options"])
            items = [(o, o) for o in opts] + [("__keep__", "(unverändert)")]
            sel = self._submenu(field["label"], items, 0)
            if sel is not None and items[sel][0] != "__keep__":
                values[key] = items[sel][0]
        elif t == "password":
            raw = self._prompt(f"{field['label']} (leer = leer lassen):", mask=True)
            if raw is not None:
                values[key] = raw
        else:  # text / number / textarea
            raw = self._prompt(f"{field['label']}:", default=str(values[key] or ""))
            if raw is not None:
                values[key] = raw.strip()

    # -- Cloud targets (providers) --
    def _providers_area(self):
        while True:
            try:
                provs = self.api.get_providers()
            except ApiError as exc:
                self.message = f"Konnte Cloud-Ziele nicht laden: {exc.detail}"
                return
            items = [("__add__", "+ Neues Cloud-Ziel")]
            for p in provs:
                items.append((str(p["id"]), f"{p['name']}  [{p['type']}] · {p['status']}"))
            items.append(("__back__", "Zurück"))
            sel = self._submenu("Cloud-Ziele (Provider)", items, 0)
            if sel is None:
                return
            key = items[sel][0]
            if key == "__back__":
                return
            if key == "__add__":
                self._provider_add()
            else:
                self._provider_actions(next(p for p in provs if str(p["id"]) == key))

    def _provider_actions(self, prov):
        acts = [("test", "Verbindung testen"), ("delete", "Löschen"), ("back", "Zurück")]
        sel = self._submenu(f"{prov['name']} [{prov['type']}]", acts, 0,
                            hint=f"Status: {prov['status']}  {prov.get('last_error', '')}")
        if sel is None:
            return
        key = acts[sel][0]
        try:
            if key == "test":
                self.message = "Teste Verbindung …"
                res = self.api.test_provider(prov["id"])
                self.message = f"Test „{prov['name']}“: {res.get('status')} {res.get('last_error', '')}"
            elif key == "delete" and self._confirm(f"„{prov['name']}“ wirklich löschen?"):
                self.api.delete_provider(prov["id"])
                self.message = f"„{prov['name']}“ gelöscht."
        except ApiError as exc:
            self.message = f"Fehlgeschlagen: {exc.detail}"

    def _provider_add(self):
        try:
            types = self.api.get_provider_types()
        except ApiError as exc:
            self.message = f"Konnte Typen nicht laden: {exc.detail}"
            return
        types = sorted(types, key=lambda t: (not t.get("popular"), t["label"]))
        items = [(t["key"], t["label"]) for t in types] + [("__cancel__", "Abbrechen")]
        sel = self._submenu("Cloud-Typ wählen", items, 0)
        if sel is None or items[sel][0] == "__cancel__":
            return
        ptype = next(t for t in types if t["key"] == items[sel][0])
        fields = [{"key": "__name__", "label": "Anzeigename", "type": "text", "required": True}]
        for fd in ptype["fields"]:
            fields.append({
                "key": fd["key"], "label": fd["label"], "type": fd["type"],
                "required": fd.get("required", False), "secret": fd.get("secret", False),
                "options": fd.get("options") or [], "default": fd.get("default", ""),
            })
        vals = self._collect_form(f"Neues Ziel: {ptype['label']}", fields)
        if vals is None:
            return
        name = str(vals.pop("__name__")).strip()
        config = {k: ("true" if v is True else "false" if v is False else str(v))
                  for k, v in vals.items() if str(v) != ""}
        try:
            self.api.create_provider({"name": name, "type": ptype["key"], "config": config})
            self.message = f"Cloud-Ziel „{name}“ angelegt — mit „Verbindung testen“ prüfen."
        except ApiError as exc:
            self.message = f"Anlegen fehlgeschlagen: {exc.detail}"

    # -- VPN --
    def _vpn_area(self):
        while True:
            try:
                tunnels = self.api.get_vpn_tunnels()
                vstatus = self.api.get_vpn_status()
            except ApiError as exc:
                self.message = f"Konnte VPN nicht laden: {exc.detail}"
                return
            items = [("__add__", "+ VPN-Profil aus Datei (.conf/.ovpn)")]
            for t in tunnels:
                mark = "● " if t.get("active") else "  "
                items.append((str(t["id"]), f"{mark}{t['name']}  [{t['type']}]"))
            items.append(("__back__", "Zurück"))
            hint = f"Status: {vstatus.get('state', '?')} {vstatus.get('detail', '')}"
            sel = self._submenu("VPN", items, 0, hint=hint)
            if sel is None:
                return
            key = items[sel][0]
            if key == "__back__":
                return
            if key == "__add__":
                self._vpn_add()
            else:
                self._vpn_actions(next(t for t in tunnels if str(t["id"]) == key))

    def _vpn_actions(self, tunnel):
        acts = [("connect", "Verbinden"), ("disconnect", "Trennen"),
                ("delete", "Löschen"), ("back", "Zurück")]
        sel = self._submenu(f"{tunnel['name']} [{tunnel['type']}]", acts, 0)
        if sel is None:
            return
        key = acts[sel][0]
        try:
            if key == "connect":
                r = self.api.vpn_connect(tunnel["id"])
                self.message = f"VPN: {r.get('state')} {r.get('detail', '')}"
            elif key == "disconnect":
                r = self.api.vpn_disconnect(tunnel["id"])
                self.message = f"VPN: {r.get('state')} {r.get('detail', '')}"
            elif key == "delete" and self._confirm(f"„{tunnel['name']}“ löschen?"):
                self.api.delete_vpn(tunnel["id"])
                self.message = f"„{tunnel['name']}“ gelöscht."
        except ApiError as exc:
            self.message = f"Fehlgeschlagen: {exc.detail}"

    def _vpn_add(self):
        path = self._prompt("Pfad zur Config-Datei (.conf/.ovpn):")
        if not path:
            return
        p = Path(path.strip()).expanduser()
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as exc:
            self.message = f"Datei nicht lesbar: {exc}"
            return
        vtype = "openvpn" if p.suffix.lower() == ".ovpn" else "wireguard"
        name = self._prompt("Name:", default=p.stem)
        if name is None:
            return
        try:
            self.api.create_vpn({"name": name.strip() or p.stem, "type": vtype,
                                 "config": text, "autostart": False})
            self.message = f"VPN-Profil „{name or p.stem}“ ({vtype}) angelegt."
        except ApiError as exc:
            self.message = f"Anlegen fehlgeschlagen: {exc.detail}"

    # -- Network (Wi-Fi / fallback AP) --
    def _network_area(self):
        while True:
            try:
                net = self.api.get_network()
            except ApiError as exc:
                self.message = f"Konnte Netzwerk nicht laden: {exc.detail}"
                return
            st = net.get("status", {})
            mode = st.get("mode", "?")
            if mode == "tether":
                mode = f"USB-Tethering ({TETHER_NAMES.get(st.get('tether'), 'Handy')})"
            if st.get("metered"):
                mode += ", getaktet"
            hint = (f"Modus: {mode} · online: {'ja' if st.get('online') else 'nein'}"
                    f" · AP: {'an' if st.get('ap_active') else 'aus'}")
            items = [
                ("ap", "Fallback-AP & Watchdog"),
                ("wifi", "Bekannte WLANs"),
                ("apply", "Jetzt anwenden"),
                ("scan", "WLAN-Scan"),
                ("back", "Zurück"),
            ]
            sel = self._submenu("Netzwerk", items, 0, hint=hint)
            if sel is None:
                return
            key = items[sel][0]
            if key == "back":
                return
            if key == "ap":
                self._live_editor("Fallback-AP & Watchdog", NETWORK_AP_FIELDS,
                                  lambda: self.api.get_network()["settings"],
                                  self.api.put_network_settings)
            elif key == "wifi":
                self._network_wifi()
            elif key == "apply":
                try:
                    r = self.api.network_apply()
                    self.message = f"Anwenden: {r.get('message', 'ok')}"
                except ApiError as exc:
                    self.message = f"Anwenden fehlgeschlagen: {exc.detail}"
            elif key == "scan":
                try:
                    ssids = self.api.network_scan().get("ssids", [])
                    self.message = ("Gefunden: " + ", ".join(ssids[:8])) if ssids \
                        else "WLAN-Scan: kein Netz sichtbar."
                except ApiError as exc:
                    self.message = f"Scan fehlgeschlagen: {exc.detail}"

    def _network_wifi(self):
        while True:
            try:
                known = self.api.get_network().get("known_networks", [])
            except ApiError as exc:
                self.message = f"Konnte WLANs nicht laden: {exc.detail}"
                return
            items = [("__add__", "+ WLAN hinzufügen")]
            for n in known:
                items.append((str(n["id"]), f"{n['ssid']}  (Prio {n['priority']})"))
            items.append(("__back__", "Zurück"))
            sel = self._submenu("Bekannte WLANs", items, 0)
            if sel is None:
                return
            key = items[sel][0]
            if key == "__back__":
                return
            if key == "__add__":
                self._wifi_add()
            elif self._confirm("WLAN löschen?"):
                try:
                    self.api.delete_known_network(int(key))
                    self.message = "WLAN gelöscht."
                except ApiError as exc:
                    self.message = f"Löschen fehlgeschlagen: {exc.detail}"

    def _wifi_add(self):
        fields = [
            {"key": "ssid", "label": "WLAN-Name (SSID)", "type": "text", "required": True},
            {"key": "password", "label": "Passwort", "type": "password", "required": False},
            {"key": "priority", "label": "Priorität (höher=zuerst)", "type": "number",
             "required": False, "default": "0"},
            {"key": "autoconnect", "label": "Automatisch verbinden", "type": "bool",
             "required": False, "default": "true"},
        ]
        vals = self._collect_form("WLAN hinzufügen", fields)
        if vals is None:
            return
        try:
            self.api.add_known_network({
                "ssid": str(vals["ssid"]).strip(),
                "password": str(vals["password"]),
                "priority": int(str(vals["priority"]).strip() or "0"),
                "autoconnect": bool(vals["autoconnect"]),
            })
            self.message = f"WLAN „{vals['ssid']}“ gespeichert."
        except (ApiError, ValueError) as exc:
            detail = getattr(exc, "detail", str(exc))
            self.message = f"Speichern fehlgeschlagen: {detail}"

    # -- read-only text viewer (pool overview, storage, release notes) --
    def _show_text(self, title, lines):
        top = 0
        self.scr.timeout(-1)
        try:
            while True:
                self.scr.erase()
                h, w = self.scr.getmaxyx()
                self._center(0, title, curses.A_BOLD | self.color(1))
                view = max(3, h - 3)
                for i in range(view):
                    if top + i < len(lines):
                        self._addstr(2 + i, 2, str(lines[top + i])[: w - 3])
                more = " · ↑↓ scrollen" if len(lines) > view else ""
                self._addstr(h - 1, 1, f"Esc: zurück{more}", curses.A_DIM)
                self.scr.noutrefresh()
                curses.doupdate()
                ch = self.scr.getch()
                if ch in (curses.KEY_UP, ord("k")):
                    top = max(0, top - 1)
                elif ch in (curses.KEY_DOWN, ord("j")):
                    top = min(max(0, len(lines) - view), top + 1)
                elif ch in (27, ord("q"), ord("Q"), curses.KEY_ENTER, 10, 13):
                    return
        finally:
            self.scr.timeout(5000)

    # -- Bandwidth --
    def _bandwidth_editor(self):
        self._live_editor("Bandbreite", BANDWIDTH_FIELDS,
                          self.api.get_bandwidth, self.api.put_bandwidth)

    # -- Pool (multi-server) --
    def _pool_overview(self):
        try:
            ov = self.api.get_pool_overview()
        except ApiError as exc:
            self.message = f"Konnte Pool nicht laden: {exc.detail}"
            return

        def node_lines(n, prefix=""):
            out = [f"{prefix}{n.get('name', '?')}"
                   f"  ({'erreichbar' if n.get('reachable') else 'offline'})"]
            if n.get("version"):
                out.append(f"{prefix}   Version: {n['version']}")
            out.append(f"{prefix}   Medien: {n.get('media_total', 0)}"
                       f" · aktive Transfers: {n.get('active_transfers', 0)}")
            if n.get("disk_total"):
                out.append(f"{prefix}   Speicher frei: {_h(n.get('disk_free', 0))}"
                           f" / {_h(n['disk_total'])}")
            if n.get("throughput_kbps"):
                out.append(f"{prefix}   Durchsatz: {n['throughput_kbps']:.0f} KiB/s")
            if n.get("error"):
                out.append(f"{prefix}   Fehler: {n['error']}")
            return out

        lines = ["— Dieser Knoten —"] + node_lines(ov.get("self") or {})
        peers = ov.get("peers", [])
        lines += ["", f"— Peers ({len(peers)}) —"]
        if not peers:
            lines.append("  (keine konfiguriert)")
        for p in peers:
            lines += node_lines(p, prefix="  ")
        self._show_text("Pool-Übersicht", lines)

    def _pool_pair(self):
        """Show a pairing code to type in on the hub (the box's own screen is the proof)."""
        try:
            r = self.api.open_pool_pairing()
        except ApiError as exc:
            self.message = f"Konnte keinen Code erzeugen: {exc.detail}"
            return
        code = r.get("code", "")
        minutes = int(r.get("expires_in", 0)) // 60
        self._show_text("Kopplungscode", [
            "Am Hub unter Pool → „In der Nähe gefunden“ bei dieser Box eingeben:",
            "",
            f"        {' '.join(code)}",
            "",
            f"Gültig {minutes} Minuten, nur einmal verwendbar.",
            "Der Hub bekommt damit nur Leserecht; „steuern“ hier unter",
            "„Zugriff für Hubs (Tokens)“ vergeben.",
        ])

    def _pool_token(self):
        """One token per hub: create, switch read/control, revoke."""
        scope_label = {"read": "lesen", "control": "steuern"}
        while True:
            try:
                tokens = self.api.get_pool_tokens()
            except ApiError as exc:
                self.message = f"Konnte Tokens nicht laden: {exc.detail}"
                return
            items = [("__add__", "+ Token für einen Hub erzeugen")]
            for t in tokens:
                items.append((str(t["id"]),
                              f"{t['name']}  …{t['hint']}  [{scope_label.get(t['scope'], '?')}]"))
            items.append(("__back__", "Zurück"))
            hint = (f"{len(tokens)} Hub(s) dürfen abfragen" if tokens
                    else "Nicht teilbar (kein Token)")
            sel = self._submenu("Zugriff für Hubs", items, 0, hint=hint)
            if sel is None or items[sel][0] == "__back__":
                return
            key = items[sel][0]
            try:
                if key == "__add__":
                    vals = self._collect_form("Token für einen Hub", [
                        {"key": "name", "label": "Für welchen Hub? (z. B. ELW)",
                         "type": "text", "required": True},
                        {"key": "control", "label": "Darf Transfers steuern",
                         "type": "bool", "required": False, "default": "false"},
                    ])
                    if vals is None:
                        continue
                    r = self.api.create_pool_token(
                        str(vals["name"]).strip(), "control" if vals["control"] else "read")
                    self._show_text("Neuer Pool-Token", [
                        f"Token für „{r.get('name', '')}“ — wird nur jetzt angezeigt.",
                        "Beim Hub als Peer-Token eintragen:", "", r.get("token", "")])
                    continue
                tok = next(t for t in tokens if str(t["id"]) == key)
                other = "read" if tok["scope"] == "control" else "control"
                acts = [("scope", f"Recht ändern auf: {scope_label[other]}"),
                        ("revoke", "Zugriff entziehen"), ("back", "Zurück")]
                used = tok.get("last_used_hub") or ("benutzt" if tok.get("last_used_at")
                                                    else "noch nie benutzt")
                act = self._submenu(f"Token „{tok['name']}“", acts, 0, hint=used)
                if act is None:
                    continue
                if acts[act][0] == "scope":
                    self.api.set_pool_token_scope(int(key), other)
                    self.message = f"„{tok['name']}“ darf jetzt {scope_label[other]}."
                elif acts[act][0] == "revoke" and self._confirm(
                        f"Zugriff für „{tok['name']}“ entziehen?"):
                    self.api.delete_pool_token(int(key))
                    self.message = "Zugriff entzogen."
            except ApiError as exc:
                self.message = f"Fehlgeschlagen: {exc.detail}"

    def _pool_peers(self):
        while True:
            try:
                peers = self.api.get_pool_peers()
            except ApiError as exc:
                self.message = f"Konnte Peers nicht laden: {exc.detail}"
                return
            items = [("__add__", "+ Peer hinzufügen")]
            for p in peers:
                items.append((str(p["id"]),
                              f"{p['name']}  {p['base_url']}  {'✓' if p['enabled'] else '×'}"))
            items.append(("__back__", "Zurück"))
            sel = self._submenu("Pool-Peers (andere Boxen)", items, 0)
            if sel is None:
                return
            key = items[sel][0]
            if key == "__back__":
                return
            if key == "__add__":
                fields = [
                    {"key": "name", "label": "Name", "type": "text", "required": True},
                    {"key": "base_url", "label": "URL (http://host:8000)", "type": "text",
                     "required": True},
                    {"key": "token", "label": "Peer-Token", "type": "password", "required": False},
                ]
                vals = self._collect_form("Peer hinzufügen", fields)
                if vals is None:
                    continue
                try:
                    self.api.add_pool_peer({"name": str(vals["name"]).strip(),
                                            "base_url": str(vals["base_url"]).strip(),
                                            "token": str(vals["token"])})
                    self.message = "Peer hinzugefügt."
                except ApiError as exc:
                    self.message = f"Fehlgeschlagen: {exc.detail}"
            elif self._confirm("Peer löschen?"):
                try:
                    self.api.delete_pool_peer(int(key))
                    self.message = "Peer gelöscht."
                except ApiError as exc:
                    self.message = f"Löschen fehlgeschlagen: {exc.detail}"

    # -- Users --
    def _users_area(self):
        while True:
            try:
                users = self.api.get_users()
            except ApiError as exc:
                self.message = f"Konnte Benutzer nicht laden: {exc.detail}"
                return
            items = [("__add__", "+ Benutzer anlegen")]
            for u in users:
                flag = "" if u["active"] else " · inaktiv"
                items.append((str(u["id"]), f"{u['email']}  [{u['role']}]{flag}"))
            items.append(("__back__", "Zurück"))
            sel = self._submenu("Benutzer", items, 0)
            if sel is None:
                return
            key = items[sel][0]
            if key == "__back__":
                return
            if key == "__add__":
                self._user_add()
            else:
                self._user_actions(next(u for u in users if str(u["id"]) == key))

    def _user_add(self):
        fields = [
            {"key": "email", "label": "E-Mail", "type": "text", "required": True},
            {"key": "name", "label": "Name", "type": "text", "required": False},
            {"key": "password", "label": "Passwort (min. 8)", "type": "password", "required": True},
            {"key": "role", "label": "Rolle", "type": "select",
             "options": ["user", "admin"], "default": "user", "required": True},
        ]
        vals = self._collect_form("Benutzer anlegen", fields)
        if vals is None:
            return
        try:
            self.api.create_user({"email": str(vals["email"]).strip(),
                                  "name": str(vals["name"]).strip(),
                                  "password": str(vals["password"]),
                                  "role": str(vals["role"])})
            self.message = f"Benutzer „{vals['email']}“ angelegt."
        except ApiError as exc:
            self.message = f"Anlegen fehlgeschlagen: {exc.detail}"

    def _user_actions(self, user):
        active = user["active"]
        acts = [("pw", "Passwort zurücksetzen"),
                ("toggle", "Deaktivieren" if active else "Aktivieren"),
                ("role", "Rolle wechseln (user/admin)"),
                ("delete", "Löschen"), ("back", "Zurück")]
        sel = self._submenu(f"{user['email']} [{user['role']}]", acts, 0)
        if sel is None:
            return
        key = acts[sel][0]
        try:
            if key == "pw":
                pw = self._prompt("Neues Passwort (min. 8):", mask=True)
                if pw:
                    self.api.update_user(user["id"], {"password": pw})
                    self.message = "Passwort geändert."
            elif key == "toggle":
                self.api.update_user(user["id"], {"active": not active})
                self.message = "Aktualisiert."
            elif key == "role":
                new = "admin" if user["role"] == "user" else "user"
                if self._confirm(f"Rolle auf „{new}“ setzen?"):
                    self.api.update_user(user["id"], {"role": new})
                    self.message = f"Rolle: {new}."
            elif key == "delete" and self._confirm(f"„{user['email']}“ löschen?"):
                self.api.delete_user(user["id"])
                self.message = "Benutzer gelöscht."
        except ApiError as exc:
            self.message = f"Fehlgeschlagen: {exc.detail}"

    # -- Groups --
    def _groups_area(self):
        while True:
            try:
                groups = self.api.get_groups()
                users = self.api.get_users()
            except ApiError as exc:
                self.message = f"Konnte Gruppen nicht laden: {exc.detail}"
                return
            items = [("__add__", "+ Gruppe anlegen")]
            for g in groups:
                items.append((str(g["id"]),
                              f"{g['name']}  ({len(g.get('member_ids', []))} Mitglieder)"))
            items.append(("__back__", "Zurück"))
            sel = self._submenu("Gruppen", items, 0)
            if sel is None:
                return
            key = items[sel][0]
            if key == "__back__":
                return
            if key == "__add__":
                fields = [{"key": "name", "label": "Name", "type": "text", "required": True},
                          {"key": "description", "label": "Beschreibung", "type": "text",
                           "required": False}]
                vals = self._collect_form("Gruppe anlegen", fields)
                if vals is None:
                    continue
                try:
                    self.api.create_group({"name": str(vals["name"]).strip(),
                                           "description": str(vals["description"]).strip()})
                    self.message = "Gruppe angelegt."
                except ApiError as exc:
                    self.message = f"Anlegen fehlgeschlagen: {exc.detail}"
            else:
                self._group_actions(next(g for g in groups if str(g["id"]) == key), users)

    def _group_actions(self, group, users):
        acts = [("members", "Mitglieder verwalten"), ("delete", "Löschen"), ("back", "Zurück")]
        sel = self._submenu(f"Gruppe: {group['name']}", acts, 0)
        if sel is None:
            return
        key = acts[sel][0]
        if key == "members":
            self._group_members(group, users)
        elif key == "delete" and self._confirm(f"Gruppe „{group['name']}“ löschen?"):
            try:
                self.api.delete_group(group["id"])
                self.message = "Gruppe gelöscht."
            except ApiError as exc:
                self.message = f"Löschen fehlgeschlagen: {exc.detail}"

    def _group_members(self, group, users):
        if not users:
            self.message = "Keine Benutzer vorhanden."
            return
        member_ids = set(group.get("member_ids", []))
        pos = 0
        top = 0
        self.scr.timeout(-1)
        try:
            while True:
                h, w = self.scr.getmaxyx()
                rows = max(3, h - 4)
                if pos < top:
                    top = pos
                elif pos >= top + rows:
                    top = pos - rows + 1
                self.scr.erase()
                self._center(0, f"Mitglieder: {group['name']}", curses.A_BOLD | self.color(1))
                x = max(2, (w - 56) // 2)
                y = 2
                for idx in range(top, min(len(users), top + rows)):
                    u = users[idx]
                    box = "[x]" if u["id"] in member_ids else "[ ]"
                    is_sel = idx == pos
                    self._addstr(y, x, f"{'▶' if is_sel else ' '} {box} {u['email']}",
                                 curses.A_REVERSE if is_sel else 0)
                    y += 1
                self._addstr(h - 1, 1,
                             "Space: umschalten · s: speichern · Esc: abbrechen", curses.A_DIM)
                self.scr.noutrefresh()
                curses.doupdate()
                ch = self.scr.getch()
                if ch in (curses.KEY_UP, ord("k")):
                    pos = (pos - 1) % len(users)
                elif ch in (curses.KEY_DOWN, ord("j")):
                    pos = (pos + 1) % len(users)
                elif ch in (ord(" "), curses.KEY_ENTER, 10, 13):
                    uid = users[pos]["id"]
                    member_ids.discard(uid) if uid in member_ids else member_ids.add(uid)
                elif ch in (ord("s"), ord("S")):
                    try:
                        self.api.set_group_members(group["id"], sorted(member_ids))
                        self.message = "Mitglieder gespeichert."
                    except ApiError as exc:
                        self.message = f"Speichern fehlgeschlagen: {exc.detail}"
                    return
                elif ch == 27:
                    return
        finally:
            self.scr.timeout(5000)

    # -- System: updates --
    def _update_progress(self) -> dict:
        try:
            return self.api.get_update_progress()
        except ApiError:
            return {}

    def _updates_area(self):
        force = False
        selected = 0
        while True:
            if force:
                self._busy("Suche nach Updates …")
            try:
                info = self.api.get_update_info(force=force)
            except ApiError as exc:
                self.message = f"Konnte Update-Info nicht laden: {exc.detail}"
                return
            prog = self._update_progress()
            phase = prog.get("phase", "idle")
            avail = info.get("update_available")
            latest = info.get("latest") or "—"
            if force:
                if info.get("error"):
                    self.message = f"Prüfung fehlgeschlagen: {info['error']}"
                elif avail:
                    self.message = f"Neue Version verfügbar: {latest}."
                else:
                    self.message = "Keine neue Version — die Box ist aktuell."
            force = False

            cur = info.get("current", "?")
            if info.get("channel") == "main":
                commit = (info.get("commit") or "")[:7]
                cur += f" (main @ {commit})" if commit else " (main)"
            behind = info.get("behind_by") or 0
            hint = [f"Installiert : {cur}",
                    f"Verfügbar   : {latest}" + (f" ({behind} neue Commits)" if behind else ""),
                    "Status      : " + ("UPDATE VERFÜGBAR" if avail else
                                        "Prüfung fehlgeschlagen" if info.get("error") else
                                        "aktuell")]
            if avail and not info.get("self_update_enabled"):
                hint.append("One-Click-Update ist aus. Auf der Box ausführen:")
                hint.append("  docker compose pull && docker compose up -d" if info.get("in_docker")
                            else "  sudo /opt/offgridcloud/src/deploy/update.sh")
            if phase == "running":
                hint.append("Ein Update läuft gerade.")

            items = []
            if phase == "running":
                items.append(("progress", "Update-Fortschritt ansehen (läuft)"))
            items.append(("check", "Nach Updates suchen"))
            items.append(("info", "Details / Release-Notes"))
            if avail and info.get("self_update_enabled") and phase != "running":
                items.append(("apply", f"Jetzt aktualisieren ({latest})"))
            if phase in ("success", "failed", "unknown"):
                items.append(("progress", f"Letztes Update: {UPDATE_PHASE.get(phase, phase)}"
                                          " — Protokoll"))
            items.append(("back", "Zurück"))
            sel = self._submenu("Version & Updates", items, min(selected, len(items) - 1),
                                hint="\n".join(hint))
            if sel is None:
                return
            selected = sel
            key = items[sel][0]
            if key == "back":
                return
            if key == "check":
                force = True
            elif key == "info":
                self._update_details(info, cur, latest, avail)
            elif key == "progress":
                self._update_progress_view()
            elif key == "apply":
                if not self._confirm(f"Auf {latest} aktualisieren? Der Dienst startet neu."):
                    self.message = "Abgebrochen."
                    continue
                try:
                    self.api.apply_update()
                except ApiError as exc:
                    self.message = f"Update fehlgeschlagen: {exc.detail}"
                    continue
                self._update_progress_view()

    def _update_details(self, info, cur, latest, avail):
        lines = [f"Installiert: {cur}", f"Verfügbar:   {latest}",
                 f"Status:      {'Update verfügbar' if avail else 'aktuell'}"]
        for lbl, k in [("Release", "release_name"), ("Datum", "published_at"),
                       ("Link", "release_url"), ("Hinweis", "error")]:
            if info.get(k):
                lines.append(f"{lbl + ':':<12} {info[k]}")
        if info.get("notes"):
            lines += ["", "Release-Notes:"] + info["notes"].splitlines()
        self._show_text("Version & Updates", lines)

    def _update_progress_view(self):
        """Live view of the self-update: phase, versions, elapsed time, log tail.

        The update restarts the service, so the API vanishes for a while — we
        keep the last log on screen and poll until it answers again."""
        prog = {}
        api_up = True
        self.scr.timeout(2000)
        try:
            while True:
                try:
                    prog = self.api.get_update_progress()
                    api_up = True
                except ApiError as exc:
                    if exc.code == 401:
                        self.message = "Sitzung abgelaufen — bitte erneut anmelden."
                        return
                    api_up = False
                phase = prog.get("phase", "running" if not api_up else "idle")
                running = phase == "running" or not api_up
                self.scr.erase()
                h, w = self.scr.getmaxyx()
                self._center(0, "Update-Fortschritt", curses.A_BOLD | self.color(1))
                x = 2
                attr = self.color({"success": 2, "failed": 3}.get(phase, 4))
                self._addstr(2, x, f"Status    : {UPDATE_PHASE.get(phase, phase)}", attr)
                frm, to = prog.get("from_version"), prog.get("to_version")
                if frm or to:
                    self._addstr(3, x, f"Version   : {frm or '?'} → {to or '?'}")
                started = prog.get("started_at") or 0
                if started:
                    end = prog.get("finished_at") or time.time()
                    self._addstr(4, x, f"Dauer     : {_duration(end - started)}")
                note = prog.get("message", "")
                if not api_up:
                    note = "Dienst startet neu — warte auf die API …"
                elif phase == "success":
                    note += " Konsole neu öffnen, um ihre neue Version zu laden."
                self._addstr(5, x, note[: w - 4], self.color(4) if not api_up else 0)
                log = (prog.get("log") or "").splitlines()
                view = max(1, h - 9)
                self._addstr(7, x, "Protokoll (Ende):", curses.A_DIM)
                for i, line in enumerate(log[-view:]):
                    self._addstr(8 + i, x, line[: w - 4], curses.A_DIM)
                if running:
                    foot = "Esc: zurück — das Update läuft im Hintergrund weiter"
                else:
                    foot = "Enter: Verstanden (Ergebnis ausblenden) · Esc: zurück"
                self._addstr(h - 1, 1, foot[: w - 2], curses.A_DIM)
                self.scr.noutrefresh()
                curses.doupdate()
                ch = self.scr.getch()
                if ch in (27, ord("q"), ord("Q")):
                    break
                if ch in (curses.KEY_ENTER, 10, 13) and not running:
                    try:
                        self.api.dismiss_update()
                    except ApiError:
                        pass
                    break
            if prog.get("phase") == "success":
                self.message = f"Update erfolgreich: {prog.get('to_version') or 'neue Version'}."
            elif prog.get("phase") == "failed":
                self.message = f"Update fehlgeschlagen: {prog.get('message', '')}"
            self.status = gather_status()
        finally:
            self.scr.timeout(5000)

    # -- Storage media: drives, cards, imports, cache drives --
    def _drives_area(self):
        selected = 0
        while True:
            try:
                ov = self.api.get_storage()
            except ApiError as exc:
                self.message = f"Konnte Laufwerke nicht laden: {exc.detail}"
                return
            rows = []  # (key, label, disk, partition-or-None)
            for d in ov.get("disks", []):
                if not d.get("partitions"):
                    rows.append((d["path"], f"{_disk_title(d)} · keine Partition", d, None))
                for part in d.get("partitions", []):
                    rows.append((part["path"],
                                 f"{_disk_title(d)} › {part.get('label') or part['name']}"
                                 f"  {_part_state(part)}", d, part))
            items = [(r[0], r[1]) for r in rows] + [("__refresh__", "Aktualisieren"),
                                                     ("__back__", "Zurück")]
            buf = ov.get("buffer") or {}
            hint = [f"Puffer: {_h(buf.get('free', 0))} frei von {_h(buf.get('total', 0))}"]
            if not rows:
                hint.append("Kein Laufwerk erkannt — Karte oder USB-Laufwerk einstecken.")
            if not ov.get("manage_enabled"):
                hint.append("Verwaltung nicht eingerichtet (install.sh) — nur Anzeige.")
            if ov.get("error"):
                hint.append(ov["error"])
            sel = self._submenu("Laufwerke & Speicherkarten", items,
                                min(selected, len(items) - 1), hint="\n".join(hint))
            if sel is None or items[sel][0] == "__back__":
                return
            selected = sel
            if items[sel][0] == "__refresh__":
                continue
            _, _, disk, part = rows[sel]
            self._drive_actions(ov, disk, part)

    def _drive_actions(self, ov, disk, part):
        manage = ov.get("manage_enabled")
        if disk.get("protected") or (part and part.get("protected")):
            self.message = "System- bzw. Pufferlaufwerk — hier nicht verwaltbar."
            return
        acts = []
        if part:
            mounted = bool(part.get("mountpoint"))
            if part.get("browsable") and not part.get("cache"):
                acts.append(("import", "In einen Ordner hochladen"))
                acts.append(("backup", "Auf Cache-Laufwerk sichern"))
            if manage and not mounted and part.get("fstype"):
                acts.append(("mount", "Einhängen"))
            if manage and mounted:
                acts.append(("unmount", "Aushängen"))
            if part.get("cache"):
                acts.append(("unmark", "Cache-Markierung entfernen"))
            elif mounted and part.get("uuid") and not part.get("read_only"):
                acts.append(("mark", "Als Cache-Laufwerk markieren"))
            if manage:
                acts.append(("format_part", "Partition formatieren"))
        if manage:
            acts.append(("eject", "Laufwerk auswerfen"))
            acts.append(("format_disk", "Ganzes Laufwerk formatieren"))
        acts.append(("back", "Zurück"))
        if part:
            title = f"{_disk_title(disk)} › {part.get('label') or part['name']}"
            hint = [f"Gerät: {part['path']} · {part.get('fstype') or 'kein Dateisystem'}"
                    f" · {_h(part.get('size', 0))}"]
            if part.get("mountpoint"):
                hint.append(f"Eingehängt: {part['mountpoint']} · {_h(part.get('used', 0))} "
                            f"belegt, {_h(part.get('free', 0))} frei")
            if part.get("busy"):
                hint.append("Auf diesem Medium läuft gerade ein Vorgang.")
        else:
            title = _disk_title(disk)
            hint = [f"Gerät: {disk['path']} · keine Partition — zum Nutzen formatieren."]
        sel = self._submenu(title, acts, 0, hint="\n".join(hint))
        if sel is None:
            return
        key = acts[sel][0]
        try:
            if key == "import":
                self._import_wizard(part)
            elif key == "backup":
                self._backup_wizard(part)
            elif key in ("mount", "unmount"):
                self._busy("Einhängen …" if key == "mount" else "Aushängen …")
                r = self.api.storage_action(key, part["path"])
                self.message = (f"Eingehängt unter {r.get('mountpoint', '?')}." if key == "mount"
                                else f"{part['path']} ausgehängt.")
            elif key == "eject" and self._confirm(f"{_disk_title(disk)} auswerfen?"):
                self._busy("Auswerfen …")
                self.api.storage_action("eject", disk["path"])
                self.message = f"{_disk_title(disk)} kann jetzt entfernt werden."
            elif key == "mark":
                self.api.mark_cache(part["path"])
                self.message = f"{part['path']} ist jetzt Cache-Laufwerk für Kartensicherungen."
            elif key == "unmark" and self._confirm("Cache-Markierung entfernen? "
                                                   "(Sicherungen bleiben auf dem Laufwerk)"):
                self.api.unmark_cache(part["uuid"])
                self.message = "Cache-Markierung entfernt."
            elif key in ("format_part", "format_disk"):
                target = part if key == "format_part" else disk
                self._format_wizard(ov, target["path"])
        except ApiError as exc:
            self.message = f"Fehlgeschlagen: {exc.detail}"

    def _format_wizard(self, ov, device):
        filesystems = ov.get("filesystems") or ["exfat"]
        items = [(f, f) for f in filesystems] + [("__cancel__", "Abbrechen")]
        sel = self._submenu(f"{device} formatieren — Dateisystem", items, 0,
                            hint="exFAT: Kameras/PCs · ext4: nur Linux (Cache-Laufwerk)")
        if sel is None or items[sel][0] == "__cancel__":
            return
        fstype = items[sel][0]
        max_len = (ov.get("label_max") or {}).get(fstype, 11)
        label = self._prompt(f"Bezeichnung (A–Z, 0–9, _ -, max. {max_len}):", default="OFFGRID")
        if label is None:
            return
        name = device.rsplit("/", 1)[-1]
        confirm = self._prompt(f"ALLE DATEN auf {device} werden gelöscht! Zur Bestätigung "
                               f"„{name}“ eingeben:")
        if confirm is None or confirm.strip() != name:
            self.message = "Abgebrochen — nichts formatiert."
            return
        self._busy(f"Formatiere {device} als {fstype} …")
        try:
            self.api.storage_format(device, fstype, label.strip(), confirm.strip())
            self.message = f"{device} als {fstype} „{label.strip()}“ formatiert."
        except ApiError as exc:
            self.message = f"Formatieren fehlgeschlagen: {exc.detail}"

    def _pick_source(self, part):
        """Choose what to copy: the whole medium or one folder on it.
        Returns a relative path ("" = whole medium) or None if cancelled."""
        device = part["path"]
        path = ""
        while True:
            try:
                br = self.api.storage_browse(device, path)
            except ApiError as exc:
                self.message = f"Konnte Medium nicht lesen: {exc.detail}"
                return None
            dirs = [e for e in br.get("entries", []) if e.get("is_dir")]
            files = [e for e in br.get("entries", []) if not e.get("is_dir")]
            media = sum(1 for e in files if e.get("is_media"))
            where = "/" + (br.get("path") or "")
            items = [("__pick__", f"✓ Diesen Ordner nehmen ({where})")]
            if br.get("parent") is not None:
                items.append(("__up__", ".. (eine Ebene höher)"))
            items += [(e["path"], f"▸ {e['name']}/") for e in dirs]
            items.append(("__cancel__", "Abbrechen"))
            hint = f"Hier: {len(dirs)} Ordner, {len(files)} Dateien ({media} Medien)"
            sel = self._submenu("Quelle wählen", items, 0, hint=hint)
            if sel is None or items[sel][0] == "__cancel__":
                return None
            key = items[sel][0]
            if key == "__pick__":
                return br.get("path") or ""
            path = (br.get("parent") or "") if key == "__up__" else key

    def _import_wizard(self, part):
        try:
            folders = self.api.get_folders()
        except ApiError as exc:
            self.message = f"Konnte Ordner nicht laden: {exc.detail}"
            return
        if not folders:
            self.message = "Noch kein Upload-Ordner — erst in der Web-Oberfläche anlegen."
            return
        items = [(str(f["id"]), f"{f['name']}  ({f.get('media_count', 0)} Dateien)")
                 for f in folders] + [("__cancel__", "Abbrechen")]
        sel = self._submenu("Ziel-Ordner wählen", items, 0)
        if sel is None or items[sel][0] == "__cancel__":
            return
        folder = folders[sel]
        src = self._pick_source(part)
        if src is None:
            return
        vals = self._collect_form(f"Hochladen nach „{folder['name']}“", [
            {"key": "media_only", "label": "Nur Fotos/Videos/Audio", "type": "bool",
             "default": "true"},
            {"key": "skip_duplicates", "label": "Schon Vorhandenes überspringen",
             "type": "bool", "default": "true"},
            {"key": "tags", "label": "Schlagwörter (Komma-getrennt)", "type": "text"},
        ])
        if vals is None:
            return
        tags = [t.strip() for t in str(vals["tags"]).split(",") if t.strip()]
        self._busy("Dateien werden gezählt …")
        try:
            job = self.api.start_import({
                "device": part["path"], "paths": [src], "folder_id": folder["id"],
                "media_only": bool(vals["media_only"]),
                "skip_duplicates": bool(vals["skip_duplicates"]), "tags": tags,
            })
        except ApiError as exc:
            self.message = f"Hochladen fehlgeschlagen: {exc.detail}"
            return
        self.message = f"Upload gestartet: {job.get('total_files', 0)} Dateien."
        self._jobs_view()

    def _backup_wizard(self, part):
        try:
            caches = [c for c in self.api.get_caches() if c.get("device")]
        except ApiError as exc:
            self.message = f"Konnte Cache-Laufwerke nicht laden: {exc.detail}"
            return
        if not caches:
            self.message = ("Kein Cache-Laufwerk angeschlossen — ein Laufwerk unter "
                            "„Laufwerke“ als Cache markieren.")
            return
        cache = caches[0]
        if len(caches) > 1:
            items = [(c["uuid"], f"{c['label']}  ({_h(c.get('free', 0))} frei)")
                     for c in caches] + [("__cancel__", "Abbrechen")]
            sel = self._submenu("Cache-Laufwerk wählen", items, 0)
            if sel is None or items[sel][0] == "__cancel__":
                return
            cache = caches[sel]
        src = self._pick_source(part)
        if src is None:
            return
        vals = self._collect_form(f"Sichern auf „{cache['label']}“", [
            {"key": "media_only", "label": "Nur Fotos/Videos/Audio", "type": "bool",
             "default": "true"},
            {"key": "only_new", "label": "Nur Neues kopieren", "type": "bool",
             "default": "true"},
            {"key": "eject_after", "label": "Karte danach auswerfen", "type": "bool",
             "default": "false"},
        ])
        if vals is None:
            return
        self._busy("Dateien werden gezählt …")
        try:
            job = self.api.cache_backup({
                "device": part["path"], "paths": [src] if src else [],
                "cache_uuid": cache["uuid"], "media_only": bool(vals["media_only"]),
                "only_new": bool(vals["only_new"]), "eject_after": bool(vals["eject_after"]),
            })
        except ApiError as exc:
            self.message = f"Sicherung fehlgeschlagen: {exc.detail}"
            return
        self.message = f"Sicherung gestartet: {job.get('total_files', 0)} Dateien."
        self._jobs_view()

    def _jobs_view(self):
        """Live list of imports / cache backups with progress; c cancels, x clears."""
        jobs = []
        err = ""
        pos = 0
        top = 0
        self.scr.timeout(1500)
        try:
            while True:
                try:
                    jobs = self.api.get_imports()
                    err = ""
                except ApiError as exc:
                    err = f"Aktualisieren fehlgeschlagen: {exc.detail}"
                if jobs:
                    pos = min(pos, len(jobs) - 1)
                self.scr.erase()
                h, w = self.scr.getmaxyx()
                self._center(0, "Vorgänge (Upload & Sicherung)", curses.A_BOLD | self.color(1))
                x = 2
                block = 4
                per_page = max(1, (h - 4) // block)
                if pos < top:
                    top = pos
                elif pos >= top + per_page:
                    top = pos - per_page + 1
                if not jobs:
                    self._addstr(2, x, "Keine Vorgänge.", curses.A_DIM)
                for i, j in enumerate(jobs[top: top + per_page]):
                    idx = top + i
                    y = 2 + i * block
                    cache = j.get("kind") == "cache"
                    target = (j.get("target", "").split("::")[-1] if cache
                              else f"„{j.get('folder_name', '')}“")
                    head = (f"{'▶' if idx == pos else ' '} "
                            f"{'Cache-Sicherung' if cache else 'Upload'} {j.get('device', '')}"
                            f" → {target}")
                    status = JOB_STATUS.get(j.get("status"), j.get("status", ""))
                    self._addstr(y, x, head[: w - 20], curses.A_REVERSE if idx == pos else 0)
                    self._addstr(y, max(x, w - 18), status,
                                 self.color({"done": 2, "failed": 3}.get(j.get("status"), 4)))
                    done, total = j.get("done_bytes", 0), j.get("total_bytes", 0)
                    if j.get("status") == "done":
                        done = total = 1
                    self._addstr(y + 1, x + 2,
                                 f"{_bar(done, total)} {j.get('done_files', 0)}/"
                                 f"{j.get('total_files', 0)} Dateien · "
                                 f"{_h(j.get('done_bytes', 0))}/{_h(j.get('total_bytes', 0))}")
                    detail = (f"{j.get('imported', 0)} {'kopiert' if cache else 'übernommen'} · "
                              f"{j.get('skipped', 0)} übersprungen · "
                              f"{j.get('failed', 0)} fehlgeschlagen")
                    if j.get("status") == "running" and j.get("current"):
                        detail += f" · {j['current']}"
                    elif j.get("error"):
                        detail += f" · {j['error']}"
                    self._addstr(y + 2, x + 2, detail[: w - x - 4], curses.A_DIM)
                if err:
                    self._addstr(h - 2, 1, err[: w - 2], self.color(3))
                self._addstr(h - 1, 1, "↑↓ · Enter: Fehler · c: abbrechen · "
                                       "x: Erledigte entfernen · Esc: zurück", curses.A_DIM)
                self.scr.noutrefresh()
                curses.doupdate()
                ch = self.scr.getch()
                if ch in (27, ord("q"), ord("Q")):
                    return
                if ch in (curses.KEY_UP, ord("k")) and jobs:
                    pos = (pos - 1) % len(jobs)
                elif ch in (curses.KEY_DOWN, ord("j")) and jobs:
                    pos = (pos + 1) % len(jobs)
                elif ch in (ord("c"), ord("C")) and jobs:
                    j = jobs[pos]
                    if j.get("status") in ("pending", "running") and \
                            self._confirm("Diesen Vorgang abbrechen?"):
                        try:
                            self.api.cancel_import(j["id"])
                        except ApiError as exc:
                            err = f"Abbrechen fehlgeschlagen: {exc.detail}"
                    self.scr.timeout(1500)
                elif ch in (ord("x"), ord("X")):
                    try:
                        self.api.clear_imports()
                    except ApiError as exc:
                        err = f"Entfernen fehlgeschlagen: {exc.detail}"
                elif ch in (curses.KEY_ENTER, 10, 13) and jobs:
                    errors = jobs[pos].get("errors") or []
                    self._show_text("Fehlerdetails", errors or ["Keine Fehler."])
                    self.scr.timeout(1500)
        finally:
            self.scr.timeout(5000)

    def _caches_area(self):
        while True:
            try:
                caches = self.api.get_caches()
            except ApiError as exc:
                self.message = f"Konnte Cache-Laufwerke nicht laden: {exc.detail}"
                return
            items = []
            for c in caches:
                where = (f"{c['device']} · {_h(c.get('free', 0))} frei von {_h(c.get('total', 0))}"
                         if c.get("device") and not c.get("error") else c.get("error") or "—")
                items.append((c["uuid"], f"{c['label']}  {where} · "
                                         f"{len(c.get('snapshots', []))} Sicherungen"))
            items.append(("__back__", "Zurück"))
            hint = ("" if caches else
                    "Noch kein Cache-Laufwerk. Unter „Laufwerke & Speicherkarten“\n"
                    "ein Laufwerk als Cache markieren — dann lassen sich Karten\n"
                    "auch ohne Netz darauf sichern.")
            sel = self._submenu("Kartensicherungen (Cache-Laufwerke)", items, 0, hint=hint)
            if sel is None or items[sel][0] == "__back__":
                return
            self._cache_actions(caches[sel])

    def _cache_actions(self, cache):
        while True:
            snaps = cache.get("snapshots", [])
            items = [(s["name"], f"{s['name']}  · {s.get('files', 0)} Dateien · "
                                 f"{_h(s.get('size', 0))}") for s in snaps]
            items += [("__unmark__", "Cache-Markierung entfernen"), ("__back__", "Zurück")]
            hint = "Enter auf einer Sicherung: löschen" if snaps else "Noch keine Sicherungen."
            sel = self._submenu(f"Cache: {cache['label']}", items, 0, hint=hint)
            if sel is None or items[sel][0] == "__back__":
                return
            key = items[sel][0]
            try:
                if key == "__unmark__":
                    if self._confirm("Markierung entfernen? (Sicherungen bleiben erhalten)"):
                        self.api.unmark_cache(cache["uuid"])
                        self.message = "Cache-Markierung entfernt."
                        return
                elif self._confirm(f"Sicherung „{key}“ endgültig löschen?"):
                    self.api.delete_snapshot(cache["uuid"], key)
                    self.message = f"Sicherung „{key}“ gelöscht."
                    cache = next((c for c in self.api.get_caches()
                                  if c["uuid"] == cache["uuid"]), None)
                    if cache is None:
                        return
            except ApiError as exc:
                self.message = f"Fehlgeschlagen: {exc.detail}"

    def _storage_view(self):
        try:
            sysd = self.api.get_system()
        except ApiError as exc:
            self.message = f"Konnte Speicher nicht laden: {exc.detail}"
            return
        d = sysd.get("disk", {})
        lines = [f"Belegt:      {_h(d.get('used', 0))}",
                 f"Frei:        {_h(d.get('free', 0))}",
                 f"Gesamt:      {_h(d.get('total', 0))}",
                 f"Auslastung:  {d.get('percent_used', 0):.0f} %",
                 f"Wenig Speicher: {'JA' if d.get('low_space') else 'nein'}"]
        self._show_text("Speicherbelegung (Puffer)", lines)


def _disk_title(disk: dict) -> str:
    name = " ".join(x for x in (disk.get("vendor", ""), disk.get("model", "")) if x).strip()
    return f"{name or disk.get('name', '?')} ({_h(disk.get('size', 0))})"


def _part_state(part: dict) -> str:
    if part.get("system"):
        state = "System"
    elif part.get("buffer"):
        state = "Puffer"
    elif part.get("mountpoint"):
        state = f"{part.get('fstype') or 'eingehängt'} · {_h(part.get('free', 0))} frei"
    elif part.get("fstype"):
        state = f"{part['fstype']} · nicht eingehängt"
    else:
        state = "kein Dateisystem"
    if part.get("cache"):
        state += " · Cache"
    if part.get("read_only"):
        state += " · schreibgeschützt"
    if part.get("busy"):
        state += " · in Arbeit"
    return state


def _bar(done: float, total: float, width: int = 20) -> str:
    frac = min(1.0, done / total) if total else 0.0
    filled = int(frac * width)
    return f"[{'#' * filled}{'-' * (width - filled)}] {frac * 100:3.0f} %"


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60} min {seconds % 60:02d} s" if seconds >= 60 else f"{seconds} s"


def _has_chromium() -> bool:
    return any(shutil.which(b) for b in ("chromium-browser", "chromium"))


class _suspended:
    """Context manager: leave curses so a child process owns the real tty."""

    def __enter__(self):
        curses.def_prog_mode()
        curses.endwin()
        return self

    def __exit__(self, *exc):
        # Re-enter curses; getch() below refreshes the screen on next loop.
        curses.reset_prog_mode()
        return False


# --- Entry points -----------------------------------------------------------
def _cli_set_pin() -> int:
    if os.geteuid() != 0:
        print("Bitte als root ausführen (sudo).", file=sys.stderr)
        return 1
    import getpass

    pin1 = getpass.getpass("Neue Admin-PIN: ")
    if len(pin1.strip()) < 4:
        print("PIN zu kurz (mindestens 4 Zeichen).", file=sys.stderr)
        return 1
    if pin1 != getpass.getpass("PIN wiederholen: "):
        print("PINs stimmen nicht überein.", file=sys.stderr)
        return 1
    PIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    PIN_FILE.write_text(hash_pin(pin1) + "\n", encoding="utf-8")
    os.chmod(PIN_FILE, 0o600)
    print(f"PIN gespeichert: {PIN_FILE}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "--hash-pin":
        if len(argv) < 3:
            print("usage: offgrid-console.py --hash-pin PIN", file=sys.stderr)
            return 2
        print(hash_pin(argv[2]))
        return 0
    if len(argv) >= 2 and argv[1] == "--set-pin":
        return _cli_set_pin()

    global curses
    import curses as _curses

    curses = _curses

    def _start(scr):
        try:
            curses.curs_set(0)
        except curses.error:
            pass  # terminals without a hideable cursor still work
        Console(scr).run()

    curses.wrapper(_start)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
