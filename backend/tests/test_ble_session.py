"""A BLE session end to end: simulated app ↔ Session ↔ fake host."""

import asyncio
from typing import Any

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from app.ble import noise, protocol, session
from app.ble.registry import DeviceInfo, PairingClaim

PID = 0x5A3C1
SECRET = bytes(range(10))


class FakeHost:
    def __init__(self) -> None:
        self.box_key = X25519PrivateKey.generate()
        self.enabled = True
        self.devices: dict[bytes, DeviceInfo] = {}
        self.codes = {PID: SECRET}
        self.forwarded: list[tuple] = []
        self.audits: list[tuple] = []
        self.revoked: set[int] = set()
        self.restarted = False
        self.snapshots = [{"folders": {"queued": 1}}]
        self.forward_delay = 0.0

    def box_name(self):
        return "Kiste-1"

    def ble_enabled(self):
        return self.enabled

    async def find_device(self, pk):
        return self.devices.get(pk)

    async def claim_pairing(self, pid):
        secret = self.codes.get(pid)
        return PairingClaim(1, secret, 1) if secret else None

    async def confirm_pairing(self, claim, pk, name):
        info = DeviceInfo(len(self.devices) + 1, name, 1, "admin@test.local", "admin")
        self.devices[pk] = info
        self.codes.clear()
        return info

    async def device_still_valid(self, device_id):
        return device_id not in self.revoked

    async def forward(self, device, method, path, query, body):
        self.forwarded.append((device.id, method, path, query, body))
        await asyncio.sleep(self.forward_delay)
        return 200, {"path": path, "query": query}

    async def health(self):
        return {"backend_up": True, "version": "test", "uptime_s": 1}

    async def restart_backend(self):
        self.restarted = True

    async def status_snapshot(self, device):
        return self.snapshots[0]

    async def audit(self, device, action, detail):
        self.audits.append((device.id, action, detail))


class App:
    """Plays the companion app against a Session over an in-memory link."""

    def __init__(self, host: FakeHost, mtu: int = 23, static_key=None) -> None:
        self.host = host
        self.mtu = mtu
        self.key = static_key or X25519PrivateKey.generate()
        self.inbox: asyncio.Queue[protocol.Message] = asyncio.Queue()
        self.closed: list[str] = []
        self._reassembler = protocol.Reassembler()
        self.guard = session.Guard()
        self.session: session.Session | None = None
        self.transport: noise.Transport | None = None

    def open(self, guard=None):
        async def send(frame: bytes):
            msg = self._reassembler.push(frame, 0)
            if msg:
                await self.inbox.put(msg)

        async def on_close(reason):
            self.closed.append(reason)

        self.session = session.Session(
            self.host, guard or self.guard, send, lambda: self.mtu, on_close
        )
        return self

    async def write(self, msg: protocol.Message):
        for frame in protocol.split_message(msg, self.mtu):
            await self.session.receive(frame)

    async def handshake(self, payload: dict, psk: bytes | None = None, box_pk=None):
        hs = noise.Handshake(
            initiator=True,
            static_key=self.key,
            remote_static=box_pk or noise.public_bytes(self.host.box_key),
            psk=psk,
        )
        await self.write(
            protocol.Message(0, hs.write_message1(protocol.encode(payload)), handshake=True)
        )
        reply = await asyncio.wait_for(self.inbox.get(), 2)
        assert reply.handshake
        if len(reply.payload) < 48:
            return protocol.decode_map(reply.payload)
        result = protocol.decode_map(hs.read_message2(reply.payload))
        self.transport = hs.split()
        return result

    async def pair(self):
        psk = noise.derive_pairing_psk(SECRET, noise.public_bytes(self.host.box_key))
        p2 = await self.handshake({"mode": "pair", "pid": PID, "dev": "Testhandy", "app": "1"}, psk)
        assert p2 == {"v": 1, "name": "Kiste-1"}
        return await self.request(1, "POST", "/ble/pair/confirm")

    async def send_request(self, rid: int, method: str, path: str, **extra):
        plaintext = protocol.encode({"id": rid, "method": method, "path": path, **extra})
        await self.write(protocol.Message(rid, self.transport.encrypt(plaintext)))

    async def next_message(self) -> dict[str, Any]:
        msg = await asyncio.wait_for(self.inbox.get(), 2)
        assert not msg.handshake
        return protocol.decode_map(self.transport.decrypt(msg.payload)) | {"_event": msg.event}

    async def request(self, rid: int, method: str, path: str, **extra):
        await self.send_request(rid, method, path, **extra)
        while True:
            msg = await self.next_message()
            if not msg.pop("_event"):
                return msg


def run(coro):
    return asyncio.run(coro)


def test_pairing_then_session_and_forwarding():
    async def scenario():
        host = FakeHost()
        app = App(host).open()
        confirm = await app.pair()
        binding = {"dev_id": 1, "user": "admin@test.local", "role": "admin"}
        assert confirm == {"id": 1, "status": 200, "body": binding}
        assert "ble.pair" in [a[1] for a in host.audits]
        await app.session.close("test")

        again = App(host, mtu=185, static_key=app.key).open()
        p2 = await again.handshake({"mode": "session", "app": "1"})
        assert p2 == {"v": 1, "dev_id": 1, "user": "admin@test.local", "role": "admin"}
        res = await again.request(5, "GET", "/api/health", query={"x": "1"})
        assert res == {"id": 5, "status": 200, "body": {"path": "/api/health", "query": {"x": "1"}}}
        res = await again.request(6, "POST", "/api/network/apply", body={"ssid": "A"})
        assert res["status"] == 200
        assert host.audits[-1][1] == "ble.request"
        await again.session.close("test")

    run(scenario())


def test_rejections_are_short_and_unencrypted():
    async def scenario():
        host = FakeHost()
        app = App(host).open()
        assert await app.handshake({"mode": "session", "app": "1"}) == {"error": "unknown_device"}
        assert app.closed

        app = App(host).open()
        psk = noise.derive_pairing_psk(SECRET, noise.public_bytes(host.box_key))
        bad = await app.handshake({"mode": "pair", "pid": PID + 1, "dev": "x", "app": "1"}, psk)
        assert bad == {"error": "pairing_invalid"}

        host.enabled = False
        app = App(host).open()
        assert await app.handshake({"mode": "session", "app": "1"}) == {"error": "ble_disabled"}

    run(scenario())


def test_wrong_secret_is_noticed_by_the_app_only():
    async def scenario():
        host = FakeHost()
        app = App(host).open()
        wrong = noise.derive_pairing_psk(bytes(10), noise.public_bytes(host.box_key))
        try:
            await app.handshake({"mode": "pair", "pid": PID, "dev": "x", "app": "1"}, wrong)
        except noise.NoiseError:
            pass
        else:
            raise AssertionError("App hätte Nachricht 2 nicht lesen dürfen")
        assert host.devices == {}
        await app.session.close("test")

    run(scenario())


def test_pairing_session_only_allows_confirm():
    async def scenario():
        host = FakeHost()
        app = App(host).open()
        psk = noise.derive_pairing_psk(SECRET, noise.public_bytes(host.box_key))
        await app.handshake({"mode": "pair", "pid": PID, "dev": "x", "app": "1"}, psk)
        res = await app.request(2, "GET", "/api/health")
        assert res["error"] == "not_allowed" and res["status"] == 0
        assert host.forwarded == []
        await app.session.close("test")

    run(scenario())


def test_whitelist_rate_limit_and_busy():
    async def scenario():
        host = FakeHost()
        app = App(host, mtu=185).open()
        await app.pair()
        assert (await app.request(2, "POST", "/api/storage/format"))["error"] == "not_allowed"
        assert (await app.request(3, "GET", "/api/users"))["error"] == "not_allowed"
        assert (await app.request(4, "BREW", "/api/health"))["error"] == "bad_request"

        # Five at once: the fifth exceeds MAX_IN_FLIGHT while four are pending.
        host.forward_delay = 0.2
        for rid in range(10, 15):
            await app.send_request(rid, "GET", "/api/health")
        replies = [await app.next_message() for _ in range(5)]
        assert sorted(r.get("error", "") for r in replies) == ["", "", "", "", "busy"]

        host.forward_delay = 0
        errors = []
        for rid in range(20, 40):
            errors.append((await app.request(rid, "GET", "/api/health")).get("error"))
        assert "rate_limited" in errors
        await app.session.close("test")

    run(scenario())


def test_local_endpoints_and_status_events():
    async def scenario():
        host = FakeHost()
        app = App(host, mtu=185).open()
        await app.pair()
        health = await app.request(2, "GET", "/ble/health")
        assert health["body"]["backend_up"] is True
        sub = await app.request(3, "POST", "/ble/subscribe", body={"topics": ["status"]})
        assert sub["body"] == {"topics": ["status"]}
        event = await app.next_message()
        assert event == {"event": "status", "body": {"folders": {"queued": 1}}, "_event": True}
        res = await app.request(4, "POST", "/ble/backend/restart")
        assert res["body"] == {"ok": True} and host.restarted
        unsub = await app.request(5, "POST", "/ble/unsubscribe", body={"topics": ["status"]})
        assert unsub["body"] == {"topics": []}
        await app.session.close("test")

    run(scenario())


def test_protocol_errors_close_the_session():
    async def scenario():
        host = FakeHost()
        app = App(host, mtu=185).open()
        await app.pair()
        # A message the box can't decrypt (nonce out of step) ends the session.
        app.transport.encrypt(b"lost")
        await app.send_request(2, "GET", "/api/health")
        await asyncio.sleep(0)
        assert app.session.state == session.Session.CLOSED

        app = App(host).open()
        await app.session.receive(bytes([0, 0, 0, 0, 0x13]))  # FIRST|LAST + reserved bit
        assert app.session.state == session.Session.CLOSED

    run(scenario())


def test_guard_locks_after_repeated_unknown_codes():
    async def scenario():
        host = FakeHost()
        guard = session.Guard()
        psk = noise.derive_pairing_psk(SECRET, noise.public_bytes(host.box_key))
        results = []
        for _ in range(6):
            app = App(host).open(guard)
            results.append(
                await app.handshake({"mode": "pair", "pid": 1, "dev": "x", "app": "1"}, psk)
            )
        assert results[0] == {"error": "pairing_invalid"}
        assert results[-1] == {"error": "locked"}

    run(scenario())


def test_compact_status():
    from app.ble.bridge import compact_status

    snap = {
        "folders": [{"queued": 2, "done": 5}, {"queued": 1, "failed": 1}],
        "transfers": {"counts": {"running": 1}, "active": [{"kbps": 10.04}, {"kbps": 2}]},
        "bandwidth": {"gated": True, "gate_reason": "slow"},
        "notices": [{"level": "info", "title": "T" * 200, "message": ""}] * 5,
    }
    out = compact_status(snap)
    assert out["folders"] == {"queued": 3, "uploading": 0, "failed": 1, "done": 5}
    assert out["transfers"] == {"running": 1, "queued": 0, "failed": 0}
    assert out["kbps"] == 12.0 and out["gated"] is True
    assert len(out["notices"]) == 3 and out["notices"][0] == "T" * 120
    assert compact_status({"folders": []}) == {
        "folders": {"queued": 0, "uploading": 0, "failed": 0, "done": 0}
    }
    assert len(protocol.encode(out)) < 600


def test_box_host_forwards_with_short_lived_user_token():
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from app.ble.bridge import BoxHost
    from app.security import decode_access_token

    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _answer(self, status, body, ctype="application/json"):
            raw = json.dumps(body).encode() if body is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            seen["path"] = self.path
            seen["auth"] = self.headers["Authorization"]
            if self.path.startswith("/text"):
                self._answer(200, "x", "text/plain")
            elif self.path.startswith("/empty"):
                self._answer(204, None)
            else:
                self._answer(403, {"detail": "nope"})

        def do_POST(self):
            length = int(self.headers["Content-Length"])
            seen["body"] = json.loads(self.rfile.read(length))
            self._answer(200, {"ok": True})

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host = BoxHost(X25519PrivateKey.generate(), f"http://127.0.0.1:{server.server_port}")
    device = DeviceInfo(1, "Handy", 42, "u@test.local", "user")

    async def scenario():
        status, body = await host.forward(device, "GET", "/api/x", {"a": "b c"}, None)
        assert (status, body) == (403, {"detail": "nope"})
        assert seen["path"] == "/api/x?a=b+c"
        claims = decode_access_token(seen["auth"].removeprefix("Bearer "))
        assert claims["sub"] == "42" and claims["exp"] - claims["iat"] == 300
        assert await host.forward(device, "POST", "/api/y", None, {"k": [1]}) == (200, {"ok": True})
        assert seen["body"] == {"k": [1]}
        assert await host.forward(device, "GET", "/empty", None, None) == (204, None)
        try:
            await host.forward(device, "GET", "/text", None, None)
        except session.NotJson:
            pass
        else:
            raise AssertionError("text/plain darf nicht durchgereicht werden")

    try:
        run(scenario())
    finally:
        server.shutdown()

    dead = BoxHost(X25519PrivateKey.generate(), "http://127.0.0.1:9")

    async def unavailable():
        try:
            await dead.forward(device, "GET", "/api/health", None, None)
        except session.BackendUnavailable:
            return True
        return False

    assert run(unavailable())
    assert run(dead.backend_up()) is False
