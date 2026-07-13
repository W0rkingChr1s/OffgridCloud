"""Reimplementation of rclone's password *obscure* scheme.

Several rclone backends (SMB, SFTP, FTP, WebDAV, MEGA, …) store passwords
"obscured" — a reversible AES-CTR scramble with a fixed key baked into rclone.
Passing a plain password to those backends fails with

    couldn't connect: base64 decode failed when revealing password - is it obscured?

so OffgridCloud must obscure such values before handing them to rclone. This
mirrors ``rclone obscure`` (``fs/obscure/obscure.go``) exactly, so the produced
tokens are byte-for-byte compatible with rclone's ``reveal``.

Note: obscure is *obfuscation, not security* — the key is public. Real secrecy
comes from encrypting the stored config (see ``crypto.py``).
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# The fixed 256-bit key from rclone's source (fs/obscure/obscure.go).
_CRYPT_KEY = bytes(
    [
        0x9C, 0x93, 0x5B, 0x48, 0x73, 0x0A, 0x55, 0x4D,
        0x6B, 0xFD, 0x7C, 0x63, 0xC8, 0x86, 0xA9, 0x2B,
        0xD3, 0x90, 0x19, 0x8E, 0xB8, 0x12, 0x8A, 0xFB,
        0xF4, 0xDE, 0x16, 0x2B, 0x8B, 0x95, 0xF6, 0x38,
    ]
)
_BLOCK_SIZE = 16  # AES block size == CTR IV length


def obscure(plaintext: str) -> str:
    """Return the rclone-obscured form of ``plaintext`` (empty stays empty)."""
    if plaintext == "":
        return ""
    iv = os.urandom(_BLOCK_SIZE)
    encryptor = Cipher(algorithms.AES(_CRYPT_KEY), modes.CTR(iv)).encryptor()
    ciphertext = encryptor.update(plaintext.encode("utf-8")) + encryptor.finalize()
    # rclone uses base64.RawURLEncoding (URL-safe alphabet, no padding).
    return base64.urlsafe_b64encode(iv + ciphertext).decode("ascii").rstrip("=")


def reveal(token: str) -> str:
    """Inverse of :func:`obscure` — mainly for tests and parity checks."""
    if token == "":
        return ""
    padding = "=" * (-len(token) % 4)
    raw = base64.urlsafe_b64decode(token + padding)
    iv, ciphertext = raw[:_BLOCK_SIZE], raw[_BLOCK_SIZE:]
    decryptor = Cipher(algorithms.AES(_CRYPT_KEY), modes.CTR(iv)).decryptor()
    return (decryptor.update(ciphertext) + decryptor.finalize()).decode("utf-8")
