"""Cloud providers: admin CRUD, credential masking and rclone connection test."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..admin_ops import audit
from ..crypto import decrypt, encrypt
from ..db import get_db
from ..deps import require_admin
from ..models import CloudProvider, ProviderStatus, User
from ..providers_registry import (
    ProviderType,
    categories_json,
    config_error,
    get_type,
    registry_json,
    validate_config,
)
from ..rclone import TestResult, test_remote
from ..schemas import (
    ProviderCreate,
    ProviderOut,
    ProviderTestRequest,
    ProviderTestResult,
    ProviderUpdate,
)

router = APIRouter(
    prefix="/api/providers",
    tags=["providers"],
    dependencies=[Depends(require_admin)],
)

MASK = "••••••"


def _load_config(provider: CloudProvider) -> dict[str, str]:
    raw = decrypt(provider.config_encrypted)
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def _mask_config(type_key: str, config: dict[str, str]) -> dict[str, str]:
    pt = get_type(type_key)
    if pt is None:
        return {}
    masked: dict[str, str] = {}
    for f in pt.fields:
        value = config.get(f.key, "")
        masked[f.key] = MASK if (f.secret and value) else value
    return masked


def _to_out(provider: CloudProvider) -> ProviderOut:
    return ProviderOut(
        id=provider.id,
        name=provider.name,
        type=provider.type,
        status=provider.status,
        last_error=provider.last_error,
        last_tested_at=provider.last_tested_at,
        note=provider.note or "",
        created_at=provider.created_at,
        config=_mask_config(provider.type, _load_config(provider)),
    )


def _merge_config(
    type_key: str, existing: dict[str, str], incoming: dict[str, str]
) -> dict[str, str]:
    """Apply incoming values; keep existing secrets when the client sends the mask."""
    pt = get_type(type_key)
    if pt is None:
        return existing
    merged = dict(existing)
    for f in pt.fields:
        if f.key not in incoming:
            continue
        value = incoming[f.key]
        if f.secret and value in ("", MASK):
            continue  # untouched secret -> keep stored value
        merged[f.key] = value
    return merged


# --- Registry (for the dynamic form) --------------------------------------


@router.get("/types")
def provider_types() -> list[dict]:
    return registry_json()


@router.get("/categories")
def provider_categories() -> list[dict]:
    return categories_json()


# --- CRUD -----------------------------------------------------------------


@router.get("", response_model=list[ProviderOut])
def list_providers(db: Session = Depends(get_db)) -> list[ProviderOut]:
    providers = db.scalars(select(CloudProvider).order_by(CloudProvider.name)).all()
    return [_to_out(p) for p in providers]


@router.post("", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
def create_provider(
    payload: ProviderCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ProviderOut:
    pt = get_type(payload.type)
    if pt is None:
        raise HTTPException(status_code=400, detail=f"Unknown provider type '{payload.type}'")
    missing = validate_config(pt, payload.config)
    if missing:
        raise HTTPException(status_code=400, detail=f"Pflichtfelder fehlen: {', '.join(missing)}")
    error = config_error(pt, payload.config)
    if error:
        raise HTTPException(status_code=400, detail=error)

    provider = CloudProvider(
        name=payload.name,
        type=payload.type,
        config_encrypted=encrypt(json.dumps(payload.config)),
        status=ProviderStatus.UNKNOWN,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    audit(db, admin, "provider.create", f"{provider.name} ({provider.type})")
    return _to_out(provider)


def _get(db: Session, provider_id: int) -> CloudProvider:
    provider = db.get(CloudProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")
    return provider


@router.patch("/{provider_id}", response_model=ProviderOut)
def update_provider(
    provider_id: int, payload: ProviderUpdate, db: Session = Depends(get_db)
) -> ProviderOut:
    provider = _get(db, provider_id)
    if payload.name is not None:
        provider.name = payload.name
    if payload.config is not None:
        merged = _merge_config(provider.type, _load_config(provider), payload.config)
        pt = get_type(provider.type)
        error = config_error(pt, merged) if pt else None
        if error:
            raise HTTPException(status_code=400, detail=error)
        provider.config_encrypted = encrypt(json.dumps(merged))
        provider.status = ProviderStatus.UNKNOWN  # re-test after edits
    db.commit()
    db.refresh(provider)
    return _to_out(provider)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_provider(
    provider_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    provider = _get(db, provider_id)
    name = provider.name
    db.delete(provider)
    db.commit()
    audit(db, admin, "provider.delete", name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Connection test ------------------------------------------------------


def _run_test(
    pt: ProviderType, config: dict[str, str], subpath: str = ""
) -> tuple[TestResult, str | None]:
    """rclone listing test, then the type's write check and note.

    Returns the result and the provider note (``None``: couldn't tell, keep
    the old one).
    """
    error = config_error(pt, config)
    if error:
        return TestResult(False, error), None
    result = test_remote(pt.to_rclone_options(config), subpath)
    if result.ok and pt.write_check is not None:
        problem = pt.write_check(config)
        if problem:
            return TestResult(False, problem), None
    if result.ok and pt.test_note is not None:
        known, note = pt.test_note(config)
        if note:
            return TestResult(True, f"{result.message} — Hinweis: {note}"), note
        return result, ("" if known else None)
    return result, ("" if result.ok else None)


@router.post("/test", response_model=ProviderTestResult)
def test_draft(payload: ProviderTestRequest) -> ProviderTestResult:
    """Test an unsaved draft (config must contain real secrets)."""
    pt = get_type(payload.type)
    if pt is None:
        raise HTTPException(status_code=400, detail=f"Unknown provider type '{payload.type}'")
    missing = validate_config(pt, payload.config)
    if missing:
        return ProviderTestResult(ok=False, message=f"Pflichtfelder fehlen: {', '.join(missing)}")
    result, _note = _run_test(pt, payload.config, payload.subpath)
    return ProviderTestResult(ok=result.ok, message=result.message)


@router.post("/{provider_id}/test", response_model=ProviderOut)
def test_saved(provider_id: int, db: Session = Depends(get_db)) -> ProviderOut:
    """Test a stored provider and persist the resulting status."""
    provider = _get(db, provider_id)
    pt = get_type(provider.type)
    if pt is None:
        raise HTTPException(status_code=400, detail="Unknown provider type")
    result, note = _run_test(pt, _load_config(provider))
    provider.status = ProviderStatus.OK if result.ok else ProviderStatus.ERROR
    provider.last_error = "" if result.ok else result.message
    if note is not None:
        provider.note = note
        provider.note_checked_at = datetime.now(UTC)
    provider.last_tested_at = datetime.now(UTC)
    db.commit()
    db.refresh(provider)
    return _to_out(provider)
