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
import urllib.error
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


# --- Curses UI --------------------------------------------------------------
# "OffgridCloud" in the figlet "cybermedium" font — compact (3 rows) so it fits
# even an 80x24 console, and reads clearly on the framebuffer console font.
LOGO = [
    "____ ____ ____ ____ ____ _ ___  ____ _    ____ _  _ ___",
    "|  | |___ |___ | __ |__/ | |  \\ |    |    |  | |  | |  \\",
    "|__| |    |    |__] |  \\ | |__/ |___ |___ |__| |__| |__/",
]

MENU = [
    ("refresh", "Status aktualisieren"),
    ("url", "Admin-Zugang anzeigen"),
    ("settings", "Einstellungen (Admin)"),
    ("browser", "Web-Oberfläche im Browser öffnen"),
    ("restart", "OffgridCloud-Dienst neu starten"),
    ("reboot", "Box neu starten"),
    ("shutdown", "Box herunterfahren"),
    ("shell", "Zur Raspberry-Pi-Shell (PIN)"),
]


class Console:
    def __init__(self, stdscr):
        self.scr = stdscr
        self.selected = 0
        self.status = gather_status()
        self.message = "Pfeiltasten: Auswahl · Enter: bestätigen · q: nichts tun"
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

    # -- drawing --
    def draw(self):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        y = 1
        for line in LOGO:
            self._center(y, line, curses.A_BOLD | self.color(1))
            y += 1
        self._center(y + 1, "OS · Lokale Konsole", self.color(4))
        y += 3

        y = self._draw_status(y + 1, w)
        y += 1
        self._draw_menu(y, w)
        self._status_bar(h, w)
        self.scr.noutrefresh()
        curses.doupdate()

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

    def _draw_status(self, y, w):
        s = self.status
        svc_ok = s["service"] == "active"
        rows = [
            ("Gerät", s["host"], 0),
            ("IP-Adresse", s["ips"], 0),
            ("Web-Oberfläche", s["url"], self.color(1)),
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

    def _draw_menu(self, y, w):
        x = max(2, (w - 64) // 2)
        for i, (key, label) in enumerate(self._visible_menu()):
            selected = i == self.selected
            prefix = " ▶ " if selected else "   "
            attr = curses.A_REVERSE | curses.A_BOLD if selected else 0
            self._addstr(y, x, f"{prefix}{label}", attr)
            y += 1

    def _status_bar(self, h, w):
        self._addstr(h - 1, 1, self.message[: w - 2], self.color(4))

    def _visible_menu(self):
        items = list(MENU)
        # Hide the browser item unless a chromium launcher is actually installed.
        if not (CHROMIUM_LAUNCHER.exists() and _has_chromium()):
            items = [it for it in items if it[0] != "browser"]
        return items

    # -- interaction --
    def run(self):
        self.scr.timeout(5000)  # auto-refresh status every 5s
        while True:
            self.draw()
            try:
                ch = self.scr.getch()
            except KeyboardInterrupt:
                continue  # Ctrl-C must not drop out of the kiosk
            if ch == -1:
                self.status = gather_status()
                continue
            if ch in (curses.KEY_UP, ord("k")):
                self.selected = (self.selected - 1) % len(self._visible_menu())
            elif ch in (curses.KEY_DOWN, ord("j")):
                self.selected = (self.selected + 1) % len(self._visible_menu())
            elif ch in (curses.KEY_ENTER, 10, 13):
                self._activate(self._visible_menu()[self.selected][0])
            elif ch in (ord("q"), ord("Q")):
                self.message = "Kein Grund zu gehen — die Box läuft. (Enter für Aktionen)"

    def _activate(self, key):
        if key == "refresh":
            self.status = gather_status()
            self.message = "Status aktualisiert."
        elif key == "url":
            self._show_access()
        elif key == "settings":
            self._settings_area()
        elif key == "browser":
            self._launch_browser()
        elif key == "restart":
            self._power("restart", "OffgridCloud-Dienst neu starten?",
                        ["systemctl", "restart", SERVICE_NAME])
        elif key == "reboot":
            self._power("reboot", "Box wirklich NEU STARTEN?",
                        ["systemctl", "reboot"])
        elif key == "shutdown":
            self._power("shutdown", "Box wirklich HERUNTERFAHREN?",
                        ["systemctl", "poweroff"])
        elif key == "shell":
            self._drop_to_shell()

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

    def _show_access(self):
        s = self.status
        self.message = (
            f"Öffnen: {s['url']}  ·  Login: {s['admin_email']} "
            f"(Passwort steht im Installations-Protokoll)"
        )

    def _power(self, _key, question, cmd):
        if not self._confirm(question):
            self.message = "Abgebrochen."
            return
        self.message = "Aktion läuft …"
        self.draw()
        # Capture output so a chatty command can't bleed onto the curses screen.
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip().splitlines()
            hint = detail[-1] if detail else "journalctl -xe"
            self.message = f"Fehlgeschlagen (Code {proc.returncode}): {hint}"
        else:
            self.message = "Erledigt."

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

    def _settings_area(self):
        if not self._ensure_login():
            return
        items = [
            ("system", "System & Benachrichtigungen"),
            ("providers", "Cloud-Ziele (Provider)"),
            ("vpn", "VPN"),
            ("network", "Netzwerk (WLAN / Fallback-AP)"),
            ("logout", "Abmelden"),
            ("back", "Zurück zum Hauptmenü"),
        ]
        sel = 0
        while True:
            sel = self._submenu(f"Einstellungen — {self.admin_email}", items, sel)
            if sel is None:
                return
            key = items[sel][0]
            if key == "system":
                self._live_editor("System & Benachrichtigungen", SYSTEM_FIELDS,
                                  self.api.get_system, self.api.patch_system)
            elif key == "providers":
                self._providers_area()
            elif key == "vpn":
                self._vpn_area()
            elif key == "network":
                self._network_area()
            elif key == "logout":
                self.api.token = None
                self.message = "Abgemeldet."
                return
            else:  # back
                return

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
                if hint:
                    self._addstr(y + 1, x, hint, curses.A_DIM)
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
            hint = (f"Modus: {st.get('mode', '?')} · online: {'ja' if st.get('online') else 'nein'}"
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
