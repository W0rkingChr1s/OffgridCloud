"""Wire format of BLE-PROTOKOLL.md v1: constants, framing (§4), CBOR (§7.1).

Kept free of I/O so the same code is exercised by the tests and the service.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cbor2

PROTOCOL_VERSION = 1

SERVICE_UUID = "90c00001-59bd-434e-b548-6113f2f006f9"
INFO_CHAR_UUID = "90c00002-59bd-434e-b548-6113f2f006f9"
RX_CHAR_UUID = "90c00003-59bd-434e-b548-6113f2f006f9"
TX_CHAR_UUID = "90c00004-59bd-434e-b548-6113f2f006f9"
MANUFACTURER_ID = 0xFFFF
ADVERT_NAME_PREFIX = "OGC-"

FLAG_SETUP_PENDING = 0x01
FLAG_PAIRING_OPEN = 0x02
FLAG_BACKEND_UP = 0x04

FRAME_HEADER = 5
ATT_OVERHEAD = 3
MIN_MTU = 23
MAX_MESSAGE = 65_535
FRAME_FIRST = 0x01
FRAME_LAST = 0x02
FRAME_EVENT = 0x04
FRAME_HANDSHAKE = 0x08
FRAME_RESERVED = 0xF0
REASSEMBLY_TIMEOUT = 10.0


class FramingError(Exception):
    """Protocol violation in the frame stream (§4 rule 5)."""


@dataclass
class Message:
    msg_id: int
    payload: bytes
    event: bool = False
    handshake: bool = False


def split_message(msg: Message, mtu: int) -> list[bytes]:
    if len(msg.payload) > MAX_MESSAGE:
        raise FramingError(f"Nachricht mit {len(msg.payload)} Byte über dem Limit")
    chunk = max(mtu, MIN_MTU) - ATT_OVERHEAD - FRAME_HEADER
    count = max(1, -(-len(msg.payload) // chunk))
    frames = []
    for seq in range(count):
        flags = 0
        if seq == 0:
            flags |= FRAME_FIRST
        if seq == count - 1:
            flags |= FRAME_LAST
        if msg.event:
            flags |= FRAME_EVENT
        if msg.handshake:
            flags |= FRAME_HANDSHAKE
        part = msg.payload[seq * chunk : (seq + 1) * chunk]
        header = (msg.msg_id & 0xFFFF).to_bytes(2, "big") + seq.to_bytes(2, "big")
        frames.append(header + bytes([flags]) + part)
    return frames


@dataclass
class _Pending:
    next_seq: int
    event: bool
    handshake: bool
    parts: list[bytes]
    size: int
    started: float


class Reassembler:
    """Frames → messages. Raises ``FramingError`` on any rule violation."""

    def __init__(self, timeout: float = REASSEMBLY_TIMEOUT) -> None:
        self._timeout = timeout
        self._pending: dict[int, _Pending] = {}

    def push(self, frame: bytes, now: float) -> Message | None:
        for msg_id, p in list(self._pending.items()):
            if now - p.started > self._timeout:
                del self._pending[msg_id]
        if len(frame) < FRAME_HEADER:
            raise FramingError("Frame kürzer als der Header")
        msg_id = int.from_bytes(frame[0:2], "big")
        seq = int.from_bytes(frame[2:4], "big")
        flags = frame[4]
        part = bytes(frame[FRAME_HEADER:])
        if flags & FRAME_RESERVED:
            self._pending.pop(msg_id, None)
            raise FramingError(f"Reservierte Flags gesetzt: {flags}")
        entry = self._pending.get(msg_id)
        if flags & FRAME_FIRST:
            if seq != 0:
                self._pending.pop(msg_id, None)
                raise FramingError(f"Erster Frame mit seq {seq}")
            entry = _Pending(
                0, bool(flags & FRAME_EVENT), bool(flags & FRAME_HANDSHAKE), [], 0, now
            )
            self._pending[msg_id] = entry
        if entry is None or seq != entry.next_seq:
            self._pending.pop(msg_id, None)
            raise FramingError(f"Frame {seq} von Nachricht {msg_id} außer der Reihe")
        entry.size += len(part)
        if entry.size > MAX_MESSAGE:
            del self._pending[msg_id]
            raise FramingError(f"Nachricht {msg_id} über dem Limit")
        entry.parts.append(part)
        entry.next_seq += 1
        if not flags & FRAME_LAST:
            return None
        del self._pending[msg_id]
        return Message(msg_id, b"".join(entry.parts), entry.event, entry.handshake)


class CborError(Exception):
    pass


def encode(value: Any) -> bytes:
    return cbor2.dumps(value)


def decode_map(data: bytes) -> dict[str, Any]:
    try:
        value = cbor2.loads(data)
    except Exception as exc:  # cbor2 raises several types for bad input
        raise CborError(f"Ungültiges CBOR: {exc}") from None
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise CborError("Nachricht ist keine Map mit Text-Schlüsseln")
    return value


def build_info(name: str, flags: int, public_key: bytes) -> bytes:
    """Value of the unencrypted ``info`` characteristic (§3.2), at most 120 bytes."""
    raw = name.encode("utf-8")[:32].decode("utf-8", "ignore")
    return encode({"v": PROTOCOL_VERSION, "name": raw, "flags": flags, "pk": public_key})


def advert_name(box_name: str) -> str:
    """``OGC-`` plus the name, at most 29 bytes UTF-8 (§3.1 scan response)."""
    raw = (ADVERT_NAME_PREFIX + box_name).encode("utf-8")[:29]
    return raw.decode("utf-8", "ignore")
