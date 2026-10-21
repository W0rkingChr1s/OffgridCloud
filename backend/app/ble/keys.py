"""The box's static X25519 key (BLE-PROTOKOLL.md §5.1).

Shared by the backend (it puts the public key into pairing links) and the BLE
service (it runs the Noise handshakes). Whoever starts first creates it; the
file is written exclusively so two processes can't race to different keys.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from ..config import get_settings

KEY_FILE = "box_static.key"


def ble_dir() -> Path:
    path = get_settings().data_dir / "ble"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _raw_private(key: X25519PrivateKey) -> bytes:
    return key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())


def public_bytes(key: X25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def load_or_create_box_key() -> X25519PrivateKey:
    path = ble_dir() / KEY_FILE
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raw = path.read_bytes()
        if len(raw) != 32:
            raise RuntimeError(f"{path} ist beschädigt (erwartet 32 Byte)") from None
        return X25519PrivateKey.from_private_bytes(raw)
    key = X25519PrivateKey.generate()
    with os.fdopen(fd, "wb") as fh:
        fh.write(_raw_private(key))
    return key


def box_public_key() -> bytes:
    return public_bytes(load_or_create_box_key())


def rotate_box_key() -> bytes:
    """Replace the key. Invalidates every paired device (callers delete them)."""
    path = ble_dir() / KEY_FILE
    path.unlink(missing_ok=True)
    return box_public_key()
