"""offgridcloud-ble orchestration: one connection, advertising, info, status file."""

import asyncio
import json

from app.ble import noise, protocol, service
from app.ble.keys import ble_dir

from .test_ble_session import App, FakeHost


class ServiceHost(FakeHost):
    def __init__(self):
        super().__init__()
        self.open_pairing = False

    def set_enabled(self, enabled):
        self.enabled = enabled

    async def read_enabled(self):
        return self.enabled

    async def pairing_open(self):
        return self.open_pairing

    async def backend_up(self):
        return True


class FakeGatt:
    def __init__(self):
        self.info = b""
        self.adverts: list[tuple[str, int] | None] = []
        self.disconnected: list[str] = []
        self.sent: list[bytes] = []
        self.sink = None

    def set_info(self, value):
        self.info = value

    async def notify(self, frame):
        self.sent.append(frame)
        if self.sink is not None:
            await self.sink(frame)

    async def start_advertising(self, name, flags):
        self.adverts.append((name, flags))

    async def stop_advertising(self):
        self.adverts.append(None)

    async def disconnect(self, device):
        self.disconnected.append(device)


class RadioApp(App):
    """The simulated app, but talking through BleService like a real central."""

    def __init__(self, svc, gatt, device, **kw):
        super().__init__(svc.host, **kw)
        self.svc, self.gatt, self.device = svc, gatt, device

        async def sink(frame):
            m = self._reassembler.push(frame, 0)
            if m:
                await self.inbox.put(m)

        gatt.sink = sink

    async def write(self, msg):
        for frame in protocol.split_message(msg, self.mtu):
            await self.svc.on_write(self.device, frame, self.mtu)


def run(coro):
    return asyncio.run(coro)


def test_info_advertising_and_status_file():
    async def scenario():
        host, gatt = ServiceHost(), FakeGatt()
        svc = service.BleService(host, gatt)
        host.open_pairing = True
        await svc.refresh()
        info = protocol.decode_map(gatt.info)
        assert info == {
            "v": 1,
            "name": "Kiste-1",
            "flags": protocol.FLAG_PAIRING_OPEN | protocol.FLAG_BACKEND_UP,
            "pk": noise.public_bytes(host.box_key),
        }
        assert gatt.adverts == [("OGC-Kiste-1", 0x06)]
        await svc.refresh()  # unchanged → no re-registration
        assert gatt.adverts == [("OGC-Kiste-1", 0x06)]
        host.open_pairing = False
        await svc.refresh()
        assert gatt.adverts[-2:] == [None, ("OGC-Kiste-1", 0x04)]
        status = json.loads((ble_dir() / service.STATUS_FILE).read_text())
        assert status["enabled"] is True and status["connected"] is False

    run(scenario())


def test_one_connection_at_a_time_and_advertising_pause():
    async def scenario():
        host, gatt = ServiceHost(), FakeGatt()
        svc = service.BleService(host, gatt)
        await svc.refresh()

        app = RadioApp(svc, gatt, "/dev_A", mtu=185)
        await app.pair()
        assert gatt.adverts[-1] is None  # paused while connected
        assert svc.mtu == 185

        # A second phone is ignored completely — nothing is sent for it.
        sent = len(gatt.sent)
        await svc.on_write("/dev_B", b"\x00\x00\x00\x00\x0b" + bytes(60), 23)
        assert len(gatt.sent) == sent and svc.device == "/dev_A"

        res = await app.request(2, "GET", "/api/health")
        assert res["status"] == 200

        await svc.on_disconnect("/dev_A")
        assert svc.session is None and gatt.disconnected == ["/dev_A"]
        assert gatt.adverts[-1] == ("OGC-Kiste-1", 0x04)

    run(scenario())


def test_switching_bluetooth_off_closes_the_session():
    async def scenario():
        host, gatt = ServiceHost(), FakeGatt()
        svc = service.BleService(host, gatt)
        await svc.refresh()
        app = RadioApp(svc, gatt, "/dev_A", mtu=185)
        await app.pair()
        host.enabled = False
        await svc.refresh()
        assert svc.session is None and gatt.adverts[-1] is None
        # Writes while disabled start nothing.
        await svc.on_write("/dev_A", b"\x00\x00\x00\x00\x0b" + bytes(60), 23)
        assert svc.session is None

    run(scenario())


def test_run_loop_stops_cleanly():
    async def scenario():
        host, gatt = ServiceHost(), FakeGatt()
        svc = service.BleService(host, gatt)
        stop = asyncio.Event()
        task = asyncio.create_task(svc.run(stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, 2)
        assert gatt.adverts[-1] is None

    run(scenario())


def test_main_starts_gatt_before_first_refresh(monkeypatch):
    """Regression: advertising before the D-Bus connection crashed the service."""
    from app.ble import __main__ as entry

    class StrictGatt(FakeGatt):
        def __init__(self, adapter=""):
            super().__init__()
            self.started = False

        async def start(self, on_write, on_disconnect):
            self.started = True

        async def start_advertising(self, name, flags):
            assert self.started, "advertising before gatt.start()"
            await super().start_advertising(name, flags)

    async def no_loop(self, stop):
        return None

    monkeypatch.setattr(entry, "BlueZGatt", StrictGatt)
    monkeypatch.setattr(entry, "BoxHost", lambda key, url: ServiceHost())
    monkeypatch.setattr(entry, "load_or_create_box_key", lambda: None)
    monkeypatch.setattr(entry.BleService, "run", no_loop)
    run(entry.main())
    status = ble_dir() / service.STATUS_FILE
    try:
        assert json.loads(status.read_text("utf-8"))["enabled"]
    finally:
        status.unlink()  # the web UI tests expect no running service
