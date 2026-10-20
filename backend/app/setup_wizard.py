"""First-boot setup wizard for the prebuilt OS image.

The flashable image must not carry secrets: every box flashed from it would
otherwise share the same admin password. So the image boots into **setup mode**
instead (``OGC_SETUP_WIZARD=true`` and no user yet):

* the root helper ``deploy/setup/setup-ap.sh`` hosts an open Wi-Fi
  (``OffgridCloud-Setup-XXXX``) with a captive portal whenever the box has no
  uplink of its own (no cable, no pre-configured Wi-Fi),
* every API except ``/api/setup/*`` and ``/api/health`` answers 503, and
* the web UI shows the wizard under ``/setup``: admin account, box name, the
  Wi-Fi to join and the password of the fallback Wi-Fi.

Submitting the wizard creates the admin and the network config, then hands off
to the privileged ``deploy/setup/finish.sh`` (setup AP down, chosen network and
hostname applied). Once a user exists, setup mode is over for good.

"First come, first admin" is the inherent risk of an open setup network. An
operator can close it by putting ``offgridcloud-setup.txt`` with a
``SETUP_CODE=`` line on the boot partition after flashing: firstboot moves its
hash into ``<data_dir>/setup.json`` and the wizard then requires the code.

Pure helpers (state files, code check, captive redirect) are kept apart from
the router so they are testable without HTTP.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .crypto import encrypt
from .https_config import normalise_hostname, validate_hostname
from .models import KnownNetwork, Role, User
from .network import (
    get_network_settings,
    validate_country,
    validate_passphrase,
    validate_ssid,
    write_config_file,
)
from .security import hash_password

logger = logging.getLogger("offgridcloud.setup")

STATE_FILENAME = "setup.json"  # written by deploy/setup/firstboot.sh
RESULT_FILENAME = "setup-result.json"  # read by deploy/setup/finish.sh
# NetworkManager's default "shared" address — the setup AP hands it out and its
# dnsmasq answers every DNS name with it (the captive portal).
SETUP_AP_ADDRESS = "10.42.0.1"
SETUP_AP_NETWORK = ipaddress.ip_network("10.42.0.0/24")
# Priority of the Wi-Fi chosen in the wizard: above anything added later by
# default (0), so the box prefers the network it was set up with.
WIZARD_WIFI_PRIORITY = 10

# Wrong-code throttle: after this many misses, refuse attempts for a while.
MAX_CODE_FAILURES = 5
LOCKOUT_SECONDS = 60.0

_lock = threading.Lock()  # one wizard submission at a time
_completed = False  # cached: once a user exists, setup mode never returns


# --------------------------------------------------------------------------- #
# Mode                                                                         #
# --------------------------------------------------------------------------- #
def setup_required() -> bool:
    """True while the image's setup wizard still has to run."""
    global _completed
    if _completed or not get_settings().setup_wizard:
        return False
    from .db import SessionLocal

    with SessionLocal() as db:
        if has_users(db):
            _completed = True
            return False
    return True


def has_users(db: Session) -> bool:
    return db.scalar(select(User.id).limit(1)) is not None


def reset_cache() -> None:
    """Forget the cached "completed" flag (tests)."""
    global _completed
    _completed = False


# --------------------------------------------------------------------------- #
# State written by firstboot                                                   #
# --------------------------------------------------------------------------- #
def read_state(data_dir: Path) -> dict:
    """``setup.json`` from firstboot; missing/broken → empty (no code needed)."""
    try:
        raw = json.loads((Path(data_dir) / STATE_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def code_required(state: dict) -> bool:
    return bool(state.get("code_sha256"))


def hash_code(code: str, salt: str) -> str:
    """Same construction as firstboot.sh: sha256(salt + code), hex."""
    return hashlib.sha256((salt + code.strip()).encode("utf-8")).hexdigest()


def code_matches(state: dict, code: str) -> bool:
    expected = state.get("code_sha256") or ""
    if not expected:
        return True
    return hmac.compare_digest(hash_code(code or "", state.get("salt") or ""), expected)


class CodeThrottle:
    """Tiny in-memory lockout so the setup code can't be brute-forced."""

    def __init__(self, *, now=time.monotonic) -> None:
        self._now = now
        self._failures = 0
        self._locked_until = 0.0

    def retry_after(self) -> float:
        return max(0.0, self._locked_until - self._now())

    def failure(self) -> None:
        self._failures += 1
        if self._failures >= MAX_CODE_FAILURES:
            self._failures = 0
            self._locked_until = self._now() + LOCKOUT_SECONDS

    def success(self) -> None:
        self._failures = 0


throttle = CodeThrottle()


def suggested_hostname(state: dict) -> str:
    try:
        return validate_hostname(normalise_hostname(state.get("suggested_hostname") or ""))
    except ValueError:
        return "offgridcloud"


# --------------------------------------------------------------------------- #
# Captive portal                                                               #
# --------------------------------------------------------------------------- #
def captive_redirect(host: str, client_ip: str) -> str | None:
    """Where to send a page request for ``host`` while in setup mode.

    On the setup AP every DNS name resolves to the box, so a phone's captive
    portal probe (``captive.apple.com``, ``connectivitycheck.gstatic.com``…)
    arrives here with a foreign Host header — answer it with a redirect to the
    wizard, which makes the OS pop up its portal browser. Only clients on the
    setup AP's subnet are redirected: on a LAN the box may legitimately be
    reached by a router-provided name (``offgridcloud-setup.fritz.box``).
    Requests for the box itself (an IP, ``*.local``, ``localhost``) are served
    normally.
    """
    try:
        if ipaddress.ip_address(client_ip) not in SETUP_AP_NETWORK:
            return None
    except ValueError:
        return None
    name = (host or "").strip().lower()
    if name.startswith("["):  # [v6]:port
        name = name[1 : name.find("]")] if "]" in name else name
    elif name.count(":") == 1:
        name = name.split(":", 1)[0]
    if not name or name == "localhost" or name.endswith(".local"):
        return None
    try:
        ipaddress.ip_address(name)
        return None
    except ValueError:
        pass
    if "." not in name:
        return None  # a bare name someone typed for the box itself
    return f"http://{SETUP_AP_ADDRESS}/setup"


def client_ip(peer: str, forwarded_for: str) -> str:
    """The browser's address: behind the local Caddy the peer is loopback and
    the real one is the first ``X-Forwarded-For`` entry."""
    try:
        loopback = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        loopback = False
    if loopback and forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return peer


# --------------------------------------------------------------------------- #
# Wi-Fi scan cache                                                             #
# --------------------------------------------------------------------------- #
def read_scan(path: Path) -> list[str]:
    """SSIDs from the scan setup-ap.sh took before raising the AP."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    return [s for s in raw if isinstance(s, str) and s.strip()]


def merge_ssids(*lists: list[str]) -> list[str]:
    seen: list[str] = []
    for items in lists:
        for ssid in items:
            if ssid not in seen:
                seen.append(ssid)
    return seen


# --------------------------------------------------------------------------- #
# Completion                                                                   #
# --------------------------------------------------------------------------- #
@dataclass
class SetupInput:
    admin_email: str
    admin_password: str
    admin_name: str
    hostname: str
    wifi_ssid: str
    wifi_password: str
    fallback_password: str
    country: str


@dataclass
class SetupResult:
    hostname: str
    wifi_ssid: str
    finish_started: bool


class SetupError(ValueError):
    """Invalid wizard input (→ 422)."""


def complete(db: Session, data: SetupInput) -> SetupResult:
    """Create the admin + network config and hand off to the root helper.

    Raises :class:`SetupError` on bad input and ``RuntimeError`` if setup is
    already done (a second, racing submission).
    """
    try:
        hostname = validate_hostname(normalise_hostname(data.hostname))
        country = validate_country(data.country)
        ssid = validate_ssid(data.wifi_ssid) if data.wifi_ssid.strip() else ""
        wifi_psk = validate_passphrase(data.wifi_password, allow_empty=True) if ssid else ""
        fallback_psk = validate_passphrase(data.fallback_password, allow_empty=False)
    except ValueError as exc:
        raise SetupError(str(exc)) from exc

    with _lock:
        if has_users(db):
            raise RuntimeError("Die Box ist bereits eingerichtet.")

        db.add(
            User(
                email=data.admin_email,
                name=data.admin_name.strip() or "Administrator",
                role=Role.ADMIN,
                password_hash=hash_password(data.admin_password),
            )
        )
        if ssid:
            db.add(
                KnownNetwork(
                    ssid=ssid,
                    psk_encrypted=encrypt(wifi_psk) if wifi_psk else "",
                    priority=WIZARD_WIFI_PRIORITY,
                )
            )
        net = get_network_settings(db)
        # The fallback AP is mandatory out of the wizard: if the chosen Wi-Fi
        # password is wrong, it is the only way back into the box.
        net.fallback_enabled = True
        net.ap_ssid = hostname[:32]
        net.ap_psk_encrypted = encrypt(fallback_psk)
        net.country_code = country
        db.commit()

        settings = get_settings()
        write_config_file(db)
        result_path = Path(settings.data_dir) / RESULT_FILENAME
        result_path.write_text(
            json.dumps({"hostname": hostname, "country": country}), encoding="utf-8"
        )
        result_path.chmod(0o600)

    logger.warning("Setup wizard completed: admin '%s', host '%s'.", data.admin_email, hostname)
    started = launch_finish(settings.setup_finish_command)
    return SetupResult(hostname=hostname, wifi_ssid=ssid, finish_started=started)


def launch_finish(command: str, *, popen=subprocess.Popen) -> bool:
    """Start the privileged finish helper detached (it outlives this request)."""
    command = command.strip()
    if not command:
        return False
    try:
        popen(  # noqa: S603 (operator-configured command)
            shlex.split(command),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        logger.error("Could not start setup finish command: %s", exc)
        return False
    return True
