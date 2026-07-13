"""Network redundancy: known Wi-Fi networks + the fallback access point.

Admin-only. The app stores the desired config and reports live state; actually
flipping the box between "join a network" and "host our own AP" is done by the
opt-in privileged helper and the root watchdog (see ``app/network.py`` and
``deploy/netfallback/``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..admin_ops import audit
from ..config import get_settings
from ..db import get_db
from ..deps import require_admin
from ..models import KnownNetwork, User
from ..network import (
    apply_config,
    get_network_settings,
    get_status,
    scan_wifi,
    set_known_psk,
    status_dict,
    validate_country,
    validate_passphrase,
    validate_ssid,
)
from ..schemas import (
    KnownNetworkCreate,
    KnownNetworkOut,
    KnownNetworkUpdate,
    NetworkApplyResult,
    NetworkOverviewOut,
    NetworkSettingsOut,
    NetworkSettingsUpdate,
    NetworkStatusOut,
    WifiScanOut,
)

router = APIRouter(prefix="/api/network", tags=["network"], dependencies=[Depends(require_admin)])


def _settings_out(row) -> NetworkSettingsOut:
    return NetworkSettingsOut(
        fallback_enabled=row.fallback_enabled,
        ap_ssid=row.ap_ssid,
        ap_hidden=row.ap_hidden,
        ap_address=row.ap_address,
        country_code=row.country_code,
        check_interval=row.check_interval,
        fail_threshold=row.fail_threshold,
        ap_has_password=bool(row.ap_psk_encrypted),
    )


def _known_out(n: KnownNetwork) -> KnownNetworkOut:
    return KnownNetworkOut(
        id=n.id,
        ssid=n.ssid,
        priority=n.priority,
        autoconnect=n.autoconnect,
        has_password=bool(n.psk_encrypted),
        created_at=n.created_at,
    )


def _live_status() -> NetworkStatusOut:
    status = get_status(apply_wired=bool(get_settings().net_apply_command.strip()))
    return NetworkStatusOut(**status_dict(status))


@router.get("", response_model=NetworkOverviewOut)
def overview(_: User = Depends(require_admin), db: Session = Depends(get_db)) -> NetworkOverviewOut:
    row = get_network_settings(db)
    known = db.scalars(
        select(KnownNetwork).order_by(KnownNetwork.priority.desc(), KnownNetwork.id)
    ).all()
    return NetworkOverviewOut(
        status=_live_status(),
        settings=_settings_out(row),
        known_networks=[_known_out(n) for n in known],
    )


@router.get("/status", response_model=NetworkStatusOut)
def network_status(_: User = Depends(require_admin)) -> NetworkStatusOut:
    return _live_status()


@router.put("/settings", response_model=NetworkSettingsOut)
def update_settings(
    payload: NetworkSettingsUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> NetworkSettingsOut:
    row = get_network_settings(db)
    changed: list[str] = []
    try:
        if payload.ap_ssid is not None:
            row.ap_ssid = validate_ssid(payload.ap_ssid)
            changed.append("ap_ssid")
        if payload.ap_password is not None:
            from ..crypto import encrypt

            psk = validate_passphrase(payload.ap_password, allow_empty=True)
            row.ap_psk_encrypted = encrypt(psk) if psk else ""
            changed.append("ap_password")
        if payload.country_code is not None:
            row.country_code = validate_country(payload.country_code)
            changed.append("country")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if payload.fallback_enabled is not None:
        row.fallback_enabled = payload.fallback_enabled
        changed.append(f"fallback={payload.fallback_enabled}")
    if payload.ap_hidden is not None:
        row.ap_hidden = payload.ap_hidden
        changed.append("ap_hidden")
    if payload.ap_address is not None:
        row.ap_address = payload.ap_address.strip()
        changed.append("ap_address")
    if payload.check_interval is not None:
        row.check_interval = payload.check_interval
        changed.append("check_interval")
    if payload.fail_threshold is not None:
        row.fail_threshold = payload.fail_threshold
        changed.append("fail_threshold")

    if changed:
        db.commit()
        db.refresh(row)
        audit(db, admin, "network.settings", ", ".join(changed))
    return _settings_out(row)


@router.get("/known", response_model=list[KnownNetworkOut])
def list_known(
    _: User = Depends(require_admin), db: Session = Depends(get_db)
) -> list[KnownNetworkOut]:
    known = db.scalars(
        select(KnownNetwork).order_by(KnownNetwork.priority.desc(), KnownNetwork.id)
    ).all()
    return [_known_out(n) for n in known]


@router.post("/known", response_model=KnownNetworkOut, status_code=201)
def add_known(
    payload: KnownNetworkCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> KnownNetworkOut:
    try:
        ssid = validate_ssid(payload.ssid)
        network = KnownNetwork(
            ssid=ssid, priority=payload.priority, autoconnect=payload.autoconnect
        )
        set_known_psk(network, payload.password, allow_empty=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.add(network)
    db.commit()
    db.refresh(network)
    audit(db, admin, "network.known.add", ssid)
    return _known_out(network)


@router.put("/known/{network_id}", response_model=KnownNetworkOut)
def update_known(
    network_id: int,
    payload: KnownNetworkUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> KnownNetworkOut:
    network = db.get(KnownNetwork, network_id)
    if network is None:
        raise HTTPException(status_code=404, detail="Netzwerk nicht gefunden")
    try:
        if payload.password is not None:
            set_known_psk(network, payload.password, allow_empty=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.priority is not None:
        network.priority = payload.priority
    if payload.autoconnect is not None:
        network.autoconnect = payload.autoconnect
    db.commit()
    db.refresh(network)
    audit(db, admin, "network.known.update", network.ssid)
    return _known_out(network)


@router.delete(
    "/known/{network_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_known(
    network_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    network = db.get(KnownNetwork, network_id)
    if network is None:
        raise HTTPException(status_code=404, detail="Netzwerk nicht gefunden")
    ssid = network.ssid
    db.delete(network)
    db.commit()
    audit(db, admin, "network.known.delete", ssid)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/apply", response_model=NetworkApplyResult)
def apply(
    admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> NetworkApplyResult:
    """Export the config and (if wired up) apply it via the privileged helper."""
    result = apply_config(db)
    audit(db, admin, "network.apply", "ok" if result.ok else "export-only")
    return NetworkApplyResult(ok=result.ok, message=result.message, output=result.output)


@router.post("/scan", response_model=WifiScanOut)
def scan(_: User = Depends(require_admin)) -> WifiScanOut:
    return WifiScanOut(ssids=scan_wifi())
