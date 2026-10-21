"""Devices and pairing codes (BLE-PROTOKOLL.md §6, §10).

Used by the web API (create codes, list/revoke devices) and by the BLE service
in its own process (look up codes and devices, register a device after key
confirmation). Both talk to the same SQLite database.
"""

from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..crypto import decrypt, encrypt
from ..models import BleDevice, BlePairing, Role, User

PAIRING_TTL = timedelta(minutes=5)
MAX_HANDSHAKES_PER_CODE = 3
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _now() -> datetime:
    # Naive UTC, like the rest of the models.
    return datetime.now(UTC).replace(tzinfo=None)


def format_code(pid: int, secret: bytes) -> str:
    """20 Crockford-Base32 characters in groups of five (§6.1)."""
    value = (pid << 80) | int.from_bytes(secret, "big")
    chars = "".join(CROCKFORD[(value >> (5 * i)) & 31] for i in reversed(range(20)))
    return "-".join(chars[i : i + 5] for i in range(0, 20, 5))


def pairing_link(code: str, box_public_key: bytes, box_name: str) -> str:
    pk = base64.urlsafe_b64encode(box_public_key).rstrip(b"=").decode("ascii")
    return f"offgridcloud://pair?v=1&c={code.replace('-', '')}&pk={pk}&n={quote(box_name)}"


@dataclass
class NewPairing:
    pid: int
    code: str
    expires_at: datetime


def purge_expired(db: Session) -> None:
    db.execute(
        delete(BlePairing).where((BlePairing.expires_at < _now()) | BlePairing.used.is_(True))
    )


def create_pairing(db: Session, user: User) -> NewPairing:
    purge_expired(db)
    while True:
        pid = secrets.randbits(20)
        if db.scalar(select(BlePairing).where(BlePairing.pid == pid)) is None:
            break
    secret = secrets.token_bytes(10)
    row = BlePairing(
        pid=pid,
        secret_encrypted=encrypt(secret.hex()),
        user_id=user.id,
        expires_at=_now() + PAIRING_TTL,
    )
    db.add(row)
    db.commit()
    return NewPairing(pid=pid, code=format_code(pid, secret), expires_at=row.expires_at)


def pairing_open(db: Session) -> bool:
    return (
        db.scalar(
            select(BlePairing.id).where(
                BlePairing.expires_at >= _now(), BlePairing.used.is_(False)
            )
        )
        is not None
    )


@dataclass
class PairingClaim:
    pairing_id: int
    secret: bytes
    user_id: int


def claim_pairing(db: Session, pid: int) -> PairingClaim | None:
    """Count one handshake against an open code and return its secret.

    ``None`` if the code is unknown, expired, used or out of attempts.
    """
    row = db.scalar(select(BlePairing).where(BlePairing.pid == pid))
    if (
        row is None
        or row.used
        or row.expires_at < _now()
        or row.attempts >= MAX_HANDSHAKES_PER_CODE
    ):
        return None
    row.attempts += 1
    db.commit()
    return PairingClaim(row.id, bytes.fromhex(decrypt(row.secret_encrypted)), row.user_id)


@dataclass
class DeviceInfo:
    id: int
    name: str
    user_id: int
    user_email: str
    role: str


def _info(device: BleDevice) -> DeviceInfo:
    return DeviceInfo(
        id=device.id,
        name=device.name,
        user_id=device.user_id,
        user_email=device.user.email,
        role="admin" if device.user.role == Role.ADMIN else "user",
    )


def confirm_pairing(
    db: Session, claim: PairingClaim, public_key: bytes, name: str
) -> DeviceInfo | None:
    """Key confirmation succeeded (§6.3): register the device, burn the code."""
    row = db.get(BlePairing, claim.pairing_id)
    user = db.get(User, claim.user_id)
    if row is None or row.used or user is None or not user.active:
        return None
    row.used = True
    existing = db.scalar(select(BleDevice).where(BleDevice.public_key == public_key.hex()))
    if existing is not None:
        db.delete(existing)
        db.flush()
    device = BleDevice(
        user_id=user.id,
        name=(name.strip() or "Gerät")[:64],
        public_key=public_key.hex(),
        last_seen_at=_now(),
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return _info(device)


def find_device(db: Session, public_key: bytes) -> DeviceInfo | None:
    """A paired device whose user is still active, or ``None``."""
    device = db.scalar(select(BleDevice).where(BleDevice.public_key == public_key.hex()))
    if device is None or not device.user.active:
        return None
    device.last_seen_at = _now()
    db.commit()
    return _info(device)


def device_still_valid(db: Session, device_id: int) -> bool:
    device = db.get(BleDevice, device_id)
    return device is not None and device.user.active
