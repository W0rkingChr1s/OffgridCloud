"""Bluetooth management for the companion app (offgridcloud-ble).

Protocol: docs/BLE-PROTOKOLL.md. The FastAPI backend owns devices and pairing
codes (``registry``, ``routers/ble.py``); the separate ``offgridcloud-ble``
service (``python -m app.ble``) speaks the radio protocol and bridges requests
to the REST API on localhost.
"""
