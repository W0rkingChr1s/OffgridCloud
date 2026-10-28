"""The BlueZ GATT layer against a real D-Bus daemon with a fake BlueZ.

No radio here: a private ``dbus-daemon`` stands in for the system bus and a
small fake implements just the BlueZ calls ``gatt_bluez`` makes. That checks
the D-Bus signatures, object layout and signal handling for real.
"""

import asyncio
import os
import shutil
import subprocess
import tempfile

import pytest

dbus_fast = pytest.importorskip("dbus_fast")
if shutil.which("dbus-daemon") is None:
    pytest.skip("dbus-daemon nicht installiert", allow_module_level=True)

from dbus_fast import DBusError, Message, MessageType, Variant  # noqa: E402
from dbus_fast.aio import MessageBus  # noqa: E402
from dbus_fast.service import (  # noqa: E402
    PropertyAccess,
    ServiceInterface,
    dbus_property,
    method,
)

from app.ble import gatt_bluez, protocol  # noqa: E402

ADAPTER = "/org/bluez/hci0"
DEVICE = ADAPTER + "/dev_AA_BB_CC_DD_EE_FF"


class FakeObjectManager(ServiceInterface):
    def __init__(self):
        super().__init__("org.freedesktop.DBus.ObjectManager")

    @method()
    def GetManagedObjects(self) -> "a{oa{sa{sv}}}":  # noqa: F722, F821, N802
        return {
            ADAPTER: {
                "org.bluez.Adapter1": {"Powered": Variant("b", False)},
                "org.bluez.GattManager1": {},
                "org.bluez.LEAdvertisingManager1": {},
            }
        }


class FakeAdapter(ServiceInterface):
    def __init__(self, log):
        super().__init__("org.bluez.Adapter1")
        self.log = log
        self._powered = False

    @dbus_property(access=PropertyAccess.READWRITE)
    def Powered(self) -> "b":  # noqa: F821, N802
        return self._powered

    @Powered.setter
    def Powered(self, value: "b"):  # noqa: F811, F821, N802
        self._powered = value
        self.log.append(("set", "org.bluez.Adapter1", "Powered", value))


class FakeGattManager(ServiceInterface):
    def __init__(self, log):
        super().__init__("org.bluez.GattManager1")
        self.log = log

    @method()
    def RegisterApplication(self, app: "o", options: "a{sv}"):  # noqa: F722, F821, N802
        self.log.append(("register_app", app))


class FakeAdvertisingManager(ServiceInterface):
    def __init__(self, log):
        super().__init__("org.bluez.LEAdvertisingManager1")
        self.log = log
        self.fail_next = False

    @method()
    def RegisterAdvertisement(self, adv: "o", options: "a{sv}"):  # noqa: F722, F821, N802
        if self.fail_next:
            self.fail_next = False
            raise DBusError("org.bluez.Error.Failed", "Failed to register advertisement")
        self.log.append(("register_adv", adv))

    @method()
    def UnregisterAdvertisement(self, adv: "o"):  # noqa: F821, N802
        self.log.append(("unregister_adv", adv))


class FakeDevice(ServiceInterface):
    def __init__(self, log):
        super().__init__("org.bluez.Device1")
        self.log = log

    @method()
    def Disconnect(self):  # noqa: N802
        self.log.append(("disconnect",))


@pytest.fixture
def bus_address():
    tmp = tempfile.mkdtemp()
    config = os.path.join(tmp, "bus.conf")
    with open(config, "w") as fh:
        fh.write(
            f"""<busconfig>
  <type>session</type>
  <listen>unix:dir={tmp}</listen>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>"""
        )
    proc = subprocess.Popen(
        ["dbus-daemon", "--config-file", config, "--print-address", "--nofork"],
        stdout=subprocess.PIPE,
        text=True,
    )
    address = proc.stdout.readline().strip()
    yield address
    proc.terminate()
    proc.wait(5)
    shutil.rmtree(tmp, ignore_errors=True)


async def _call(bus, dest, path, iface, member, signature="", body=None):
    reply = await bus.call(
        Message(
            destination=dest,
            path=path,
            interface=iface,
            member=member,
            signature=signature,
            body=body or [],
        )
    )
    assert reply.message_type == MessageType.METHOD_RETURN, reply.body
    return reply.body


def test_gatt_layer_on_a_real_bus(bus_address):
    async def scenario():
        log = []
        bluez = await MessageBus(bus_address=bus_address).connect()
        bluez.export("/", FakeObjectManager())
        bluez.export(ADAPTER, FakeAdapter(log))
        bluez.export(ADAPTER, FakeGattManager(log))
        adv_manager = FakeAdvertisingManager(log)
        bluez.export(ADAPTER, adv_manager)
        bluez.export(DEVICE, FakeDevice(log))
        await bluez.request_name("org.bluez")

        writes, disconnects = [], []

        async def on_write(device, data, mtu):
            writes.append((device, data, mtu))

        async def on_disconnect(device):
            disconnects.append(device)

        app_bus = await MessageBus(bus_address=bus_address).connect()
        gatt = gatt_bluez.BlueZGatt()
        await gatt.start(on_write, on_disconnect, bus=app_bus)
        assert ("set", "org.bluez.Adapter1", "Powered", True) in log
        assert ("register_app", gatt_bluez.APP_PATH) in log

        # BlueZ reads the application tree from the service's connection.
        (objects,) = await _call(
            bluez,
            app_bus.unique_name,
            gatt_bluez.APP_PATH,
            "org.freedesktop.DBus.ObjectManager",
            "GetManagedObjects",
        )
        uuids = {
            props.get("org.bluez.GattCharacteristic1", {}).get("UUID", Variant("s", "")).value
            for props in objects.values()
        }
        assert {protocol.INFO_CHAR_UUID, protocol.RX_CHAR_UUID, protocol.TX_CHAR_UUID} <= uuids
        service = objects[gatt_bluez.SERVICE_PATH]["org.bluez.GattService1"]
        assert service["UUID"].value == protocol.SERVICE_UUID

        info_path, rx_path, tx_path = (gatt_bluez.SERVICE_PATH + f"/char{i}" for i in range(3))

        # info: read (with offset)
        gatt.set_info(b"\x01\x02\x03")
        (value,) = await _call(
            bluez, app_bus.unique_name, info_path, "org.bluez.GattCharacteristic1",
            "ReadValue", "a{sv}", [{"offset": Variant("q", 1)}],
        )
        assert value == b"\x02\x03"

        # rx: write without response, with device and mtu
        await _call(
            bluez, app_bus.unique_name, rx_path, "org.bluez.GattCharacteristic1",
            "WriteValue", "aya{sv}",
            [b"frame", {"device": Variant("o", DEVICE), "mtu": Variant("q", 185)}],
        )
        assert writes == [(DEVICE, b"frame", 185)]

        # tx: notify arrives as PropertiesChanged on Value
        received = asyncio.get_running_loop().create_future()

        def on_signal(msg):
            if msg.member == "PropertiesChanged" and msg.path == tx_path:
                if not received.done():
                    received.set_result(msg.body[1]["Value"].value)

        bluez.add_message_handler(on_signal)
        await _call(
            bluez, "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "AddMatch", "s", [f"type='signal',sender='{app_bus.unique_name}'"],
        )
        await _call(
            bluez, app_bus.unique_name, tx_path, "org.bluez.GattCharacteristic1",
            "StartNotify",
        )
        await gatt.notify(b"\x00\x01\x00\x00\x03hi")
        assert await asyncio.wait_for(received, 2) == b"\x00\x01\x00\x00\x03hi"

        # advertising: register, BlueZ reads the properties, unregister
        await gatt.start_advertising("OGC-Kiste-1", 0x06)
        assert ("register_adv", gatt_bluez.ADVERT_PATH) in log
        (props,) = await _call(
            bluez, app_bus.unique_name, gatt_bluez.ADVERT_PATH,
            "org.freedesktop.DBus.Properties", "GetAll", "s", ["org.bluez.LEAdvertisement1"],
        )
        assert props["Type"].value == "peripheral"
        assert props["ServiceUUIDs"].value == [protocol.SERVICE_UUID]
        assert props["LocalName"].value == "OGC-Kiste-1"
        mfr = props["ManufacturerData"].value[protocol.MANUFACTURER_ID].value
        assert mfr == bytes([1, 0x06])
        await gatt.stop_advertising()
        assert ("unregister_adv", gatt_bluez.ADVERT_PATH) in log

        # Regression: one refused registration used to leave the advert
        # exported, so every later attempt failed in export() for good.
        adv_manager.fail_next = True
        log.clear()
        try:
            await gatt.start_advertising("OGC-Kiste-1", 0x04)
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected the BlueZ error")
        await gatt.start_advertising("OGC-Kiste-1", 0x04)
        assert ("register_adv", gatt_bluez.ADVERT_PATH) in log
        await gatt.stop_advertising()

        # a device dropping off is reported; Disconnect reaches BlueZ
        await bluez.send(
            Message.new_signal(
                DEVICE,
                "org.freedesktop.DBus.Properties",
                "PropertiesChanged",
                "sa{sv}as",
                ["org.bluez.Device1", {"Connected": Variant("b", False)}, []],
            )
        )
        for _ in range(50):
            if disconnects:
                break
            await asyncio.sleep(0.02)
        assert disconnects == [DEVICE]
        await gatt.disconnect(DEVICE)
        assert ("disconnect",) in log

        # bluetoothd going away drops our registrations: the service must notice.
        assert gatt.alive
        await bluez.release_name("org.bluez")
        for _ in range(50):
            if not gatt.alive:
                break
            await asyncio.sleep(0.02)
        assert not gatt.alive

        app_bus.disconnect()
        bluez.disconnect()

    asyncio.run(scenario())
