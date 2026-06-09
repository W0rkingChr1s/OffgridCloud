"""Symmetric encryption for provider credentials at rest.

The key is derived from ``OGC_SECRET_KEY`` — so rotating that env value
invalidates stored secrets (they must be re-entered). Keep it stable and backed
up in production.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(get_settings().secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        # Wrong key or corrupted data — surface as empty so callers can re-test.
        return ""
