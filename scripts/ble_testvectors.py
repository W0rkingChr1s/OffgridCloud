#!/usr/bin/env python3
"""Erzeugt die Testvektoren zu ``docs/BLE-PROTOKOLL.md``.

Deterministisch: alle Schlüssel (statisch und ephemer) sind aus festen Labels
abgeleitet, damit Box-Dienst und Companion-App byteweise gegen dieselben Werte
testen können. Ausgabe: ``docs/ble/testvectors.json``.

Usage::

    pip install noiseprotocol==0.3.1 cbor2 cryptography
    python3 scripts/ble_testvectors.py            # schreibt die JSON-Datei
    python3 scripts/ble_testvectors.py --check    # vergleicht nur, Exit 1 bei Abweichung

Die Schlüssel hier sind öffentlich und nur für Tests — nie produktiv verwenden.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import warnings
from pathlib import Path

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from noise.connection import Keypair, NoiseConnection

# Feste ephemere Schlüssel sind hier gewollt (deterministische Vektoren).
warnings.filterwarnings("ignore", message="One of ephemeral keypairs")

OUT = Path(__file__).resolve().parent.parent / "docs" / "ble" / "testvectors.json"

PROLOGUE = b"OGC-BLE/1"
PAIR_SUITE = b"Noise_IKpsk2_25519_ChaChaPoly_SHA256"
SESSION_SUITE = b"Noise_IK_25519_ChaChaPoly_SHA256"
PSK_INFO = b"OGC-BLE/1 pair"
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

FLAG_FIRST, FLAG_LAST, FLAG_EVENT, FLAG_HANDSHAKE = 0x01, 0x02, 0x04, 0x08
HEADER = 5
ATT_OVERHEAD = 3


def key(label: str) -> bytes:
    """Fester 32-Byte-Privatschlüssel aus einem Label (nur für Testvektoren)."""
    return hashlib.sha256(b"OGC-BLE test key: " + label.encode()).digest()


def pub(priv: bytes) -> bytes:
    return (
        X25519PrivateKey.from_private_bytes(priv)
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
    )


def split(msg_id: int, flags: int, payload: bytes, mtu: int) -> list[bytes]:
    chunk = mtu - ATT_OVERHEAD - HEADER
    count = max(1, -(-len(payload) // chunk))
    frames = []
    for seq in range(count):
        f = flags
        if seq == 0:
            f |= FLAG_FIRST
        if seq == count - 1:
            f |= FLAG_LAST
        part = payload[seq * chunk : (seq + 1) * chunk]
        frames.append(msg_id.to_bytes(2, "big") + seq.to_bytes(2, "big") + bytes([f]) + part)
    return frames


def crockford(value: int, length: int) -> str:
    return "".join(CROCKFORD[(value >> (5 * i)) & 31] for i in reversed(range(length)))


def pairing_vector(box_pk: bytes) -> dict:
    pid = 0x5A3C1
    secret = bytes.fromhex("0f1e2d3c4b5a69788796")
    value = (pid << 80) | int.from_bytes(secret, "big")
    code = crockford(value, 20)
    psk = HKDF(algorithm=hashes.SHA256(), length=32, salt=box_pk, info=PSK_INFO).derive(secret)
    return {
        "pid": pid,
        "secret": secret.hex(),
        "code": "-".join(code[i : i + 5] for i in range(0, 20, 5)),
        "box_static_public": box_pk.hex(),
        "psk": psk.hex(),
    }


def handshake(suite: bytes, app_s: bytes, app_e: bytes, box_s: bytes, box_e: bytes,
              psk: bytes | None, p1: dict, p2: dict, req: dict, res: dict) -> dict:
    app = NoiseConnection.from_name(suite)
    app.set_as_initiator()
    app.set_prologue(PROLOGUE)
    app.set_keypair_from_private_bytes(Keypair.STATIC, app_s)
    app.set_keypair_from_private_bytes(Keypair.EPHEMERAL, app_e)
    app.set_keypair_from_public_bytes(Keypair.REMOTE_STATIC, pub(box_s))

    box = NoiseConnection.from_name(suite)
    box.set_as_responder()
    box.set_prologue(PROLOGUE)
    box.set_keypair_from_private_bytes(Keypair.STATIC, box_s)
    box.set_keypair_from_private_bytes(Keypair.EPHEMERAL, box_e)
    if psk is not None:
        app.set_psks(psk)
        box.set_psks(psk)

    app.start_handshake()
    box.start_handshake()
    p1b, p2b = cbor2.dumps(p1), cbor2.dumps(p2)
    m1 = app.write_message(p1b)
    assert box.read_message(m1) == p1b
    m2 = box.write_message(p2b)
    assert app.read_message(m2) == p2b
    assert app.get_handshake_hash() == box.get_handshake_hash()

    reqb, resb = cbor2.dumps(req), cbor2.dumps(res)
    c_req = app.encrypt(reqb)
    assert box.decrypt(c_req) == reqb
    c_res = box.encrypt(resb)
    assert app.decrypt(c_res) == resb

    return {
        "suite": suite.decode(),
        "prologue": PROLOGUE.hex(),
        "app_static_private": app_s.hex(),
        "app_static_public": pub(app_s).hex(),
        "app_ephemeral_private": app_e.hex(),
        "box_static_private": box_s.hex(),
        "box_static_public": pub(box_s).hex(),
        "box_ephemeral_private": box_e.hex(),
        "psk": psk.hex() if psk else None,
        "msg1_payload": p1b.hex(),
        "msg1": m1.hex(),
        "msg2_payload": p2b.hex(),
        "msg2": m2.hex(),
        "handshake_hash": app.get_handshake_hash().hex(),
        "transport": [
            {"from": "app", "nonce": 0, "plaintext": reqb.hex(), "ciphertext": c_req.hex()},
            {"from": "box", "nonce": 0, "plaintext": resb.hex(), "ciphertext": c_res.hex()},
        ],
        "msg1_frames_mtu23": [f.hex() for f in split(0, FLAG_HANDSHAKE, m1, 23)],
        "request_frames_mtu23": [f.hex() for f in split(req["id"], 0, c_req, 23)],
    }


def build() -> dict:
    box_s, app_s = key("box static"), key("app static")
    pairing = pairing_vector(pub(box_s))
    psk = bytes.fromhex(pairing["psk"])

    cbor_vectors = [
        {"name": "request", "value": {"id": 1, "method": "GET", "path": "/api/health"}},
        {
            "name": "request_body_query",
            "value": {
                "id": 513,
                "method": "POST",
                "path": "/api/network/apply",
                "query": {"dry": "1"},
                "body": {"ssid": "Fahrzeug", "priority": 10, "hidden": False},
            },
        },
        {"name": "response", "value": {"id": 1, "status": 200, "body": {"status": "ok"}}},
        {"name": "response_empty", "value": {"id": 2, "status": 204, "body": None}},
        {
            "name": "bridge_error",
            "value": {"id": 3, "status": 0, "body": None, "error": "not_allowed"},
        },
        {"name": "event", "value": {"event": "status", "body": {"queued": 3, "failed": 0}}},
        {
            "name": "info",
            "value": {"v": 1, "name": "Kiste-1", "flags": 0x06, "pk": pub(box_s)},
        },
    ]
    for v in cbor_vectors:
        enc = cbor2.dumps(v["value"])
        v["hex"] = enc.hex()
        if isinstance(v["value"].get("pk"), bytes):
            v["value"] = {**v["value"], "pk": {"bytes_hex": v["value"]["pk"].hex()}}

    payload = bytes(range(40))
    framing = [
        {
            "msg_id": 0x1234, "event": False, "mtu": 23, "payload": payload.hex(),
            "frames": [f.hex() for f in split(0x1234, 0, payload, 23)],
        },
        {
            "msg_id": 7, "event": True, "mtu": 185, "payload": payload.hex(),
            "frames": [f.hex() for f in split(7, FLAG_EVENT, payload, 185)],
        },
        {
            "msg_id": 9, "event": False, "mtu": 23, "payload": "",
            "frames": [f.hex() for f in split(9, 0, b"", 23)],
        },
    ]

    advert = {
        "manufacturer_data": (0xFFFF).to_bytes(2, "little").hex() + "01" + "06",
        "decoded": {"company": 0xFFFF, "version": 1, "flags": 0x06},
        "local_name": "OGC-Kiste-1",
    }

    pair = handshake(
        PAIR_SUITE, app_s, key("app ephemeral pair"), box_s, key("box ephemeral pair"), psk,
        {"mode": "pair", "pid": pairing["pid"], "dev": "Testgerät", "app": "0.1.0"},
        {"v": 1, "name": "Kiste-1"},
        {"id": 1, "method": "POST", "path": "/ble/pair/confirm"},
        {"id": 1, "status": 200, "body": {"dev_id": 7, "user": "admin", "role": "admin"}},
    )
    session = handshake(
        SESSION_SUITE, app_s, key("app ephemeral session"), box_s,
        key("box ephemeral session"), None,
        {"mode": "session", "app": "0.1.0"},
        {"v": 1, "dev_id": 7, "user": "admin", "role": "admin"},
        {"id": 1, "method": "GET", "path": "/api/health"},
        {"id": 1, "status": 200, "body": {"status": "ok"}},
    )

    return {
        "_comment": "Testvektoren zu docs/BLE-PROTOKOLL.md (Version 1). "
        "Erzeugt mit scripts/ble_testvectors.py. Schlüssel nur für Tests.",
        "protocol_version": 1,
        "advertising": advert,
        "framing": framing,
        "cbor": cbor_vectors,
        "pairing_code": pairing,
        "handshake_pair": pair,
        "handshake_session": session,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="nur vergleichen, nicht schreiben")
    args = ap.parse_args()
    text = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT} weicht ab — Skript ohne --check ausführen.", file=sys.stderr)
            return 1
        print("Testvektoren aktuell.")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"geschrieben: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
