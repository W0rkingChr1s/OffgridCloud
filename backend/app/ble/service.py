"""offgridcloud-ble: one connection at a time, advertising, ``info`` (§3).

BlueZ sends notifications to every subscribed central, so with per-connection
encryption a second phone would receive frames it cannot decrypt. Version 1
therefore serves exactly one connection; the spec allows that (§3.3). While a
session is open the box stops advertising and ignores writes from others.

The D-Bus specifics live in ``gatt_bluez.py`` behind ``Gatt`` so this logic
is tested without a radio.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Protocol

from .. import setup_wizard
from .bridge import BoxHost
from .keys import ble_dir
from .protocol import (
    FLAG_BACKEND_UP,
    FLAG_PAIRING_OPEN,
    FLAG_SETUP_PENDING,
    MIN_MTU,
    advert_name,
    build_info,
)
from .session import Guard, Session

log = logging.getLogger("offgridcloud.ble")

POLL_INTERVAL = 5.0
# Consecutive failed refreshes (~1 min) before the service exits so systemd
# restarts it with a fresh BlueZ registration instead of idling invisibly.
MAX_REFRESH_FAILURES = 12
# From here on the web UI no longer counts the heartbeat (routers/ble, 30 s).
STALE_FAILURES = 6
STATUS_FILE = "service.json"


class Gatt(Protocol):
    def set_info(self, value: bytes) -> None: ...
    async def notify(self, frame: bytes) -> None: ...
    async def start_advertising(self, name: str, flags: int) -> None: ...
    async def stop_advertising(self) -> None: ...
    async def disconnect(self, device: str) -> None: ...


class BleService:
    def __init__(self, host: BoxHost, gatt: Gatt) -> None:
        self.host = host
        self.gatt = gatt
        self.guard = Guard()
        self.session: Session | None = None
        self.device: str | None = None
        self.mtu = MIN_MTU
        self.flags = 0
        self.enabled = False
        self._advertising: tuple[str, int] | None = None

    # --- called by the GATT layer -----------------------------------------

    async def on_write(self, device: str, data: bytes, mtu: int | None) -> None:
        if not self.enabled:
            return
        if self.session is not None and device != self.device:
            return  # one connection at a time; never answer a second central
        if mtu:
            self.mtu = max(MIN_MTU, mtu)
        if self.session is None:
            self.device = device
            self.session = Session(
                self.host, self.guard, self.gatt.notify, lambda: self.mtu, self._closed
            )
            await self._update_advertising()
        await self.session.receive(data)

    async def on_disconnect(self, device: str) -> None:
        if self.session is not None and device == self.device:
            await self.session.close("Verbindung getrennt")

    async def _closed(self, reason: str) -> None:
        device, self.device, self.session = self.device, None, None
        self.mtu = MIN_MTU
        if device:
            try:
                await self.gatt.disconnect(device)
            except Exception:  # already gone
                log.debug("Trennen von %s fehlgeschlagen", device, exc_info=True)
        await self._update_advertising()

    # --- state ------------------------------------------------------------

    async def refresh(self) -> None:
        """Read settings and box state, update ``info`` and advertising."""
        self.enabled = await self.host.read_enabled()
        self.host.set_enabled(self.enabled)
        flags = 0
        if await asyncio.to_thread(setup_wizard.setup_required):
            flags |= FLAG_SETUP_PENDING
        if await self.host.pairing_open():
            flags |= FLAG_PAIRING_OPEN
        if await self.host.backend_up():
            flags |= FLAG_BACKEND_UP
        self.flags = flags
        self.gatt.set_info(
            build_info(self.host.box_name(), flags, _public(self.host.box_key))
        )
        if not self.enabled and self.session is not None:
            await self.session.close("Bluetooth abgeschaltet")
        await self._update_advertising()
        _write_status(self.enabled, self.session is not None)

    async def _update_advertising(self) -> None:
        wanted = None
        if self.enabled and self.session is None:
            wanted = (advert_name(self.host.box_name()), self.flags)
        if wanted == self._advertising:
            return
        if self._advertising is not None:
            # Forget it first: a failed unregister must not be retried forever.
            self._advertising = None
            await self.gatt.stop_advertising()
        if wanted is not None:
            await self.gatt.start_advertising(*wanted)
            self._advertising = wanted

    async def run(self, stop: asyncio.Event) -> None:
        failures = 0
        while not stop.is_set():
            if not getattr(self.gatt, "alive", True):
                write_startup_error("bluetoothd wurde neu gestartet")
                raise RuntimeError("bluetoothd wurde neu gestartet")
            try:
                await self.refresh()
                failures = 0
            except Exception as exc:
                log.exception("BLE-Statusabgleich fehlgeschlagen")
                failures += 1
                if failures >= STALE_FAILURES:
                    # The heartbeat is stale by now: say why in the web UI.
                    write_startup_error(f"{type(exc).__name__}: {exc}")
                if failures >= MAX_REFRESH_FAILURES:
                    raise
            try:
                await asyncio.wait_for(stop.wait(), POLL_INTERVAL)
            except TimeoutError:
                pass
        if self.session is not None:
            await self.session.close("Dienst beendet")
        if self._advertising is not None:
            await self.gatt.stop_advertising()
            self._advertising = None


def _public(key) -> bytes:
    from .noise import public_bytes

    return public_bytes(key)


def _write_status(enabled: bool, connected: bool) -> None:
    """Heartbeat for the web UI (``routers/ble.service_running``)."""
    _write_status_file({"updated": time.time(), "enabled": enabled, "connected": connected})


def write_startup_error(message: str) -> None:
    """Leave the reason start or refresh failed for the web UI (no ``updated``: not alive)."""
    _write_status_file({"error": message[:300], "error_at": time.time()})


def _write_status_file(data: dict) -> None:
    path = ble_dir() / STATUS_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), "utf-8")
    tmp.replace(path)
