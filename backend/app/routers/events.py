"""Server-Sent Events: a periodic state snapshot for the live dashboard.

SSE (not WebSocket) keeps it simple and proxy-friendly. EventSource can't send
an Authorization header, so the JWT is passed as a ``?token=`` query parameter.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import jwt
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..bandwidth import effective_bwlimit, get_policy, parse_schedule, should_start
from ..db import SessionLocal
from ..models import (
    CloudProvider,
    MediaItem,
    Role,
    TransferJob,
    TransferStatus,
    UploadFolder,
    User,
)
from ..security import decode_access_token
from ..storage import accessible_folder_ids
from ..transfers import get_live

router = APIRouter(tags=["events"])

SNAPSHOT_INTERVAL = 2.0  # seconds


def _user_from_token(token: str) -> int:
    try:
        payload = decode_access_token(token)
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc


def _accessible_folders(db: Session, user: User) -> list[UploadFolder]:
    if user.role == Role.ADMIN:
        return list(db.scalars(select(UploadFolder).order_by(UploadFolder.name)))
    ids = accessible_folder_ids(db, user)
    if not ids:
        return []
    return list(
        db.scalars(
            select(UploadFolder).where(UploadFolder.id.in_(ids)).order_by(UploadFolder.name)
        )
    )


def build_snapshot(user_id: int) -> dict | None:
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or not user.active:
            return None

        # Per-folder media counts by status.
        rows = db.execute(
            select(MediaItem.folder_id, MediaItem.status, func.count())
            .group_by(MediaItem.folder_id, MediaItem.status)
        ).all()
        by_folder: dict[int, dict[str, int]] = {}
        for folder_id, st, count in rows:
            by_folder.setdefault(folder_id, {})[st.value] = count

        folders = []
        for f in _accessible_folders(db, user):
            counts = by_folder.get(f.id, {})
            total = sum(counts.values())
            folders.append(
                {
                    "id": f.id,
                    "name": f.name,
                    "total": total,
                    "done": counts.get("done", 0),
                    "uploading": counts.get("uploading", 0),
                    "queued": counts.get("queued", 0),
                    "failed": counts.get("failed", 0),
                }
            )

        snapshot: dict = {"folders": folders}

        # Admin-only: transfer overview + bandwidth.
        if user.role == Role.ADMIN:
            status_rows = db.execute(
                select(TransferJob.status, func.count()).group_by(TransferJob.status)
            ).all()
            counts = {st.value: c for st, c in status_rows}

            live = get_live()
            running = list(
                db.scalars(
                    select(TransferJob).where(TransferJob.status == TransferStatus.RUNNING)
                )
            )
            media_names = dict(db.execute(select(MediaItem.id, MediaItem.filename)).all())
            prov_names = dict(db.execute(select(CloudProvider.id, CloudProvider.name)).all())
            active = []
            for j in running:
                lv = live.get(j.id, {})
                total = lv.get("total", 0)
                done = lv.get("bytes", 0)
                active.append(
                    {
                        "id": j.id,
                        "filename": media_names.get(j.media_id, ""),
                        "provider": prov_names.get(j.provider_id, ""),
                        "bytes": done,
                        "total": total,
                        "progress": (done / total) if total else 0.0,
                        "kbps": lv.get("kbps", 0.0),
                    }
                )

            policy = get_policy(db)
            schedule = parse_schedule(policy.schedule_json)
            gated, reason = should_start(
                policy.enabled,
                policy.min_bandwidth_kbps,
                policy.last_kbps,
                policy.last_measured_at,
                datetime.utcnow(),
            )
            snapshot["transfers"] = {"counts": counts, "active": active}
            snapshot["bandwidth"] = {
                "enabled": policy.enabled,
                "effective_bwlimit_kbps": effective_bwlimit(
                    schedule, policy.bwlimit_kbps, datetime.now()
                ),
                "last_kbps": policy.last_kbps,
                "gated": not gated,
                "gate_reason": reason,
            }
        return snapshot


@router.get("/api/events")
async def events(request: Request, token: str) -> StreamingResponse:
    user_id = _user_from_token(token)

    async def stream():
        while True:
            if await request.is_disconnected():
                break
            snapshot = await asyncio.to_thread(build_snapshot, user_id)
            if snapshot is None:
                break
            yield f"data: {json.dumps(snapshot)}\n\n"
            await asyncio.sleep(SNAPSHOT_INTERVAL)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
