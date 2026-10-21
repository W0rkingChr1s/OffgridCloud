"""BLE wire format and Noise against docs/ble/testvectors.json."""

import json
import re
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from app.ble import noise, protocol, whitelist

VECTORS = json.loads(
    (Path(__file__).resolve().parents[2] / "docs" / "ble" / "testvectors.json").read_text(
        "utf-8"
    )
)


def _key(hex_: str) -> X25519PrivateKey:
    return X25519PrivateKey.from_private_bytes(bytes.fromhex(hex_))


@pytest.mark.parametrize("v", VECTORS["framing"], ids=lambda v: f"id{v['msg_id']}-mtu{v['mtu']}")
def test_framing_vectors(v):
    msg = protocol.Message(v["msg_id"], bytes.fromhex(v["payload"]), v["event"])
    assert [f.hex() for f in protocol.split_message(msg, v["mtu"])] == v["frames"]
    r = protocol.Reassembler()
    out = [r.push(bytes.fromhex(f), 0.0) for f in v["frames"]]
    assert out[-1] == msg


def test_framing_rejects_violations():
    frames = protocol.split_message(protocol.Message(3, bytes(400)), 185)
    r = protocol.Reassembler()
    r.push(frames[0], 0)
    with pytest.raises(protocol.FramingError):
        r.push(frames[2], 0)
    with pytest.raises(protocol.FramingError):
        protocol.Reassembler().push(bytes([0, 1, 0, 0, 0x13]), 0)
    with pytest.raises(protocol.FramingError):
        protocol.Reassembler().push(b"\x00", 0)
    # Timeout drops the partial message; its next frame is then an orphan.
    r = protocol.Reassembler(timeout=1)
    r.push(frames[0], 0)
    with pytest.raises(protocol.FramingError):
        r.push(frames[1], 5)


@pytest.mark.parametrize("v", VECTORS["cbor"], ids=lambda v: v["name"])
def test_cbor_vectors(v):
    value = v["value"]
    if isinstance(value.get("pk"), dict):
        value = {**value, "pk": bytes.fromhex(value["pk"]["bytes_hex"])}
    assert protocol.encode(value).hex() == v["hex"]
    assert protocol.decode_map(bytes.fromhex(v["hex"])) == value


def test_info_and_advert_name():
    info = next(v for v in VECTORS["cbor"] if v["name"] == "info")
    pk = bytes.fromhex(info["value"]["pk"]["bytes_hex"])
    assert protocol.build_info("Kiste-1", 0x06, pk).hex() == info["hex"]
    assert len(protocol.build_info("x" * 100, 7, pk)) <= 120
    assert protocol.advert_name("Kiste-1") == VECTORS["advertising"]["local_name"]
    assert len(protocol.advert_name("Ä" * 40).encode()) <= 29


def test_decode_map_rejects_non_maps():
    with pytest.raises(protocol.CborError):
        protocol.decode_map(b"\xff")
    with pytest.raises(protocol.CborError):
        protocol.decode_map(protocol.encode([1, 2]))
    with pytest.raises(protocol.CborError):
        protocol.decode_map(protocol.encode({1: 2}))


def test_psk_vector():
    v = VECTORS["pairing_code"]
    secret, box_pk = bytes.fromhex(v["secret"]), bytes.fromhex(v["box_static_public"])
    psk = noise.derive_pairing_psk(secret, box_pk)
    assert psk.hex() == v["psk"]


@pytest.mark.parametrize("name", ["handshake_pair", "handshake_session"])
def test_noise_vectors_both_roles(name):
    v = VECTORS[name]
    psk = bytes.fromhex(v["psk"]) if v["psk"] else None
    app = noise.Handshake(
        initiator=True,
        static_key=_key(v["app_static_private"]),
        remote_static=bytes.fromhex(v["box_static_public"]),
        psk=psk,
        ephemeral_key=_key(v["app_ephemeral_private"]),
    )
    box = noise.Handshake(
        initiator=False,
        static_key=_key(v["box_static_private"]),
        psk=bytes(32) if psk else None,
        ephemeral_key=_key(v["box_ephemeral_private"]),
    )
    m1 = app.write_message1(bytes.fromhex(v["msg1_payload"]))
    assert m1.hex() == v["msg1"]
    assert box.read_message1(m1).hex() == v["msg1_payload"]
    assert box.remote_static.hex() == v["app_static_public"]
    if psk:
        box.set_psk(psk)  # placeholder until the pid is known (§5.3)
    m2 = box.write_message2(bytes.fromhex(v["msg2_payload"]))
    assert m2.hex() == v["msg2"]
    assert app.read_message2(m2).hex() == v["msg2_payload"]
    a, b = app.split(), box.split()
    assert a.handshake_hash.hex() == b.handshake_hash.hex() == v["handshake_hash"]
    to_box, to_app = v["transport"]
    assert a.encrypt(bytes.fromhex(to_box["plaintext"])).hex() == to_box["ciphertext"]
    assert b.decrypt(bytes.fromhex(to_box["ciphertext"])).hex() == to_box["plaintext"]
    assert b.encrypt(bytes.fromhex(to_app["plaintext"])).hex() == to_app["ciphertext"]


def test_wrong_psk_fails_only_at_the_app():
    box_key, app_key = X25519PrivateKey.generate(), X25519PrivateKey.generate()
    app = noise.Handshake(
        initiator=True,
        static_key=app_key,
        remote_static=noise.public_bytes(box_key),
        psk=bytes([1]) * 32,
    )
    box = noise.Handshake(initiator=False, static_key=box_key, psk=bytes(32))
    box.read_message1(app.write_message1(b"hi"))  # works without the right psk
    box.set_psk(bytes([2]) * 32)
    with pytest.raises(noise.NoiseError):
        app.read_message2(box.write_message2(b"x"))


def test_session_handshake_rejects_wrong_box_key():
    app = noise.Handshake(
        initiator=True,
        static_key=X25519PrivateKey.generate(),
        remote_static=noise.public_bytes(X25519PrivateKey.generate()),
    )
    box = noise.Handshake(initiator=False, static_key=X25519PrivateKey.generate())
    with pytest.raises(noise.NoiseError):
        box.read_message1(app.write_message1(b""))


def _spec_whitelist() -> list[tuple[str, list[str]]]:
    """Rows of the §7.5 table in docs/BLE-PROTOKOLL.md as (path, methods)."""
    spec = (Path(__file__).parents[2] / "docs" / "BLE-PROTOKOLL.md").read_text("utf-8")
    section = spec.split("### 7.5", 1)[1].split("Nicht erreichbar", 1)[0]
    rows = []
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not cells[0].startswith("`/"):
            continue
        path = re.sub(r"\{[^}]+\}", "7", cells[0].strip("`"))
        methods = (
            sorted(whitelist.WRITE)
            if cells[1].startswith("alle")
            else [m.strip() for m in cells[1].split(",")]
        )
        rows.append((path, methods))
    return rows


def test_whitelist_matches_the_spec():
    rows = _spec_whitelist()
    assert len(rows) >= 19 and any(p.startswith("/api/pool/") for p, _ in rows)
    for path, methods in rows:
        for method in methods:
            assert whitelist.api_allowed(method, path), (method, path)
        for method in sorted(whitelist.WRITE - set(methods)):
            assert not whitelist.api_allowed(method, path), (method, path)


def test_whitelist_keeps_pool_access_management_out():
    assert whitelist.api_allowed("GET", "/api/pool/overview")
    assert whitelist.api_allowed("PUT", "/api/pool/priority")
    assert not whitelist.api_allowed("GET", "/api/pool/tokens")
    assert not whitelist.api_allowed("POST", "/api/pool/peers")
    assert not whitelist.api_allowed("DELETE", "/api/pool/peers/3")
    assert not whitelist.api_allowed("POST", "/api/pool/peers/x/control")
    assert not whitelist.api_allowed("POST", "/api/storage/format")
