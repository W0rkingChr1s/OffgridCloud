"""Upload handover for the companion app (app.handover, Companion PLAN.md §3.6).

Only reachable through the BLE bridge: its tokens name the paired device
(``dev``), whose own Wi-Fi key and session this is. Called from the web UI the
endpoints answer 403.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from .. import handover
from ..db import get_db
from ..deps import get_current_user, get_token_payload
from ..models import User
from ..schemas import HandoverConnectionOut, HandoverStart, HandoverStatusOut

router = APIRouter(prefix="/api/uplink", tags=["uplink"])


def _device_id(payload: dict = Depends(get_token_payload)) -> int:
    device_id = payload.get("dev")
    if not isinstance(device_id, int):
        raise HTTPException(
            status_code=403, detail="Nur aus der OffgridCloud-App über Bluetooth möglich."
        )
    return device_id


@router.get("/handover", response_model=HandoverStatusOut)
def handover_status(
    device_id: int = Depends(_device_id),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return handover.status(db, user, device_id)


@router.post("/handover", response_model=HandoverConnectionOut)
def start_handover(
    payload: HandoverStart | None = None,
    device_id: int = Depends(_device_id),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    confirm = payload.confirm_pause if payload else False
    try:
        return handover.start(db, user, device_id, confirm_pause=confirm)
    except handover.HandoverError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


@router.delete("/handover", status_code=204, response_class=Response)
def stop_handover(
    device_id: int = Depends(_device_id),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    handover.stop(db, "von der App beendet", device_id=device_id)
    return Response(status_code=204)
