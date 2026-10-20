"""Multi-server pooling: expose this node's status and aggregate the fleet.

``GET /api/pool/status`` and ``POST /api/pool/control`` are what a peer serves
to a hub; both accept either an admin JWT or an access token this node issued
to that hub (header ``X-Pool-Token``). Each hub gets its own token, scoped to
"read" (status only) or "control" (also transfer actions), so one hub can be
revoked without touching the others. Everything else is admin-only management
+ the aggregated overview.
"""

from __future__ import annotations

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import pool as pool_core
from .. import transfers as transfers_core
from ..admin_ops import audit
from ..crypto import encrypt
from ..db import get_db
from ..deps import require_admin
from ..models import PoolAccessToken, PoolPeer, PoolTokenScope, Role, User
from ..schemas import (
    PoolAccessTokenCreate,
    PoolAccessTokenCreated,
    PoolAccessTokenOut,
    PoolAccessTokenUpdate,
    PoolNodeStatus,
    PoolOverviewOut,
    PoolPeerCreate,
    PoolPeerOut,
    PoolPeerUpdate,
    PoolTotals,
    TransferControlIn,
    TransferControlOut,
)
from ..security import decode_access_token

router = APIRouter(prefix="/api/pool", tags=["pool"])

_SCOPE_LABEL = {PoolTokenScope.READ: "lesen", PoolTokenScope.CONTROL: "steuern"}


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


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Pool-Token erforderlich"
    )


@router.get("/status", response_model=PoolNodeStatus)
def pool_status(
    x_pool_token: str | None = Header(default=None, alias="X-Pool-Token"),
    x_pool_hub: str | None = Header(default=None, alias="X-Pool-Hub"),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> PoolNodeStatus:
    """This node's compact status. For hubs (via their token) and local admins."""
    if _is_admin_bearer(db, authorization):
        return PoolNodeStatus(**pool_core.node_status(db, control_enabled=True))
    token = pool_core.authenticate(db, x_pool_token, x_pool_hub)
    if token is None:
        raise _unauthorized()
    can_control = token.scope == PoolTokenScope.CONTROL
    return PoolNodeStatus(**pool_core.node_status(db, control_enabled=can_control))


@router.post("/control", response_model=TransferControlOut)
def pool_control(
    payload: TransferControlIn,
    x_pool_token: str | None = Header(default=None, alias="X-Pool-Token"),
    x_pool_hub: str | None = Header(default=None, alias="X-Pool-Hub"),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> TransferControlOut:
    """A hub controls this node's transfers — only with a "control" token."""
    if _is_admin_bearer(db, authorization):
        caller = "lokal (Admin)"
    else:
        token = pool_core.authenticate(db, x_pool_token, x_pool_hub)
        if token is None:
            raise _unauthorized()
        if token.scope != PoolTokenScope.CONTROL:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Dieses Pool-Token darf nur lesen, nicht steuern",
            )
        caller = f"Hub „{token.name}“"
        if token.last_used_hub:
            caller += f" ({token.last_used_hub})"
    message = transfers_core.apply_control(db, payload.action)
    audit(db, None, f"pool.remote_{payload.action}", f"{message} – {caller}")
    return TransferControlOut(**transfers_core.control_state(db), message=message)


# --- Access tokens this node issues, one per hub ---------------------------


def _token_or_404(db: Session, token_id: int) -> PoolAccessToken:
    row = db.get(PoolAccessToken, token_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    return row


@router.get("/tokens", response_model=list[PoolAccessTokenOut])
def list_tokens(
    _: User = Depends(require_admin), db: Session = Depends(get_db)
) -> list[PoolAccessTokenOut]:
    rows = db.scalars(select(PoolAccessToken).order_by(PoolAccessToken.name))
    return [PoolAccessTokenOut.model_validate(r) for r in rows]


@router.post("/tokens", response_model=PoolAccessTokenCreated, status_code=201)
def create_token(
    payload: PoolAccessTokenCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> PoolAccessTokenCreated:
    """Issue a token for one hub. The plaintext is returned only in this response."""
    row, raw = pool_core.issue_token(db, payload.name.strip(), payload.scope)
    audit(db, admin, "pool.token_create", f"{row.name} ({_SCOPE_LABEL[row.scope]})")
    return PoolAccessTokenCreated(
        **PoolAccessTokenOut.model_validate(row).model_dump(), token=raw
    )


@router.patch("/tokens/{token_id}", response_model=PoolAccessTokenOut)
def update_token(
    token_id: int,
    payload: PoolAccessTokenUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> PoolAccessTokenOut:
    row = _token_or_404(db, token_id)
    if payload.name is not None:
        row.name = payload.name.strip()
    if payload.scope is not None:
        row.scope = payload.scope
    db.commit()
    db.refresh(row)
    audit(db, admin, "pool.token_update", f"{row.name} ({_SCOPE_LABEL[row.scope]})")
    return PoolAccessTokenOut.model_validate(row)


@router.delete(
    "/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
def delete_token(
    token_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    """Revoke one hub's access immediately; other hubs are unaffected."""
    row = _token_or_404(db, token_id)
    name = row.name
    db.delete(row)
    db.commit()
    audit(db, admin, "pool.token_revoke", name)
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
