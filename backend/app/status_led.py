"""Status LED: tell the field team what the box is doing — without a screen.

The auto-import (:mod:`app.auto_import`) runs unattended; whoever plugs in a
card needs to know when it may be pulled again, even with no phone, no Wi-Fi
and no portal at hand. One LED carries four states:

* ``idle``  — the LED's normal behaviour (on a Pi: SD-card activity flicker)
* ``busy``  — slow, even blink (0.5 s on / 0.5 s off): copying, don't pull
* ``done``  — steady on: everything copied (and ejected) — pull the medium
* ``error`` — fast flicker (0.1 s / 0.1 s): something failed, check the portal

Any LED under ``/sys/class/leds`` works: the Pi's green ``ACT`` (``led0`` on
older kernels) by default, the red ``PWR``, or an external LED on a GPIO pin
via ``dtoverlay=gpio-led,gpio=17,label=ogc-status`` in ``config.txt`` — which
is the way to get a bright LED on the front of a case.

Writing to sysfs needs root, so the LED is driven through the storage root
helper (``storage.sh led …``); blinking is done by the kernel's ``timer``
trigger, so nothing in here has to wake up to toggle it. Every call is
best-effort: a missing LED or helper never disturbs an import.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path

logger = logging.getLogger("offgridcloud.status_led")

LEDS_DIR = Path("/sys/class/leds")
DEFAULT_NAMES = ("ACT", "led0")
NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
STATES = ("idle", "busy", "done", "error")

_lock = threading.Lock()
_current: tuple[str, str] | None = None  # (led name, state) last applied
_original: dict[str, tuple[str, int]] = {}  # led name -> (trigger, brightness) before us
_preview: tuple[str, float] | None = None  # (state, until monotonic)


def available() -> list[str]:
    try:
        return sorted(p.name for p in LEDS_DIR.iterdir() if NAME_RE.match(p.name))
    except OSError:
        return []


def resolve(name: str = "") -> str:
    """The LED to use: the configured one if present, else ``ACT``/``led0``."""
    leds = available()
    if name:
        return name if name in leds else ""
    return next((n for n in DEFAULT_NAMES if n in leds), "")


def _read_state(name: str) -> tuple[str, int]:
    base = LEDS_DIR / name
    trigger = "none"
    try:
        m = re.search(r"\[([^\]]+)\]", (base / "trigger").read_text())
        if m:
            trigger = m.group(1)
    except OSError:
        pass
    try:
        brightness = int((base / "brightness").read_text().strip() or 0)
    except (OSError, ValueError):
        brightness = 0
    return trigger, brightness


def _helper(*args: str) -> None:
    from . import drives

    drives._helper("led", *args)


def _apply(name: str, state: str) -> None:
    if state == "busy":
        _helper(name, "blink", "500", "500")
    elif state == "done":
        _helper(name, "on")
    elif state == "error":
        _helper(name, "blink", "100", "100")
    else:
        trigger, brightness = _original.get(name, ("", 0))
        if not trigger:
            return  # never touched — nothing to restore
        if trigger == "none":
            _helper(name, "on" if brightness else "off")
        else:
            _helper(name, "trigger", trigger)


def set_state(state: str, name: str = "") -> bool:
    """Show ``state`` on the LED (no-op if unchanged). Returns success."""
    global _current
    if state not in STATES:
        raise ValueError(f"unknown LED state {state!r}")
    with _lock:
        handed_back = state == "idle" and _current is not None
    led = _current[0] if handed_back and _current else resolve(name)
    if not led:
        return False
    with _lock:
        if _current == (led, state):
            return True
        if _current is not None and _current[0] != led:
            # Switched to another LED: hand the old one back first.
            try:
                _apply(_current[0], "idle")
            except Exception:  # noqa: BLE001
                pass
            _current = None
        if state != "idle" and led not in _original:
            _original[led] = _read_state(led)
        try:
            _apply(led, state)
        except Exception as exc:  # noqa: BLE001 - LED is best-effort
            logger.debug("LED %s -> %s failed: %s", led, state, exc)
            return False
        _current = (led, state)
        return True


def current() -> str:
    with _lock:
        return _current[1] if _current else "idle"


def preview(state: str, seconds: float = 6.0) -> None:
    """Show ``state`` for a few seconds (the "LED testen" button)."""
    global _preview
    if state not in STATES:
        raise ValueError(f"unknown LED state {state!r}")
    with _lock:
        _preview = (state, time.monotonic() + seconds)


def preview_state() -> str | None:
    """The state a running preview wants shown, or ``None``."""
    global _preview
    with _lock:
        if _preview is None:
            return None
        if time.monotonic() > _preview[1]:
            _preview = None
            return None
        return _preview[0]


def reset() -> None:
    """Forget everything (tests)."""
    global _current, _preview
    with _lock:
        _current = None
        _preview = None
        _original.clear()
