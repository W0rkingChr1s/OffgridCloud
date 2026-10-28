# D-Bus signatures as string annotations (dbus-fast) and D-Bus method names.
# ruff: noqa: F722, F821, N802
"""BlueZ GATT server and advertisement over D-Bus (``dbus-fast``).

Exposes the service of BLE-PROTOKOLL.md §3.2 (``info`` read, ``rx`` write
without response, ``tx`` notify) and the advertisement of §3.1. Needs the
system bus, BlueZ ≥ 5.56 and membership in the ``bluetooth`` group.

No ``from __future__ import annotations`` here: dbus-fast reads the D-Bus
signatures from the (string) annotations at runtime.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from dbus_fast import BusType, Message, MessageType, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.service import PropertyAccess, ServiceInterface, dbus_property, method

from .protocol import (
    INFO_CHAR_UUID,
    MANUFACTURER_ID,
    PROTOCOL_VERSION,
    RX_CHAR_UUID,
    SERVICE_UUID,
    TX_CHAR_UUID,
)

log = logging.getLogger("offgridcloud.ble")

BLUEZ = "org.bluez"
APP_PATH = "/de/offgridcloud/ble"
SERVICE_PATH = APP_PATH + "/service0"
ADVERT_PATH = APP_PATH + "/advert0"

OnWrite = Callable[[str, bytes, int | None], Awaitable[None]]
OnDisconnect = Callable[[str], Awaitable[None]]


class _Application(ServiceInterface):
    """``org.freedesktop.DBus.ObjectManager`` for RegisterApplication."""

    def __init__(self, objects: Callable[[], dict]) -> None:
        super().__init__("org.freedesktop.DBus.ObjectManager")
        self._objects = objects

    @method()
    def GetManagedObjects(self) -> "a{oa{sa{sv}}}":
        return self._objects()


class _Service(ServiceInterface):
    def __init__(self) -> None:
        super().__init__("org.bluez.GattService1")

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return SERVICE_UUID

    @dbus_property(access=PropertyAccess.READ)
    def Primary(self) -> "b":
        return True


class _Characteristic(ServiceInterface):
    def __init__(self, uuid: str, flags: list[str]) -> None:
        super().__init__("org.bluez.GattCharacteristic1")
        self._uuid = uuid
        self._flags = flags
        self.value = b""
        self.on_write: OnWrite | None = None
        self.notifying = False

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return self._uuid

    @dbus_property(access=PropertyAccess.READ)
    def Service(self) -> "o":
        return SERVICE_PATH

    @dbus_property(access=PropertyAccess.READ)
    def Flags(self) -> "as":
        return self._flags

    @dbus_property(access=PropertyAccess.READ)
    def Value(self) -> "ay":
        return self.value

    @method()
    def ReadValue(self, options: "a{sv}") -> "ay":
        offset = options.get("offset")
        start = offset.value if offset is not None else 0
        return self.value[start:]

    @method()
    async def WriteValue(self, value: "ay", options: "a{sv}"):
        if self.on_write is None:
            return
        device = options.get("device")
        mtu = options.get("mtu")
        await self.on_write(
            device.value if device is not None else "",
            bytes(value),
            mtu.value if mtu is not None else None,
        )

    @method()
    def StartNotify(self):
        self.notifying = True

    @method()
    def StopNotify(self):
        self.notifying = False

    def notify(self, data: bytes) -> None:
        self.value = data
        self.emit_properties_changed({"Value": data})


class _Advertisement(ServiceInterface):
    def __init__(self) -> None:
        super().__init__("org.bluez.LEAdvertisement1")
        self.local_name = ""
        self.flags = 0

    @dbus_property(access=PropertyAccess.READ)
    def Type(self) -> "s":
        return "peripheral"

    @dbus_property(access=PropertyAccess.READ)
    def ServiceUUIDs(self) -> "as":
        return [SERVICE_UUID]

    @dbus_property(access=PropertyAccess.READ)
    def ManufacturerData(self) -> "a{qv}":
        return {MANUFACTURER_ID: Variant("ay", bytes([PROTOCOL_VERSION, self.flags]))}

    @dbus_property(access=PropertyAccess.READ)
    def LocalName(self) -> "s":
        return self.local_name

    @dbus_property(access=PropertyAccess.READ)
    def Discoverable(self) -> "b":
        return True

    @method()
    def Release(self):
        log.info("BlueZ hat die Werbung freigegeben")


class BlueZGatt:
    """``service.Gatt`` on the system bus."""

    def __init__(self, adapter: str = "") -> None:
        self._adapter_name = adapter
        self.adapter_path = ""
        self.bus: MessageBus | None = None
        self._app = _Application(self._managed_objects)
        self._service = _Service()
        self.info = _Characteristic(INFO_CHAR_UUID, ["read"])
        self.rx = _Characteristic(RX_CHAR_UUID, ["write-without-response"])
        self.tx = _Characteristic(TX_CHAR_UUID, ["notify"])
        self._advert = _Advertisement()
        self._on_disconnect: OnDisconnect | None = None
        # False once bluetoothd restarted: our GATT app and advert are gone.
        self.alive = True

    def _chars(self) -> dict[str, _Characteristic]:
        return {
            SERVICE_PATH + "/char0": self.info,
            SERVICE_PATH + "/char1": self.rx,
            SERVICE_PATH + "/char2": self.tx,
        }

    def _managed_objects(self) -> dict:
        objects = {
            SERVICE_PATH: {
                "org.bluez.GattService1": {
                    "UUID": Variant("s", SERVICE_UUID),
                    "Primary": Variant("b", True),
                }
            }
        }
        for path, char in self._chars().items():
            objects[path] = {
                "org.bluez.GattCharacteristic1": {
                    "UUID": Variant("s", char._uuid),
                    "Service": Variant("o", SERVICE_PATH),
                    "Flags": Variant("as", char._flags),
                }
            }
        return objects

    async def _call(self, path: str, interface: str, member: str, signature="", body=None):
        reply = await self.bus.call(
            Message(
                destination=BLUEZ,
                path=path,
                interface=interface,
                member=member,
                signature=signature,
                body=body or [],
            )
        )
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"{interface}.{member}: {reply.error_name} {reply.body}")
        return reply.body

    async def _find_adapter(self) -> str:
        (objects,) = await self._call(
            "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"
        )
        for path, interfaces in sorted(objects.items()):
            if "org.bluez.GattManager1" not in interfaces:
                continue
            if not self._adapter_name or path.endswith("/" + self._adapter_name):
                return path
        raise RuntimeError("Kein Bluetooth-Adapter mit GATT-Unterstützung gefunden")

    async def start(self, on_write: OnWrite, on_disconnect: OnDisconnect, bus=None) -> None:
        self.bus = bus or await MessageBus(bus_type=BusType.SYSTEM).connect()
        self.rx.on_write = on_write
        self._on_disconnect = on_disconnect
        self.adapter_path = await self._find_adapter()
        await self._call(
            self.adapter_path,
            "org.freedesktop.DBus.Properties",
            "Set",
            "ssv",
            ["org.bluez.Adapter1", "Powered", Variant("b", True)],
        )
        self.bus.export(APP_PATH, self._app)
        self.bus.export(SERVICE_PATH, self._service)
        for path, char in self._chars().items():
            self.bus.export(path, char)
        self.bus.add_message_handler(self._on_signal)
        await self.bus.call(
            Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus",
                member="AddMatch",
                signature="s",
                body=[
                    "type='signal',sender='org.bluez',"
                    "interface='org.freedesktop.DBus.Properties',"
                    "member='PropertiesChanged',arg0='org.bluez.Device1'"
                ],
            )
        )
        await self.bus.call(
            Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus",
                member="AddMatch",
                signature="s",
                body=[
                    "type='signal',sender='org.freedesktop.DBus',"
                    "interface='org.freedesktop.DBus',"
                    "member='NameOwnerChanged',arg0='org.bluez'"
                ],
            )
        )
        await self._call(
            self.adapter_path,
            "org.bluez.GattManager1",
            "RegisterApplication",
            "oa{sv}",
            [APP_PATH, {}],
        )
        log.info("GATT-Dienst auf %s registriert", self.adapter_path)

    def _on_signal(self, msg: Message) -> None:
        if msg.message_type != MessageType.SIGNAL:
            return
        if msg.member == "NameOwnerChanged":
            if msg.body and msg.body[0] == BLUEZ and self.alive:
                log.warning("bluetoothd wurde neu gestartet")
                self.alive = False
            return
        if msg.member != "PropertiesChanged":
            return
        if not msg.path.startswith(self.adapter_path + "/dev_") or not msg.body:
            return
        iface, changed = msg.body[0], msg.body[1]
        connected = changed.get("Connected")
        if iface == "org.bluez.Device1" and connected is not None and not connected.value:
            if self._on_disconnect is not None:
                asyncio.ensure_future(self._on_disconnect(msg.path))

    # --- service.Gatt ---------------------------------------------------------

    def set_info(self, value: bytes) -> None:
        self.info.value = value

    async def notify(self, frame: bytes) -> None:
        self.tx.notify(frame)

    async def start_advertising(self, name: str, flags: int) -> None:
        self._advert.local_name = name
        self._advert.flags = flags
        # A failed registration must not leave the export behind: the next
        # export() would raise forever and the box stays invisible.
        self.bus.unexport(ADVERT_PATH, self._advert)
        self.bus.export(ADVERT_PATH, self._advert)
        try:
            await self._call(
                self.adapter_path,
                "org.bluez.LEAdvertisingManager1",
                "RegisterAdvertisement",
                "oa{sv}",
                [ADVERT_PATH, {}],
            )
        except Exception:
            self.bus.unexport(ADVERT_PATH, self._advert)
            raise

    async def stop_advertising(self) -> None:
        try:
            await self._call(
                self.adapter_path,
                "org.bluez.LEAdvertisingManager1",
                "UnregisterAdvertisement",
                "o",
                [ADVERT_PATH],
            )
        finally:
            self.bus.unexport(ADVERT_PATH, self._advert)

    async def disconnect(self, device: str) -> None:
        await self._call(device, "org.bluez.Device1", "Disconnect")
