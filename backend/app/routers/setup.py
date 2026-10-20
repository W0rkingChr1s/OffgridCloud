"""First-boot setup wizard (prebuilt image) — the only API open in setup mode.

Unauthenticated by design: there is no account yet. Every endpoint refuses
once setup is done, so this never becomes a back door. See ``setup_wizard.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import setup_wizard
from ..config import get_settings
from ..db import get_db
from ..network import scan_wifi
from ..schemas import SetupCompleteOut, SetupCompleteRequest, SetupStatusOut

router = APIRouter(prefix="/api/setup", tags=["setup"])


def _require_setup_mode() -> dict:
    if not setup_wizard.setup_required():
        raise HTTPException(status_code=409, detail="Die Box ist bereits eingerichtet.")
    return setup_wizard.read_state(get_settings().data_dir)


@router.get("/status", response_model=SetupStatusOut)
def setup_status() -> SetupStatusOut:
    if not setup_wizard.setup_required():
        return SetupStatusOut(required=False)
    state = setup_wizard.read_state(get_settings().data_dir)
    return SetupStatusOut(
        required=True,
        code_required=setup_wizard.code_required(state),
        suggested_hostname=setup_wizard.suggested_hostname(state),
        country=str(state.get("country") or "DE"),
    )


@router.get("/wifi", response_model=list[str])
def setup_wifi(_: dict = Depends(_require_setup_mode)) -> list[str]:
    # Live scan first; while the radio hosts the setup AP it often sees nothing,
    # so the scan setup-ap.sh took before raising the AP fills the gaps.
    return setup_wizard.merge_ssids(
        scan_wifi(), setup_wizard.read_scan(get_settings().setup_scan_file)
    )


@router.post("/complete", response_model=SetupCompleteOut)
def setup_complete(
    payload: SetupCompleteRequest,
    state: dict = Depends(_require_setup_mode),
    db: Session = Depends(get_db),
) -> SetupCompleteOut:
    throttle = setup_wizard.throttle
    wait = throttle.retry_after()
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail=f"Zu viele falsche Setup-Codes. Bitte {int(wait) + 1} s warten.",
        )
    if not setup_wizard.code_matches(state, payload.code):
        throttle.failure()
        raise HTTPException(status_code=403, detail="Setup-Code ist falsch.")
    throttle.success()

    try:
        result = setup_wizard.complete(
            db,
            setup_wizard.SetupInput(
                admin_email=payload.admin_email,
                admin_password=payload.admin_password,
                admin_name=payload.admin_name,
                hostname=payload.hostname,
                wifi_ssid=payload.wifi_ssid,
                wifi_password=payload.wifi_password,
                fallback_password=payload.fallback_password,
                country=payload.country,
            ),
        )
    except setup_wizard.SetupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    settings = get_settings()
    url = (
        f"https://{result.hostname}.local"
        if settings.https_apply_command.strip()
        else f"http://{result.hostname}.local:8000"
    )
    return SetupCompleteOut(
        hostname=result.hostname,
        url=url,
        wifi_ssid=result.wifi_ssid,
        finish_started=result.finish_started,
    )
