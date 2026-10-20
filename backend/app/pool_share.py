"""Fair share of one uplink between pool boxes (run by the hub).

The boxes ranked under "shared line" (pool priority level 1–3) split a
capacity the admin enters. Every pool-watch round the hub hands each box that
is sending ``capacity / number of senders``; boxes waiting idle get the share
they would have if they started now, so a newcomer can't grab the whole line
before the next round. Runs after the priority rule, so boxes it just paused
don't count as senders.

Each box applies its share on top of its own limit (the lower wins) from its
NEXT upload on — rclone takes the limit when an upload starts. The share lapses
on the box after a few minutes unless renewed (``bandwidth.POOL_LIMIT_TTL``),
so a hub that disappears never leaves anyone throttled.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from . import bandwidth
from . import pool as pool_core
from .admin_ops import get_system_settings
from .models import PoolPeer
from .pool_priority import LEVELS, SELF, Node

logger = logging.getLogger(__name__)

MIN_SHARE_KBPS = 16  # never hand out a share too small to make progress


def shares(nodes: list[Node], capacity_kbps: int) -> dict[str, int]:
    """KiB/s per reachable member box. Pure."""
    members = [n for n in nodes if n.level in LEVELS and n.reachable]
    senders = [n for n in members if n.pending > 0 and not n.paused]
    if not members or capacity_kbps <= 0:
        return {}
    per_sender = capacity_kbps // max(1, len(senders))
    per_newcomer = capacity_kbps // (len(senders) + 1)
    return {
        n.key: max(MIN_SHARE_KBPS, per_sender if n in senders else per_newcomer)
        for n in members
    }


_lock = threading.Lock()
_limited: set[str] = set()  # boxes we currently hand a share to
_errors: dict[str, str] = {}


def reset_state() -> None:
    with _lock:
        _limited.clear()
        _errors.clear()


def apply_round(
    db: Session, peers: list[PoolPeer], nodes: list[Node]
) -> list[tuple[str, str, bool | None]]:
    """Hand out (or, when switched off, withdraw) shares. Lines only for problems."""
    settings = get_system_settings(db)
    targets = shares(nodes, settings.pool_share_kbps) if settings.pool_share_enabled else {}
    reachable = {n.key for n in nodes if n.reachable}
    with _lock:
        # Boxes that left the line (or sharing was switched off) get their share lifted.
        for key in _limited - set(targets):
            if key in reachable:
                targets.setdefault(key, 0)
    peer_by_key = {f"peer:{p.id}": p for p in peers}
    by_key = {n.key: n for n in nodes}
    now = datetime.now(UTC).replace(tzinfo=None)
    lines: list[tuple[str, str, bool | None]] = []
    for key, kbps in targets.items():
        try:
            if key == SELF:
                bandwidth.set_pool_limit(db, kbps, "Pool (diese Box)", now)
            else:
                pool_core.limit_peer(peer_by_key[key], kbps)
        except pool_core.PeerControlError as exc:
            message = str(exc)
            with _lock:
                if _errors.get(key) != message:
                    _errors[key] = message
                    name = by_key[key].name
                    lines.append((key, f"⚠️ {name}: Leitungsanteil nicht setzbar – {message}", None))
            continue
        with _lock:
            _errors.pop(key, None)
            if kbps:
                _limited.add(key)
            else:
                _limited.discard(key)
    return lines
