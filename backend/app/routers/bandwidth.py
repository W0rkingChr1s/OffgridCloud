"""Bandwidth policy: admin view and update."""

from __future__ import annotations

import json
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..admin_ops import audit, get_system_settings
from ..bandwidth import (
    effective_bwlimit,
    get_policy,
    measure_probe,
    metered_gate,
    metered_used_today,
    parse_schedule,
    record_measurement,
    should_start,
)
from ..config import get_settings as get_app_settings
from ..db import get_db
from ..deps import require_admin
from ..models import BandwidthPolicy, User
from ..network import metered_uplink
from ..schemas import BandwidthPolicyUpdate, BandwidthStatusOut, BandwidthWindow

router = APIRouter(
    prefix="/api/bandwidth",
    tags=["bandwidth"],
    dependencies=[Depends(require_admin)],
)


def _status(policy: BandwidthPolicy) -> BandwidthStatusOut:
    schedule = parse_schedule(policy.schedule_json)
    metered, uplink = metered_uplink()
    used = metered_used_today(policy, date.today().isoformat())
    ok, reason, _ = metered_gate(
        policy.metered_mode, policy.metered_budget_mb, used, metered, uplink
    )
    if ok:
        ok, reason = should_start(
            policy.enabled,
            policy.min_bandwidth_kbps,
            policy.last_kbps,
            policy.last_measured_at,
            datetime.utcnow(),
        )
    return BandwidthStatusOut(
        enabled=policy.enabled,
        min_bandwidth_kbps=policy.min_bandwidth_kbps,
        bwlimit_kbps=policy.bwlimit_kbps,
        schedule=[BandwidthWindow(**w) for w in schedule],
        last_kbps=policy.last_kbps,
        last_measured_at=policy.last_measured_at,
        effective_bwlimit_kbps=effective_bwlimit(schedule, policy.bwlimit_kbps, datetime.now()),
        gated=not ok,
        gate_reason=reason,
        metered_mode=policy.metered_mode,
        metered_budget_mb=policy.metered_budget_mb,
        metered_used_bytes=used,
        metered=metered,
        uplink=uplink,
    )


@router.get("", response_model=BandwidthStatusOut)
def get_bandwidth(
    _: User = Depends(require_admin), db: Session = Depends(get_db)
) -> BandwidthStatusOut:
    return _status(get_policy(db))


@router.put("", response_model=BandwidthStatusOut)
def update_bandwidth(
    payload: BandwidthPolicyUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> BandwidthStatusOut:
    policy = get_policy(db)
    if payload.enabled is not None:
        policy.enabled = payload.enabled
    if payload.min_bandwidth_kbps is not None:
        policy.min_bandwidth_kbps = payload.min_bandwidth_kbps
    if payload.bwlimit_kbps is not None:
        policy.bwlimit_kbps = payload.bwlimit_kbps
    if payload.schedule is not None:
        policy.schedule_json = json.dumps([w.model_dump() for w in payload.schedule])
    if payload.metered_mode is not None:
        policy.metered_mode = payload.metered_mode
    if payload.metered_budget_mb is not None:
        policy.metered_budget_mb = payload.metered_budget_mb
    db.commit()
    db.refresh(policy)
    audit(
        db,
        admin,
        "bandwidth.update",
        f"enabled={policy.enabled} bwlimit={policy.bwlimit_kbps} min={policy.min_bandwidth_kbps}"
        f" metered={policy.metered_mode}/{policy.metered_budget_mb}MB",
    )
    return _status(policy)


@router.post("/probe", response_model=BandwidthStatusOut)
def run_probe(
    _: User = Depends(require_admin), db: Session = Depends(get_db)
) -> BandwidthStatusOut:
    """Actively measure bandwidth by downloading a test file.

    Zero-config: uses the built-in default target unless an admin has set a
    custom probe URL under System — so "measure now" just works.
    """
    url = get_system_settings(db).probe_url.strip() or get_app_settings().default_probe_url
    kbps, error = measure_probe(url)
    if kbps <= 0:
        detail = "Messung fehlgeschlagen (Testziel nicht erreichbar?)"
        if error:
            detail = f"{detail} — {error}"
        raise HTTPException(status_code=502, detail=detail)
    record_measurement(db, kbps)
    return _status(get_policy(db))
