"""VPN client: admin CRUD, connect/disconnect and live status.

Configs (which embed private keys/credentials) are encrypted at rest and never
returned to the client. Bringing a tunnel up is a privileged system operation —
see :mod:`app.vpn` for the capability requirements.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import vpn as vpnsvc
from ..admin_ops import audit
from ..crypto import decrypt, encrypt
from ..db import get_db
from ..deps import require_admin
from ..models import User, VpnTunnel
from ..schemas import (
    VpnCapabilitiesOut,
    VpnCreate,
    VpnStatusOut,
    VpnTunnelOut,
    VpnUpdate,
)

logger = logging.getLogger("offgridcloud.vpn")

router = APIRouter(prefix="/api/vpn", tags=["vpn"], dependencies=[Depends(require_admin)])


def _load(tunnel: VpnTunnel) -> dict[str, str]:
    raw = decrypt(tunnel.config_encrypted)
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    return {"config": data.get("config", ""), "username": data.get("username", ""),
            "password": data.get("password", "")}


def _to_out(tunnel: VpnTunnel, active_id: int | None) -> VpnTunnelOut:
    creds = _load(tunnel)
    return VpnTunnelOut(
        id=tunnel.id,
        name=tunnel.name,
        type=tunnel.type,
        autostart=tunnel.autostart,
        last_error=tunnel.last_error,
        created_at=tunnel.created_at,
        has_username=bool(creds["username"]),
        active=tunnel.id == active_id,
    )


def _get(db: Session, tunnel_id: int) -> VpnTunnel:
    tunnel = db.get(VpnTunnel, tunnel_id)
    if tunnel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VPN not found")
    return tunnel


# --- Capabilities & status ------------------------------------------------


@router.get("/capabilities", response_model=VpnCapabilitiesOut)
def get_capabilities() -> VpnCapabilitiesOut:
    caps = vpnsvc.capabilities()
    ready = caps.net_admin and caps.tun_device
    message = ""
    if not ready:
        parts = []
        if not caps.tun_device:
            parts.append("kein /dev/net/tun (Container mit --device=/dev/net/tun starten)")
        if not caps.net_admin:
            parts.append("keine NET_ADMIN-Berechtigung (--cap-add=NET_ADMIN)")
        message = "VPN benötigt erhöhte Rechte: " + " und ".join(parts) + "."
    return VpnCapabilitiesOut(
        net_admin=caps.net_admin,
        tun_device=caps.tun_device,
        wireguard=caps.wireguard,
        openvpn=caps.openvpn,
        ready=ready,
        message=message,
    )


@router.get("/status", response_model=VpnStatusOut)
def get_status() -> VpnStatusOut:
    st = vpnsvc.status()
    return VpnStatusOut(
        active_id=st.active_id,
        state=st.state,
        detail=st.detail,
        endpoint=st.endpoint,
        last_handshake=st.last_handshake,
    )


# --- CRUD -----------------------------------------------------------------


@router.get("", response_model=list[VpnTunnelOut])
def list_tunnels(db: Session = Depends(get_db)) -> list[VpnTunnelOut]:
    active = vpnsvc.active_id()
    tunnels = db.scalars(select(VpnTunnel).order_by(VpnTunnel.name)).all()
    return [_to_out(t, active) for t in tunnels]


@router.post("", response_model=VpnTunnelOut, status_code=status.HTTP_201_CREATED)
def create_tunnel(
    payload: VpnCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> VpnTunnelOut:
    blob = {"config": payload.config, "username": payload.username, "password": payload.password}
    tunnel = VpnTunnel(
        name=payload.name,
        type=payload.type,
        config_encrypted=encrypt(json.dumps(blob)),
        autostart=payload.autostart,
    )
    db.add(tunnel)
    db.commit()
    db.refresh(tunnel)
    audit(db, admin, "vpn.create", f"{tunnel.name} ({tunnel.type.value})")
    return _to_out(tunnel, vpnsvc.active_id())


@router.patch("/{tunnel_id}", response_model=VpnTunnelOut)
def update_tunnel(
    tunnel_id: int, payload: VpnUpdate, db: Session = Depends(get_db)
) -> VpnTunnelOut:
    tunnel = _get(db, tunnel_id)
    creds = _load(tunnel)
    if payload.name is not None:
        tunnel.name = payload.name
    if payload.autostart is not None:
        tunnel.autostart = payload.autostart
    changed_creds = False
    if payload.config is not None and payload.config.strip():
        creds["config"] = payload.config
        changed_creds = True
    if payload.username is not None:
        creds["username"] = payload.username
        changed_creds = True
    if payload.password is not None and payload.password != "":
        creds["password"] = payload.password
        changed_creds = True
    if changed_creds:
        tunnel.config_encrypted = encrypt(json.dumps(creds))
    db.commit()
    db.refresh(tunnel)
    return _to_out(tunnel, vpnsvc.active_id())


@router.delete("/{tunnel_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_tunnel(
    tunnel_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> Response:
    tunnel = _get(db, tunnel_id)
    if vpnsvc.active_id() == tunnel.id:
        vpnsvc.disconnect()
    name = tunnel.name
    db.delete(tunnel)
    db.commit()
    audit(db, admin, "vpn.delete", name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Connect / disconnect -------------------------------------------------


@router.post("/{tunnel_id}/connect", response_model=VpnStatusOut)
def connect_tunnel(
    tunnel_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> VpnStatusOut:
    tunnel = _get(db, tunnel_id)
    creds = _load(tunnel)
    result = vpnsvc.connect(
        tunnel.id, tunnel.type.value, creds["config"], creds["username"], creds["password"]
    )
    tunnel.last_error = "" if result.ok else result.message
    tunnel.active_since = datetime.now(UTC) if result.ok else None
    db.commit()
    audit(db, admin, "vpn.connect", f"{tunnel.name}: {'ok' if result.ok else result.message}")
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.message)
    st = vpnsvc.status()
    return VpnStatusOut(
        active_id=st.active_id, state=st.state, detail=st.detail,
        endpoint=st.endpoint, last_handshake=st.last_handshake,
    )


@router.post("/{tunnel_id}/disconnect", response_model=VpnStatusOut)
def disconnect_tunnel(
    tunnel_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> VpnStatusOut:
    tunnel = _get(db, tunnel_id)
    vpnsvc.disconnect()
    tunnel.active_since = None
    db.commit()
    audit(db, admin, "vpn.disconnect", tunnel.name)
    return VpnStatusOut(state="down")
