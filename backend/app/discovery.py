"""Find other OffgridCloud boxes in the LAN via mDNS (zeroconf).

Every box announces ``_offgridcloud._tcp.local.`` with its node id, name and
version in the TXT record and browses for the others; the Pool page lists what
it finds. mDNS is unauthenticated, so what it announces is only ever a
*suggestion*: access still needs a token, which a hub gets by pairing with a
short code read off the other box (see ``pool.PairingCode``). Nothing here ever
sends a stored token to an address learned via mDNS.

Runs inside the uvicorn event loop and never takes the box down: without
multicast (Docker bridge network, no interface yet) it just reports why.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import threading
import time

import ifaddr
from zeroconf import IPVersion, ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_offgridcloud._tcp.local."
RESOLVE_TIMEOUT_MS = 3000
# Interfaces come and go (fallback AP, USB tethering): re-check our addresses.
ADDRESS_REFRESH_SECONDS = 60.0


def _own_addresses() -> list[str]:
    """This box's IPv4 addresses, without loopback and link-local."""
    found = {
        ip.ip
        for adapter in ifaddr.get_adapters()
        for ip in adapter.ips
        if isinstance(ip.ip, str) and not ip.ip.startswith(("127.", "169.254."))
    }
    return sorted(found)


def best_address(addresses: list[str]) -> str:
    """Prefer an address in one of our own /24s — the one we can likely reach."""
    ours = {a.rsplit(".", 1)[0] for a in _own_addresses()}
    for addr in addresses:
        if addr.rsplit(".", 1)[0] in ours:
            return addr
    return addresses[0] if addresses else ""


def _txt(properties: dict) -> dict[str, str]:
    out = {}
    for key, value in properties.items():
        k = key.decode("utf-8", "replace") if isinstance(key, bytes) else str(key)
        v = value.decode("utf-8", "replace") if isinstance(value, bytes) else (value or "")
        out[k] = v
    return out


class Discovery:
    """Announcement + browser. One instance per process (``discovery``)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._found: dict[str, dict] = {}  # mDNS service name -> box
        self._tasks: set[asyncio.Task] = set()
        self._aiozc: AsyncZeroconf | None = None
        self._browser: AsyncServiceBrowser | None = None
        self._info: AsyncServiceInfo | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._addresses: list[str] = []
        self._announced = False
        self.node_id = ""
        self.error = ""

    # --- lifecycle --------------------------------------------------------

    async def start(
        self, *, node_id: str, name: str, version: str, port: int, announce: bool
    ) -> None:
        self._loop = asyncio.get_running_loop()
        self.node_id = node_id
        try:
            self._aiozc = AsyncZeroconf(ip_version=IPVersion.V4Only)
            self._browser = AsyncServiceBrowser(
                self._aiozc.zeroconf, [SERVICE_TYPE], handlers=[self._on_change]
            )
            self._addresses = _own_addresses()
            self._info = AsyncServiceInfo(
                SERVICE_TYPE,
                f"{name[:60]}.{SERVICE_TYPE}",
                port=port,
                properties={"id": node_id, "name": name, "v": version},
                # Own host label, so we never contend with avahi for <hostname>.local.
                server=f"ogc-{node_id[:12]}.local.",
                parsed_addresses=self._addresses,
            )
            if announce:
                await self._register()
            self._spawn(self._refresh_loop())
            self.error = ""
            logger.info("mDNS discovery running (announce=%s)", announce)
        except Exception as exc:  # noqa: BLE001 - discovery must never break startup
            self.error = str(exc) or exc.__class__.__name__
            logger.warning("mDNS discovery unavailable: %s", self.error)
            await self.stop()

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        try:
            if self._aiozc is not None:
                if self._announced and self._info is not None:
                    await self._aiozc.async_unregister_service(self._info)
                if self._browser is not None:
                    await self._browser.async_cancel()
                await self._aiozc.async_close()
        except Exception:  # noqa: BLE001 - best effort on shutdown
            logger.debug("mDNS shutdown failed", exc_info=True)
        self._aiozc = self._browser = None
        self._announced = False

    @property
    def running(self) -> bool:
        return self._aiozc is not None

    # --- announcing -------------------------------------------------------

    async def _register(self) -> None:
        if self._aiozc is None or self._info is None or self._announced:
            return
        await self._aiozc.async_register_service(self._info, allow_name_change=True)
        self._announced = True

    async def _unregister(self) -> None:
        if self._aiozc is None or self._info is None or not self._announced:
            return
        await self._aiozc.async_unregister_service(self._info)
        self._announced = False

    def set_announce(self, on: bool) -> None:
        """Switch the announcement from a sync request thread."""
        if self._loop is None or not self.running:
            return
        coro = self._register() if on else self._unregister()
        asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=10)

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(ADDRESS_REFRESH_SECONDS)
            addresses = _own_addresses()
            if addresses == self._addresses or self._info is None:
                continue
            self._addresses = addresses
            self._info.addresses = [socket.inet_aton(a) for a in addresses]
            if self._announced and self._aiozc is not None:
                try:
                    await self._aiozc.async_update_service(self._info)
                except Exception:  # noqa: BLE001
                    logger.debug("mDNS address update failed", exc_info=True)

    # --- browsing ---------------------------------------------------------

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _on_change(self, zeroconf, service_type, name, state_change) -> None:  # noqa: ARG002
        if state_change is ServiceStateChange.Removed:
            with self._lock:
                self._found.pop(name, None)
            return
        self._spawn(self._resolve(name))

    async def _resolve(self, name: str) -> None:
        if self._aiozc is None:
            return
        info = AsyncServiceInfo(SERVICE_TYPE, name)
        if not await info.async_request(self._aiozc.zeroconf, RESOLVE_TIMEOUT_MS):
            return
        txt = _txt(info.properties or {})
        self.remember(
            name,
            node_id=txt.get("id", ""),
            name=txt.get("name") or name.removesuffix(f".{SERVICE_TYPE}"),
            version=txt.get("v", ""),
            addresses=info.parsed_addresses(IPVersion.V4Only),
            port=info.port or 0,
        )

    def remember(
        self, service: str, *, node_id: str, name: str, version: str,
        addresses: list[str], port: int,
    ) -> None:
        """Record one resolved box (also the seam tests use)."""
        if not node_id or not addresses or not port:
            return
        with self._lock:
            self._found[service] = {
                "node_id": node_id,
                "name": name,
                "version": version,
                "addresses": sorted(addresses),
                "port": port,
                "seen": time.time(),
            }

    def forget_all(self) -> None:
        with self._lock:
            self._found.clear()

    def boxes(self) -> list[dict]:
        """Other boxes found in the LAN (never this one), by name."""
        with self._lock:
            found = [dict(b) for b in self._found.values() if b["node_id"] != self.node_id]
        return sorted(found, key=lambda b: b["name"].lower())

    @property
    def announced(self) -> bool:
        return self._announced


discovery = Discovery()
