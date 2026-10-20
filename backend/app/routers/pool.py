"""Multi-server pooling: expose this node's status and aggregate the fleet.

``GET /api/pool/status`` and ``POST /api/pool/control`` are what a peer serves
to a hub; both accept either an admin JWT or this node's shared pool token
(header ``X-Pool-Token``). Control via token additionally needs this node's
admin to have enabled it. Everything else is admin-only management + the
aggregated overview.
"""

from __future__ import annotations

import secrets

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import pool as pool_core
from .. import transfers as transfers_core
from ..admin_ops import audit, get_system_settings
from ..crypto import encrypt
from ..db import get_db
from ..deps import require_admin
from ..models import PoolPeer, Role, User
from ..schemas import (
    PoolNodeStatus,
    PoolOverviewOut,
    PoolPeerCreate,
    PoolPeerOut,
    PoolPeerUpdate,
    PoolSelfOut,
    PoolSelfUpdate,
    PoolTokenOut,
    PoolTotals,
    TransferControlIn,
    TransferControlOut,
)
from ..security import decode_access_token

router = APIRouter(prefix="/api/pool", tags=["pool"])


def _peer_out(peer: PoolPeer) -> PoolPeerOut:
    return PoolPeerOut(
        id=peer.id,
        name=peer.name,
        base_url=peer.base_url,
        enabled=peer.enabled,
        has_token=bool(peer.token_encrypted),
        created_at=peer.created_at,
    )


def _is_admin_bearer(db: Session, authorization: str | None) -> bool:
    if not authorization or not authorization.lower().startswith("bearer "):
        return False
    try:
        payload = decode_access_token(authorization[7:])
        user = db.get(User, int(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        return False
    return user is not None and user.active and user.role == Role.ADMIN


def _valid_pool_token(db: Session, x_pool_token: str | None) -> bool:
    expected = get_system_settings(db).pool_token
    return bool(
        expected
        and x_pool_token is not None
        and secrets.compare_digest(x_pool_token, expected)
    )


@router.get("/status", response_model=PoolNodeStatus)
def pool_status(
    x_pool_token: str | None = Header(default=None, alias="X-Pool-Token"),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> PoolNodeStatus:
    """This node's compact status. For hubs (via pool token) and local admins."""
    authorized = _valid_pool_token(db, x_pool_token) or _is_admin_bearer(db, authorization)
    if not authorized:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Pool-Token erforderlich"
        )
    return PoolNodeStatus(**pool_core.node_status(db))


@router.post("/control", response_model=TransferControlOut)
def pool_control(
    payload: TransferControlIn,
    x_pool_token: str | None = Header(default=None, alias="X-Pool-Token"),
    x_pool_hub: str | None = Header(default=None, alias="X-Pool-Hub"),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> TransferControlOut:
    """A hub controls this node's transfers — only if this node's admin allows it."""
    if _is_admin_bearer(db, authorization):
        caller = "lokal (Admin)"
    elif _valid_pool_token(db, x_pool_token):
        if not get_system_settings(db).pool_control_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Fernsteuerung auf dieser Box nicht freigegeben",
            )
        caller = f"Hub {(x_pool_hub or 'unbekannt').strip()[:100]}"
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Pool-Token erforderlich"
        )
    message = transfers_core.apply_control(db, payload.action)
    audit(db, None, f"pool.remote_{payload.action}", f"{message} – {caller}")
    return TransferControlOut(**transfers_core.control_state(db), message=message)


# --- This node's poolability (own token) ----------------------------------


@router.get("/self", response_model=PoolSelfOut)
def pool_self(
    _: User = Depends(require_admin), db: Session = Depends(get_db)
) -> PoolSelfOut:
    row = get_system_settings(db)
    return PoolSelfOut(
        pool_token=row.pool_token,
        token_set=bool(row.pool_token),
        control_enabled=row.pool_control_enabled,
    )


@router.put("/self", response_model=PoolSelfOut)
def update_self(
    payload: PoolSelfUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> PoolSelfOut:
    """Allow or forbid hubs to control this node's transfers."""
    row = get_system_settings(db)
    row.pool_control_enabled = payload.control_enabled
    db.commit()
    audit(db, admin, "pool.control_" + ("allow" if payload.control_enabled else "deny"))
    return pool_self(admin, db)


@router.post("/token", response_model=PoolTokenOut)
def rotate_token(
    admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> PoolTokenOut:
    """Generate (or rotate) this node's shared pool token so hubs can read it."""
    row = get_system_settings(db)
    row.pool_token = secrets.token_urlsafe(32)
    db.commit()
    audit(db, admin, "pool.token_rotate")
    return PoolTokenOut(pool_token=row.pool_token)


@router.delete("/token", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def clear_token(
    admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> Response:
    row = get_system_settings(db)
    row.pool_token = ""
    db.commit()
    audit(db, admin, "pool.token_clear")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Peers ----------------------------------------------------------------


@router.get("/peers", response_model=list[PoolPeerOut])
def list_peers(
    _: User = Depends(require_admin), db: Session = Depends(get_db)
) -> list[PoolPeerOut]:
    peers = db.scalars(select(PoolPeer).order_by(PoolPeer.name)).all()
    return [_peer_out(p) for p in peers]


@router.post("/peers", response_model=PoolPeerOut, status_code=status.HTTP_201_CREATED)
def add_peer(
    payload: PoolPeerCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> PoolPeerOut:
    peer = PoolPeer(
        name=payload.name.strip(),
        base_url=pool_core.normalize_base_url(payload.base_url),
        token_encrypted=encrypt(payload.token) if payload.token else "",
    )
    db.add(peer)
    db.commit()
    db.refresh(peer)
    audit(db, admin, "pool.peer_add", f"{peer.name} ({peer.base_url})")
    return _peer_out(peer)


@router.patch("/peers/{peer_id}", response_model=PoolPeerOut)
def update_peer(
    peer_id: int,
    payload: PoolPeerUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> PoolPeerOut:
    peer = db.get(PoolPeer, peer_id)
    if peer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Peer not found")
    if payload.name is not None:
        peer.name = payload.name.strip()
    if payload.base_url is not None:
        peer.base_url = pool_core.normalize_base_url(payload.base_url)
    if payload.enabled is not None:
        peer.enabled = payload.enabled
    if payload.token is not None:
        # Empty string clears the stored token; a value replaces it.
        peer.token_encrypted = encrypt(payload.token) if payload.token else ""
    db.commit()
    db.refresh(peer)
    audit(db, admin, "pool.peer_update", peer.name)
    return _peer_out(peer)


@router.delete(
    "/peers/{peer_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
def delete_peer(
    peer_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    peer = db.get(PoolPeer, peer_id)
    if peer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Peer not found")
    name = peer.name
    db.delete(peer)
    db.commit()
    audit(db, admin, "pool.peer_remove", name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/peers/{peer_id}/control", response_model=TransferControlOut)
def control_peer(
    peer_id: int,
    payload: TransferControlIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> TransferControlOut:
    """Forward one transfer action to a peer (it must have allowed control)."""
    peer = db.get(PoolPeer, peer_id)
    if peer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Peer not found")
    if not peer.enabled:
        raise HTTPException(status_code=409, detail="Peer ist deaktiviert")
    try:
        data = pool_core.control_peer(peer, payload.action)
    except pool_core.PeerControlError as exc:
        raise HTTPException(status_code=exc.status, detail=f"{peer.name}: {exc}") from exc
    result = TransferControlOut(**data)
    audit(db, admin, f"pool.peer_{payload.action}", f"{peer.name}: {result.message}")
    return result


# --- Aggregated overview --------------------------------------------------


@router.get("/overview", response_model=PoolOverviewOut)
def overview(
    _: User = Depends(require_admin), db: Session = Depends(get_db)
) -> PoolOverviewOut:
    """This node plus every enabled peer, polled concurrently, with fleet totals."""
    self_status = PoolNodeStatus(**pool_core.node_status(db))

    peers = list(db.scalars(select(PoolPeer).order_by(PoolPeer.name)))
    enabled = [p for p in peers if p.enabled]
    polled = pool_core.poll_peers(enabled)

    peer_nodes: list[PoolNodeStatus] = []
    for peer in peers:
        if not peer.enabled:
            peer_nodes.append(
                PoolNodeStatus(
                    name=peer.name,
                    base_url=peer.base_url,
                    peer_id=peer.id,
                    reachable=False,
                    error="deaktiviert",
                )
            )
            continue
        data = dict(polled.get(peer.id, {"reachable": False, "error": "keine Antwort"}))
        # The admin-chosen name and identity always win over the polled values.
        data["name"] = peer.name
        data["base_url"] = peer.base_url
        data["peer_id"] = peer.id
        peer_nodes.append(PoolNodeStatus(**data))

    online = [self_status, *[n for n in peer_nodes if n.reachable]]
    totals = PoolTotals(
        nodes=1 + len(peers),
        nodes_online=len(online),
        media_total=sum(n.media_total for n in online),
        active_transfers=sum(n.active_transfers for n in online),
        throughput_kbps=round(sum(n.throughput_kbps for n in online), 1),
        disk_free=sum(n.disk_free for n in online),
        disk_total=sum(n.disk_total for n in online),
    )
    return PoolOverviewOut(self_node=self_status, peers=peer_nodes, totals=totals)
