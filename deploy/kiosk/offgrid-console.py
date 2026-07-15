#!/usr/bin/env python3
"""OffgridCloud local console — the on-box "OffgridCloud OS" menu.

Runs full-screen on the primary console (tty1) via ``offgrid-kiosk.service``.
When a screen is attached to the box, this is *all* that is visible: a branded
menu with live status and a handful of appliance actions (restart the service,
reboot, shut down, open the web UI in a browser). Dropping into the underlying
Raspberry Pi OS shell is deliberately gated behind an admin PIN, so an attached
keyboard alone does not hand out a root prompt.

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
    def _prompt(self, question, mask=False):
        """Single-line prompt at the bottom; returns the typed string or None."""
        h, w = self.scr.getmaxyx()
        self.scr.timeout(-1)
        curses.curs_set(1)
        buf = ""
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
