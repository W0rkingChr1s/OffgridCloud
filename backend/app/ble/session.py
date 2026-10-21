"""One BLE connection: handshake, transport, RPC (BLE-PROTOKOLL.md §5–§8).

Transport-agnostic: the GATT layer feeds received frames into ``receive`` and
the session sends frames through ``send``. Everything that touches the
database, the REST API or the system goes through ``Host`` so the session can
be tested end to end without a radio.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from . import noise, protocol
from .registry import DeviceInfo, PairingClaim
from .whitelist import METHODS, api_allowed

log = logging.getLogger("offgridcloud.ble")

MAX_IN_FLIGHT = 4
RATE_WINDOW = 10.0
RATE_LIMIT = 20
IDLE_TIMEOUT = 300.0
PAIR_CONFIRM_TIMEOUT = 30.0
REVOCATION_CHECK = 5.0
STATUS_INTERVAL = 3.0
NOTICE_TEXT = 120
# Shortest valid message 2 is 48 bytes; rejections stay below (§5.3).
MAX_REJECTION = 47


class Host(Protocol):
    """What a session needs from the box. Implemented in ``bridge.py``."""

    box_key: X25519PrivateKey

    def box_name(self) -> str: ...
    def ble_enabled(self) -> bool: ...
    async def find_device(self, public_key: bytes) -> DeviceInfo | None: ...
    async def claim_pairing(self, pid: int) -> PairingClaim | None: ...
    async def confirm_pairing(
        self, claim: PairingClaim, public_key: bytes, name: str
    ) -> DeviceInfo | None: ...
    async def device_still_valid(self, device_id: int) -> bool: ...
    async def forward(
        self,
        device: DeviceInfo,
        method: str,
        path: str,
        query: dict[str, str] | None,
        body: Any,
    ) -> tuple[int, Any]: ...
    async def health(self) -> dict[str, Any]: ...
    async def restart_backend(self) -> None: ...
    async def status_snapshot(self, device: DeviceInfo) -> dict[str, Any] | None: ...
    async def audit(self, device: DeviceInfo, action: str, detail: str) -> None: ...


class BridgeError(Exception):
    """Answered as ``status 0`` + ``error`` (§7.3)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class BackendUnavailable(Exception):
    pass


class NotJson(Exception):
    pass


@dataclass
class Guard:
    """Box-wide lockouts against guessing (§8). Shared by all sessions."""

    clock: Callable[[], float] = time.monotonic
    _unknown_pid: deque[float] = field(default_factory=deque)
    _unknown_device: deque[float] = field(default_factory=deque)
    _locked_until: float = 0.0

    def locked(self) -> bool:
        return self.clock() < self._locked_until

    def _count(self, hits: deque[float], limit: int) -> None:
        now = self.clock()
        hits.append(now)
        while hits and now - hits[0] > 60.0:
            hits.popleft()
        if len(hits) >= limit:
            hits.clear()
            self._locked_until = now + 60.0

    def unknown_pid(self) -> None:
        self._count(self._unknown_pid, 5)

    def unknown_device(self) -> None:
        self._count(self._unknown_device, 10)


class Session:
    HANDSHAKE, PAIRING, OPEN, CLOSED = "handshake", "pairing", "open", "closed"

    def __init__(
        self,
        host: Host,
        guard: Guard,
        send: Callable[[bytes], Awaitable[None]],
        mtu: Callable[[], int],
        on_close: Callable[[str], Awaitable[None]],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.host = host
        self.guard = guard
        self._send = send
        self._mtu = mtu
        self._on_close = on_close
        self._clock = clock
        self.state = self.HANDSHAKE
        self.device: DeviceInfo | None = None
        self._reassembler = protocol.Reassembler()
        self._transport: noise.Transport | None = None
        self._send_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._in_flight = 0
        self._requests: deque[float] = deque()
        self._last_activity = clock()
        self._opened_at = clock()
        self._claim: PairingClaim | None = None
        self._pair_key: bytes = b""
        self._pair_name = ""
        self._status_task: asyncio.Task | None = None
        self._event_id = 0
        self._monitor = asyncio.create_task(self._watch())

    # --- incoming ----------------------------------------------------------

    async def receive(self, frame: bytes) -> None:
        if self.state == self.CLOSED:
            return
        try:
            msg = self._reassembler.push(frame, self._clock())
        except protocol.FramingError as exc:
            await self.close(f"Framing: {exc}")
            return
        if msg is None:
            return
        if self.state == self.HANDSHAKE:
            if not msg.handshake or msg.msg_id != 0:
                await self.close("Nachricht vor dem Handshake")
                return
            await self._handshake(msg.payload)
            return
        if msg.handshake or msg.event:
            await self.close("Handshake/Event-Nachricht in der Transportphase")
            return
        try:
            plaintext = self._transport.decrypt(msg.payload)
        except noise.NoiseError:
            await self.close("Entschlüsselung fehlgeschlagen")
            return
        self._last_activity = self._clock()
        # Counted here, synchronously, so a burst of frames can't slip past
        # the in-flight limit before the first handler runs.
        self._in_flight += 1
        self._spawn(self._handle_request(plaintext))

    # --- handshake (§5.3, §6.3) -------------------------------------------

    async def _reject(self, code: str) -> None:
        payload = protocol.encode({"error": code})
        assert len(payload) <= MAX_REJECTION
        for frame in protocol.split_message(
            protocol.Message(0, payload, handshake=True), self._mtu()
        ):
            await self._send(frame)
        await self.close(f"abgelehnt: {code}")

    async def _handshake(self, message1: bytes) -> None:
        if not self.host.ble_enabled():
            await self._reject("ble_disabled")
            return
        if self.guard.locked():
            await self._reject("locked")
            return
        key = self.host.box_key

        # Session (IK): an already paired device.
        hs = noise.Handshake(initiator=False, static_key=key)
        try:
            p1 = protocol.decode_map(hs.read_message1(message1))
        except (noise.NoiseError, protocol.CborError):
            p1 = None
        if p1 is not None:
            if p1.get("mode") != "session":
                await self.close("IK ohne mode=session")
                return
            device = await self.host.find_device(hs.remote_static)
            if device is None:
                self.guard.unknown_device()
                await self._reject("unknown_device")
                return
            self.device = device
            await self._finish_handshake(
                hs,
                {
                    "v": protocol.PROTOCOL_VERSION,
                    "dev_id": device.id,
                    "user": device.user_email,
                    "role": device.role,
                },
                self.OPEN,
            )
            log.info("BLE-Sitzung: %s (%s)", device.name, device.user_email)
            return

        # Pairing (IKpsk2): msg 1 is independent of the psk; pick it by pid.
        hs = noise.Handshake(initiator=False, static_key=key, psk=bytes(32))
        try:
            p1 = protocol.decode_map(hs.read_message1(message1))
        except (noise.NoiseError, protocol.CborError):
            await self.close("Nachricht 1 unlesbar")
            return
        pid = p1.get("pid")
        if p1.get("mode") != "pair" or not isinstance(pid, int):
            await self.close("IKpsk2 ohne mode=pair/pid")
            return
        claim = await self.host.claim_pairing(pid)
        if claim is None:
            self.guard.unknown_pid()
            await self._reject("pairing_invalid")
            return
        hs.set_psk(noise.derive_pairing_psk(claim.secret, noise.public_bytes(key)))
        self._claim = claim
        self._pair_key = hs.remote_static
        dev = p1.get("dev")
        self._pair_name = dev if isinstance(dev, str) else ""
        await self._finish_handshake(
            hs, {"v": protocol.PROTOCOL_VERSION, "name": self.host.box_name()}, self.PAIRING
        )

    async def _finish_handshake(self, hs: noise.Handshake, payload: dict, state: str) -> None:
        message2 = hs.write_message2(protocol.encode(payload))
        self._transport = hs.split()
        self.state = state
        self._opened_at = self._last_activity = self._clock()
        for frame in protocol.split_message(
            protocol.Message(0, message2, handshake=True), self._mtu()
        ):
            await self._send(frame)

    # --- requests (§7) -----------------------------------------------------

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send_message(self, msg_id: int, value: dict, event: bool = False) -> None:
        plaintext = protocol.encode(value)
        if len(plaintext) + noise.TAGLEN > protocol.MAX_MESSAGE and not event:
            plaintext = protocol.encode(
                {"id": value["id"], "status": 0, "body": None, "error": "too_large"}
            )
        async with self._send_lock:
            if self.state == self.CLOSED:
                return
            # Encrypt inside the lock: nonce order == send order (§4 rule 3).
            ciphertext = self._transport.encrypt(plaintext)
            for frame in protocol.split_message(
                protocol.Message(msg_id, ciphertext, event=event), self._mtu()
            ):
                await self._send(frame)

    async def _reply(self, rid: int, status: int, body: Any = None, error: str = "") -> None:
        value: dict[str, Any] = {"id": rid, "status": status, "body": body}
        if error:
            value["error"] = error
        await self._send_message(rid, value)

    async def _handle_request(self, plaintext: bytes) -> None:
        try:
            await self._process(plaintext)
        finally:
            self._in_flight -= 1

    async def _process(self, plaintext: bytes) -> None:
        try:
            req = protocol.decode_map(plaintext)
        except protocol.CborError:
            return  # authentic but unreadable: nothing to answer
        rid = req.get("id")
        if not isinstance(rid, int) or not 1 <= rid <= 0xFFFF:
            return
        method, path = req.get("method"), req.get("path")
        query, body = req.get("query"), req.get("body")
        if (
            method not in METHODS
            or not isinstance(path, str)
            or (query is not None and not isinstance(query, dict))
        ):
            await self._reply(rid, 0, error="bad_request")
            return
        now = self._clock()
        while self._requests and now - self._requests[0] > RATE_WINDOW:
            self._requests.popleft()
        if len(self._requests) >= RATE_LIMIT:
            await self._reply(rid, 0, error="rate_limited")
            return
        self._requests.append(now)
        if self._in_flight > MAX_IN_FLIGHT:
            await self._reply(rid, 0, error="busy")
            return
        try:
            status, out = await self._dispatch(method, path, query, body)
            await self._reply(rid, status, out)
        except BridgeError as exc:
            await self._reply(rid, 0, error=exc.code)
        except BackendUnavailable:
            await self._reply(rid, 0, error="backend_unavailable")
        except NotJson:
            await self._reply(rid, 0, error="bad_request")
        except Exception:
            log.exception("BLE-Anfrage %s %s fehlgeschlagen", method, path)
            await self._reply(rid, 0, error="backend_unavailable")

    async def _dispatch(
        self, method: str, path: str, query: dict | None, body: Any
    ) -> tuple[int, Any]:
        if self.state == self.PAIRING:
            if (method, path) != ("POST", "/ble/pair/confirm"):
                raise BridgeError("not_allowed")
            return await self._confirm_pairing()
        device = self.device
        if path.startswith("/ble/"):
            return await self._local(device, method, path, body)
        if not api_allowed(method, path):
            raise BridgeError("not_allowed")
        if method != "GET":
            await self.host.audit(device, "ble.request", f"{method} {path} ({device.name})")
        return await self.host.forward(device, method, path, query, body)

    async def _confirm_pairing(self) -> tuple[int, Any]:
        device = await self.host.confirm_pairing(self._claim, self._pair_key, self._pair_name)
        if device is None:
            raise BridgeError("not_allowed")
        self.device = device
        self.state = self.OPEN
        self._claim = None
        await self.host.audit(device, "ble.pair", f"{device.name} gekoppelt")
        log.info("BLE gekoppelt: %s (%s)", device.name, device.user_email)
        return 200, {"dev_id": device.id, "user": device.user_email, "role": device.role}

    async def _local(
        self, device: DeviceInfo, method: str, path: str, body: Any
    ) -> tuple[int, Any]:
        topics = body.get("topics") if isinstance(body, dict) else None
        if (method, path) == ("GET", "/ble/health"):
            return 200, await self.host.health()
        if (method, path) == ("POST", "/ble/subscribe"):
            if not isinstance(topics, list):
                raise BridgeError("bad_request")
            if "status" in topics and self._status_task is None:
                self._status_task = asyncio.create_task(self._status_loop())
            return 200, {"topics": ["status"] if self._status_task else []}
        if (method, path) == ("POST", "/ble/unsubscribe"):
            if not isinstance(topics, list):
                raise BridgeError("bad_request")
            if "status" in topics and self._status_task is not None:
                self._status_task.cancel()
                self._status_task = None
            return 200, {"topics": ["status"] if self._status_task else []}
        if (method, path) == ("POST", "/ble/backend/restart"):
            if device.role != "admin":
                return 403, {"detail": "Administrator privileges required"}
            await self.host.audit(device, "ble.backend_restart", device.name)
            await self.host.restart_backend()
            return 200, {"ok": True}
        raise BridgeError("not_allowed")

    # --- events (§7.7) -----------------------------------------------------

    async def _status_loop(self) -> None:
        last = None
        while self.state == self.OPEN:
            snapshot = await self.host.status_snapshot(self.device)
            if snapshot is not None and snapshot != last:
                last = snapshot
                self._event_id = (self._event_id % 0xFFFF) + 1
                await self._send_message(
                    self._event_id, {"event": "status", "body": snapshot}, event=True
                )
            await asyncio.sleep(STATUS_INTERVAL)

    # --- lifecycle ---------------------------------------------------------

    async def _watch(self) -> None:
        """Idle timeout, pending key confirmation and revocation (§6.3, §6.4, §8)."""
        next_revocation_check = self._clock() + REVOCATION_CHECK
        while self.state != self.CLOSED:
            await asyncio.sleep(1.0)
            now = self._clock()
            if self.state == self.HANDSHAKE and now - self._opened_at > IDLE_TIMEOUT:
                await self.close("kein Handshake")
            elif self.state == self.PAIRING and now - self._opened_at > PAIR_CONFIRM_TIMEOUT:
                await self.close("Kopplung nicht bestätigt")
            elif self.state == self.OPEN and now - self._last_activity > IDLE_TIMEOUT:
                await self.close("Leerlauf")
            elif self.state == self.OPEN and now >= next_revocation_check:
                next_revocation_check = now + REVOCATION_CHECK
                if not await self.host.device_still_valid(self.device.id):
                    await self.close("Gerät widerrufen")

    async def close(self, reason: str) -> None:
        if self.state == self.CLOSED:
            return
        self.state = self.CLOSED
        log.info("BLE-Verbindung beendet: %s", reason)
        current = asyncio.current_task()
        for task in [self._monitor, self._status_task, *self._tasks]:
            if task is not None and task is not current:
                task.cancel()
        with contextlib.suppress(Exception):
            await self._on_close(reason)
