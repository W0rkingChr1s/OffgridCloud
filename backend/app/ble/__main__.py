"""Entry point of the offgridcloud-ble service: ``python -m app.ble``."""

from __future__ import annotations

import asyncio
import logging
import signal

from ..config import get_settings
from ..db import init_db
from .bridge import BoxHost
from .gatt_bluez import BlueZGatt
from .keys import load_or_create_box_key
from .service import BleService


async def main() -> None:
    settings = get_settings()
    settings.ensure_dirs()
    # The backend creates the tables too; harmless if both do it.
    init_db()
    host = BoxHost(load_or_create_box_key(), settings.ble_backend_url)
    gatt = BlueZGatt(settings.ble_adapter)
    service = BleService(host, gatt)
    # Connect to BlueZ first: refresh() already advertises, which needs the bus.
    await gatt.start(service.on_write, service.on_disconnect)
    await service.refresh()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await service.run(stop)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
