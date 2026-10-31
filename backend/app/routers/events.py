"""Server-Sent Events: a periodic state snapshot for the live dashboard.

SSE (not WebSocket) keeps it simple and proxy-friendly. EventSource can't send
an Authorization header, so the JWT is passed as a ``?token=`` query parameter.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime

import jwt
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import notices
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
from ..network import metered_uplink
from ..security import decode_access_token
from ..storage import accessible_folder_ids
from ..transfers import get_live

router = APIRouter(tags=["events"])

SNAPSHOT_INTERVAL = 2.0  # seconds
# A stream ends after this long; EventSource reconnects on its own (after
# RETRY_MS). This bounds streams whose client vanished without a FIN — an iPad
# that went to sleep behind Caddy can otherwise keep one open for hours.
STREAM_MAX_SECONDS = 300.0
RETRY_MS = 3000


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
        db.scalars(select(UploadFolder).where(UploadFolder.id.in_(ids)).order_by(UploadFolder.name))
    )


def build_snapshot(user_id: int) -> dict | None:
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or not user.active:
            return None

        # Per-folder media counts by status.
        rows = db.execute(
            select(MediaItem.folder_id, MediaItem.status, func.count()).group_by(
                MediaItem.folder_id, MediaItem.status
            )
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
                db.scalars(select(TransferJob).where(TransferJob.status == TransferStatus.RUNNING))
            )
            # Only the names of running jobs — not every media row, which on a
            # box with thousands of files made each 2 s frame a full table scan.
            running_media = {j.media_id for j in running}
            media_names = (
                dict(
                    db.execute(
                        select(MediaItem.id, MediaItem.filename).where(
                            MediaItem.id.in_(running_media)
                        )
                    ).all()
                )
                if running_media
                else {}
            )
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
            # Operational status blips (startup / reconnect / bandwidth) — these
            # can carry the external IP, so keep them to admins like the rest.
            snapshot["notices"] = notices.recent()
            snapshot["transfers"] = {"counts": counts, "active": active}
            metered, uplink = metered_uplink()
            snapshot["bandwidth"] = {
                "enabled": policy.enabled,
                # Uplink in use (cached 30 s): lets the dashboard flag a phone.
                "metered": metered,
                "uplink": uplink,
                "effective_bwlimit_kbps": effective_bwlimit(
                    schedule, policy.bwlimit_kbps, datetime.now()
                ),
                "last_kbps": policy.last_kbps,
                "gated": not gated,
                "gate_reason": reason,
            }
        return snapshot


# Per-user snapshot cache: however many streams a user has open (tabs, devices,
# streams of clients that silently went away), the snapshot is built at most
# once per SNAPSHOT_INTERVAL.
_cache: dict[int, tuple[float, dict | None]] = {}
_locks: dict[int, asyncio.Lock] = {}


async def cached_snapshot(user_id: int) -> dict | None:
    lock = _locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        hit = _cache.get(user_id)
        # Slightly under the interval so a stream's own next tick is a miss.
        if hit and time.monotonic() - hit[0] < SNAPSHOT_INTERVAL * 0.9:
            return hit[1]
        snapshot = await asyncio.to_thread(build_snapshot, user_id)
        _cache[user_id] = (time.monotonic(), snapshot)
        return snapshot


@router.get("/api/events")
async def events(request: Request, token: str) -> StreamingResponse:
    user_id = _user_from_token(token)

    async def stream():
        yield f"retry: {RETRY_MS}\n\n"
        deadline = time.monotonic() + STREAM_MAX_SECONDS
        while time.monotonic() < deadline:
            if await request.is_disconnected():
                break
            snapshot = await cached_snapshot(user_id)
            if snapshot is None:
                break
            yield f"data: {json.dumps(snapshot)}\n\n"
            await asyncio.sleep(SNAPSHOT_INTERVAL)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
