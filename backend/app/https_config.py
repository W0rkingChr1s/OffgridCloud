"""HTTPS reverse-proxy config helpers.

Pure logic (validation + reading the state file that ``deploy/https/apply.sh``
writes) kept separate from the FastAPI router so it's unit-testable without
HTTP — same split as ``power.py`` vs. ``routers/system.py``. The privileged
work (rendering the Caddyfile, reloading Caddy, setting the hostname) lives in
the bash script; Python only validates input and shells out to it.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

_STATE_FILENAME = "https_state.json"

# mDNS short name: DNS label rules — letters/digits/hyphen, no leading/trailing
# hyphen, 1–63 chars. Avahi appends ".local" itself.
_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
# Public domain: one or more dot-separated DNS labels (needs at least one dot).
_DOMAIN_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN_RE = re.compile(rf"^{_DOMAIN_LABEL}(\.{_DOMAIN_LABEL})+$")


def normalise_hostname(value: str) -> str:
    """Lowercase, trim, and strip a trailing ``.local`` (avahi adds it back)."""
    cleaned = value.strip().lower()
    if cleaned.endswith(".local"):
        cleaned = cleaned[: -len(".local")]
    return cleaned


def validate_hostname(value: str) -> str:
    """Return ``value`` if it's a valid mDNS short name, else raise ValueError."""
    if not _HOSTNAME_RE.match(value):
        raise ValueError(
            "Ungültiger Hostname. Erlaubt: Buchstaben, Ziffern und Bindestriche "
            "(kein Bindestrich am Anfang/Ende), 1–63 Zeichen."
        )
    return value


def validate_domain(value: str) -> str:
    """Normalise + validate a public domain. Empty string means 'no domain'."""
    cleaned = value.strip().lower()
    if cleaned == "":
        return ""
    if not _DOMAIN_RE.match(cleaned):
        raise ValueError(
            "Ungültige Domain. Erwartet z. B. cloud.example.com "
            "(ohne http://, mindestens ein Punkt)."
        )
    return cleaned


def read_state(data_dir: Path) -> dict[str, str]:
    """Read ``<data_dir>/https_state.json`` written by apply.sh.

    Missing or unreadable → defaults, so the endpoint never fails hard on a box
    where HTTPS was never set up.
    """
    path = Path(data_dir) / _STATE_FILENAME
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"hostname": "", "domain": ""}
    return {
        "hostname": str(raw.get("hostname", "")),
        "domain": str(raw.get("domain", "")),
    }


def caddy_running(*, run=subprocess.run) -> bool:
    """Best-effort: is the Caddy reverse proxy up on this box?

    Uses ``systemctl is-active`` and treats *any* problem (no systemd, no caddy
    unit, timeout) as "not running" — never raises, so the status endpoint stays
    safe on a dev box or in Docker. ``run`` is injectable for tests.
    """
    try:
        result = run(
            ["systemctl", "is-active", "--quiet", "caddy"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def is_active(data_dir: Path, *, run=subprocess.run) -> bool:
    """True when HTTPS is actually serving on this box.

    Two independent signals, either is enough — this is what tells "TLS is
    genuinely up" apart from "the UI can re-apply the config" (see the router):
      * apply.sh has run at least once (state file carries a hostname), or
      * the Caddy service is up right now (covers a hand-configured box whose
        state file we never wrote).
    Checks the cheap file signal first and only shells out to systemctl when it
    has to.
    """
    if read_state(data_dir)["hostname"]:
        return True
    return caddy_running(run=run)


def run_apply(command: str, *, hostname: str, domain: str, run=subprocess.run) -> str:
    """Run the configured apply command with --hostname (and --domain if set).

    ``command`` is an operator-configured value from the .env (trusted, never
    user input) — we split it with shlex and append the validated flags. Returns
    stdout on success; raises RuntimeError with the stderr tail on a non-zero
    exit so the endpoint can surface *why* it failed. ``run`` is injectable for
    tests (mirrors power.run_power_command's ``popen`` seam).
    """
    if not command.strip():
        raise ValueError("empty https apply command")
    argv = [*shlex.split(command), "--hostname", hostname]
    if domain:
        argv += ["--domain", domain]
    result = run(argv, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip()[-500:]
        raise RuntimeError(tail or f"apply.sh exited with {result.returncode}")
    return result.stdout
