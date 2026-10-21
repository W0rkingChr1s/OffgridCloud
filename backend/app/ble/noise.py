"""Noise IK / IKpsk2 with 25519, ChaChaPoly and SHA256 (BLE-PROTOKOLL.md §5).

Only the two patterns the protocol uses, both roles, built on the
``cryptography`` primitives the backend already depends on. Checked against
``docs/ble/testvectors.json`` (generated with the independent ``noiseprotocol``
package) and against the companion app's implementation of the same vectors.

    <- s
    ...
    -> e, es, s, ss
    <- e, ee, se          (IKpsk2: psk at the end)
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

PROLOGUE = b"OGC-BLE/1"
SUITE_PAIR = b"Noise_IKpsk2_25519_ChaChaPoly_SHA256"
SUITE_SESSION = b"Noise_IK_25519_ChaChaPoly_SHA256"
PSK_INFO = b"OGC-BLE/1 pair"
DHLEN = 32
TAGLEN = 16


class NoiseError(Exception):
    pass


def public_bytes(key: X25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def derive_pairing_psk(secret: bytes, box_public_key: bytes) -> bytes:
    """psk from the pairing code's secret (§6.1)."""
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=box_public_key, info=PSK_INFO).derive(
        secret
    )


def _hkdf(ck: bytes, ikm: bytes) -> tuple[bytes, bytes, bytes]:
    temp = hmac.new(ck, ikm, hashlib.sha256).digest()
    o1 = hmac.new(temp, b"\x01", hashlib.sha256).digest()
    o2 = hmac.new(temp, o1 + b"\x02", hashlib.sha256).digest()
    o3 = hmac.new(temp, o2 + b"\x03", hashlib.sha256).digest()
    return o1, o2, o3


def _dh(private: X25519PrivateKey, public: bytes) -> bytes:
    try:
        return private.exchange(X25519PublicKey.from_public_bytes(public))
    except ValueError:  # malformed or low-order point
        raise NoiseError("Ungültiger öffentlicher Schlüssel") from None


class CipherState:
    def __init__(self, key: bytes | None = None) -> None:
        self._aead = ChaCha20Poly1305(key) if key else None
        self.n = 0

    @property
    def has_key(self) -> bool:
        return self._aead is not None

    def _nonce(self) -> bytes:
        return b"\x00\x00\x00\x00" + self.n.to_bytes(8, "little")

    def encrypt(self, ad: bytes, plaintext: bytes) -> bytes:
        if self._aead is None:
            return plaintext
        out = self._aead.encrypt(self._nonce(), plaintext, ad)
        self.n += 1
        return out

    def decrypt(self, ad: bytes, ciphertext: bytes) -> bytes:
        if self._aead is None:
            return ciphertext
        try:
            out = self._aead.decrypt(self._nonce(), ciphertext, ad)
        except InvalidTag:
            raise NoiseError("Entschlüsselung fehlgeschlagen") from None
        self.n += 1
        return out


class _Symmetric:
    def __init__(self, name: bytes) -> None:
        self.h = name.ljust(32, b"\x00") if len(name) <= 32 else hashlib.sha256(name).digest()
        self.ck = self.h
        self.cipher = CipherState()

    def mix_hash(self, data: bytes) -> None:
        self.h = hashlib.sha256(self.h + data).digest()

    def mix_key(self, ikm: bytes) -> None:
        self.ck, k, _ = _hkdf(self.ck, ikm)
        self.cipher = CipherState(k)

    def mix_key_and_hash(self, ikm: bytes) -> None:
        self.ck, th, k = _hkdf(self.ck, ikm)
        self.mix_hash(th)
        self.cipher = CipherState(k)

    def encrypt_and_hash(self, plaintext: bytes) -> bytes:
        c = self.cipher.encrypt(self.h, plaintext)
        self.mix_hash(c)
        return c

    def decrypt_and_hash(self, ciphertext: bytes) -> bytes:
        p = self.cipher.decrypt(self.h, ciphertext)
        self.mix_hash(ciphertext)
        return p


@dataclass
class Transport:
    send: CipherState
    recv: CipherState
    handshake_hash: bytes

    def encrypt(self, plaintext: bytes) -> bytes:
        return self.send.encrypt(b"", plaintext)

    def decrypt(self, ciphertext: bytes) -> bytes:
        return self.recv.decrypt(b"", ciphertext)


class Handshake:
    """One side of an IK / IKpsk2 handshake (two messages)."""

    def __init__(
        self,
        *,
        initiator: bool,
        static_key: X25519PrivateKey,
        remote_static: bytes | None = None,
        psk: bytes | None = None,
        ephemeral_key: X25519PrivateKey | None = None,
    ) -> None:
        if psk is not None and len(psk) != 32:
            raise NoiseError("psk muss 32 Byte lang sein")
        if initiator and remote_static is None:
            raise NoiseError("Initiator braucht den Box-Schlüssel")
        self.initiator = initiator
        self._s = static_key
        self._e = ephemeral_key
        self._psk = psk
        self.remote_static = remote_static
        self._re: bytes | None = None
        self._step = 0
        self._ss = _Symmetric(SUITE_PAIR if psk is not None else SUITE_SESSION)
        self._ss.mix_hash(PROLOGUE)
        # Pre-message <- s: the responder's (box's) static key.
        self._ss.mix_hash(remote_static if initiator else public_bytes(static_key))

    def set_psk(self, psk: bytes) -> None:
        """Responder, pairing: msg 1 does not depend on the psk (§5.3), so the
        box reads it with a placeholder, looks up the ``pid`` and then sets it."""
        if self._psk is None or len(psk) != 32 or self._step > 1:
            raise NoiseError("psk nur bei IKpsk2 und vor Nachricht 2 setzbar")
        self._psk = psk

    def _expect(self, initiator: bool, step: int) -> None:
        if self.initiator != initiator or self._step != step:
            raise NoiseError("Handshake-Schritt in falscher Reihenfolge")
        self._step += 1

    def _write_e(self) -> bytes:
        if self._e is None:
            self._e = X25519PrivateKey.generate()
        pub = public_bytes(self._e)
        self._ss.mix_hash(pub)
        if self._psk is not None:
            self._ss.mix_key(pub)
        return pub

    def _read_e(self, re: bytes) -> None:
        self._re = re
        self._ss.mix_hash(re)
        if self._psk is not None:
            self._ss.mix_key(re)

    def write_message1(self, payload: bytes) -> bytes:
        self._expect(True, 0)
        e = self._write_e()
        self._ss.mix_key(_dh(self._e, self.remote_static))  # es
        s = self._ss.encrypt_and_hash(public_bytes(self._s))
        self._ss.mix_key(_dh(self._s, self.remote_static))  # ss
        return e + s + self._ss.encrypt_and_hash(payload)

    def read_message1(self, message: bytes) -> bytes:
        self._expect(False, 0)
        if len(message) < DHLEN + DHLEN + TAGLEN + TAGLEN:
            raise NoiseError("Nachricht 1 zu kurz")
        self._read_e(message[:DHLEN])
        self._ss.mix_key(_dh(self._s, self._re))  # es
        s_end = DHLEN + DHLEN + TAGLEN
        self.remote_static = self._ss.decrypt_and_hash(message[DHLEN:s_end])
        self._ss.mix_key(_dh(self._s, self.remote_static))  # ss
        return self._ss.decrypt_and_hash(message[s_end:])

    def write_message2(self, payload: bytes) -> bytes:
        self._expect(False, 1)
        e = self._write_e()
        self._ss.mix_key(_dh(self._e, self._re))  # ee
        self._ss.mix_key(_dh(self._e, self.remote_static))  # se
        if self._psk is not None:
            self._ss.mix_key_and_hash(self._psk)
        return e + self._ss.encrypt_and_hash(payload)

    def read_message2(self, message: bytes) -> bytes:
        self._expect(True, 1)
        if len(message) < DHLEN + TAGLEN:
            raise NoiseError("Nachricht 2 zu kurz")
        self._read_e(message[:DHLEN])
        self._ss.mix_key(_dh(self._e, self._re))  # ee
        self._ss.mix_key(_dh(self._s, self._re))  # se
        if self._psk is not None:
            self._ss.mix_key_and_hash(self._psk)
        return self._ss.decrypt_and_hash(message[DHLEN:])

    def split(self) -> Transport:
        if self._step != 2:
            raise NoiseError("Handshake nicht abgeschlossen")
        k1, k2, _ = _hkdf(self._ss.ck, b"")
        c1, c2 = CipherState(k1), CipherState(k2)
        if self.initiator:
            return Transport(c1, c2, self._ss.h)
        return Transport(c2, c1, self._ss.h)
