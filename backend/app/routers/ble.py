"""Bluetooth management for the companion app (docs/BLE-PROTOKOLL.md §6).

Every user pairs their own phones and sees/revokes their own devices; admins
see and revoke all of them and can switch Bluetooth off.
"""

from __future__ import annotations

import base64
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..admin_ops import audit, get_system_settings
from ..ble.keys import ble_dir, box_public_key
from ..ble.registry import create_pairing, pairing_link
from ..db import get_db
from ..deps import get_current_user, require_admin
from ..models import BleDevice, Role, User
from ..pool import node_name
from ..schemas import (
    BleDeviceOut,
    BleDeviceUpdate,
    BlePairingOut,
    BleSettingsUpdate,
    BleStatusOut,
)

router = APIRouter(prefix="/api/ble", tags=["ble"])

# offgridcloud-ble rewrites this file every few seconds while it runs.
SERVICE_STATUS_FILE = "service.json"
SERVICE_STALE_SECONDS = 30


def service_running() -> bool:
    try:
        data = json.loads((ble_dir() / SERVICE_STATUS_FILE).read_text("utf-8"))
        return time.time() - float(data["updated"]) < SERVICE_STALE_SECONDS
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _status(db: Session) -> BleStatusOut:
    pk = base64.urlsafe_b64encode(box_public_key()).rstrip(b"=").decode("ascii")
    return BleStatusOut(
        enabled=get_system_settings(db).ble_enabled,
        service_running=service_running(),
        box_name=node_name(),
        box_public_key=pk,
    )


def _device_out(device: BleDevice) -> BleDeviceOut:
    return BleDeviceOut(
        id=device.id,
        name=device.name,
        user_id=device.user_id,
        user_email=device.user.email,
        created_at=device.created_at,
        last_seen_at=device.last_seen_at,
    )


def _own_device(db: Session, user: User, device_id: int) -> BleDevice:
    device = db.get(BleDevice, device_id)
    if device is None or (user.role != Role.ADMIN and device.user_id != user.id):
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@router.get("/status", response_model=BleStatusOut)
def status(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> BleStatusOut:
    return _status(db)


@router.put("/settings", response_model=BleStatusOut)
def update_settings(
    payload: BleSettingsUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> BleStatusOut:
    row = get_system_settings(db)
    row.ble_enabled = payload.enabled
    db.commit()
    audit(db, admin, "ble.settings", f"enabled={payload.enabled}")
    return _status(db)


@router.post("/pairings", response_model=BlePairingOut, status_code=201)
def new_pairing(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> BlePairingOut:
    if not get_system_settings(db).ble_enabled:
        raise HTTPException(status_code=409, detail="Bluetooth ist abgeschaltet")
    pairing = create_pairing(db, user)
    audit(db, user, "ble.pairing_code", f"pid={pairing.pid}")
    return BlePairingOut(
        code=pairing.code,
        link=pairing_link(pairing.code, box_public_key(), node_name()),
        expires_at=pairing.expires_at,
    )


@router.get("/devices", response_model=list[BleDeviceOut])
def list_devices(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[BleDeviceOut]:
    query = select(BleDevice).order_by(BleDevice.created_at)
    if user.role != Role.ADMIN:
        query = query.where(BleDevice.user_id == user.id)
    return [_device_out(d) for d in db.scalars(query)]


@router.patch("/devices/{device_id}", response_model=BleDeviceOut)
def rename_device(
    device_id: int,
    payload: BleDeviceUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BleDeviceOut:
    device = _own_device(db, user, device_id)
    device.name = payload.name.strip()
    db.commit()
    return _device_out(device)


@router.delete("/devices/{device_id}", status_code=204)
def revoke_device(
    device_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> None:
    device = _own_device(db, user, device_id)
    name = device.name
    db.delete(device)
    db.commit()
    # The BLE service drops open sessions of revoked devices within seconds.
    audit(db, user, "ble.revoke", f"{name} (id {device_id})")
