"""Priorities between boxes that share one uplink (run by the pool hub).

The admin ranks boxes 1 (high) … 3 (low); boxes left at 0 are not managed.
Every pool-watch round the hub looks at the managed boxes: while a box with a
higher rank has something to send, it pauses the lower-ranked boxes that are
also waiting to send, and resumes them once the line is theirs again. Only
makes sense for boxes on the SAME line — ranking boxes with their own uplinks
would just waste their bandwidth, so nothing is managed unless the admin opts
each box in.

The hub only ever undoes pauses it made itself (``priority_paused``, stored so
a hub restart doesn't strand a box). A manual pause on a box is left alone;
if someone at a box resumes a priority pause by hand, the hub respects that
until the higher-ranked box is done.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from sqlalchemy.orm import Session

from . import pool as pool_core
from . import transfers as transfers_core
from .admin_ops import get_system_settings
from .models import PoolPeer

logger = logging.getLogger(__name__)

LEVELS = (1, 2, 3)  # 1 = highest
SELF = "self"


@dataclass
class Node:
    key: str  # "self" or "peer:<id>"
    name: str
    level: int  # 0 = not managed
    reachable: bool
    paused: bool  # uploads paused on the box right now (by anyone)
    pending: int  # transfers waiting or running
    owned: bool  # paused by this priority rule


@dataclass
class Decision:
    key: str
    action: str  # "pause" | "resume" | "release"  (release: stop owning, no call)
    reason: str = ""


def decide(nodes: list[Node], overrides: set[str]) -> tuple[list[Decision], set[str]]:
    """What to do this round. Pure: returns the decisions and the new overrides.

    ``overrides`` holds boxes someone resumed by hand during a priority pause;
    they are left running until no higher-ranked box needs the line anymore.
    """
    managed = [n for n in nodes if n.level in LEVELS and n.reachable]
    # A box claims the line if it has work and is not paused (a box we paused
    # still has work queued, but must not block anyone).
    claiming = [n for n in managed if n.pending > 0 and not n.paused]
    top = min((n.level for n in claiming), default=None)
    top_names = ", ".join(sorted(n.name for n in claiming if n.level == top))

    decisions: list[Decision] = []
    new_overrides = set(overrides)
    for node in managed:
        must_wait = top is not None and node.level > top and node.pending > 0
        if node.owned and not node.paused:
            # Resumed by hand while we held it: hands off until the rule lets go.
            decisions.append(Decision(node.key, "release"))
            new_overrides.add(node.key)
            continue
        if not must_wait:
            new_overrides.discard(node.key)
            if node.owned and node.paused:
                decisions.append(Decision(node.key, "resume", "Leitung wieder frei"))
            continue
        if not node.paused and node.key not in new_overrides:
            decisions.append(Decision(node.key, "pause", f"Vorrang für {top_names}"))
    # Owned boxes that are no longer managed (rank removed) get their uploads back.
    for node in nodes:
        if node.owned and node.paused and node.reachable and node.level not in LEVELS:
            decisions.append(Decision(node.key, "resume", "nicht mehr in der Reihenfolge"))
    return decisions, new_overrides


_lock = threading.Lock()
_overrides: set[str] = set()
_errors: dict[str, str] = {}  # last control error per box, to report it only once


def reset_state() -> None:
    with _lock:
        _overrides.clear()
        _errors.clear()


def _pending(status: dict) -> int:
    if status.get("pending_transfers") is not None:
        return int(status["pending_transfers"])
    # Older peers: running transfers plus media still waiting.
    media = status.get("media") or {}
    return int(status.get("active_transfers") or 0) + int(media.get("queued") or 0)


def collect(db: Session, peers: list[PoolPeer], polled: dict[int, dict]) -> list[Node]:
    settings = get_system_settings(db)
    own = pool_core.node_status(db, control_enabled=True)
    nodes = [
        Node(
            key=SELF,
            name=own["name"],
            level=settings.pool_priority_self,
            reachable=True,
            paused=bool(own.get("uploads_paused")),
            pending=_pending(own),
            owned=settings.pool_priority_paused,
        )
    ]
    for peer in peers:
        status = polled.get(peer.id) or {"reachable": False}
        nodes.append(
            Node(
                key=f"peer:{peer.id}",
                name=peer.name,
                level=peer.priority,
                reachable=bool(status.get("reachable")),
                paused=bool(status.get("uploads_paused")),
                pending=_pending(status),
                owned=peer.priority_paused,
            )
        )
    return nodes


def apply_round(
    db: Session, peers: list[PoolPeer], polled: dict[int, dict]
) -> list[tuple[str, str, bool | None]]:
    """Run one priority round: ``(key, alert line, paused now or None)`` per change.

    With priorities switched off, only cleans up: boxes this rule paused get
    their uploads back.
    """
    settings = get_system_settings(db)
    nodes = collect(db, peers, polled)
    with _lock:
        if settings.pool_priority_enabled:
            decisions, new_overrides = decide(nodes, _overrides)
        else:
            decisions = [
                Decision(n.key, "resume", "Vorrang ausgeschaltet")
                if n.paused
                else Decision(n.key, "release")  # resumed meanwhile: just forget it
                for n in nodes
                if n.owned and n.reachable
            ]
            new_overrides = set()
        _overrides.clear()
        _overrides.update(new_overrides)

    by_key = {n.key: n for n in nodes}
    peer_by_key = {f"peer:{p.id}": p for p in peers}
    lines: list[tuple[str, str, bool | None]] = []
    for d in decisions:
        node = by_key[d.key]
        if d.action == "release":
            _set_owned(settings, peer_by_key.get(d.key), False)
            if settings.pool_priority_enabled:
                line = f"✋ {node.name}: von Hand fortgesetzt – Vorrang ruht für diese Box"
                lines.append((d.key, line, None))
            continue
        try:
            if d.key == SELF:
                transfers_core.apply_control(db, d.action)
            else:
                pool_core.control_peer(peer_by_key[d.key], d.action)
        except pool_core.PeerControlError as exc:
            message = str(exc)
            if _errors.get(d.key) != message:
                _errors[d.key] = message
                lines.append((d.key, f"⚠️ {node.name}: Vorrang nicht anwendbar – {message}", None))
            continue
        _errors.pop(d.key, None)
        paused = d.action == "pause"
        _set_owned(settings, peer_by_key.get(d.key), paused)
        icon, verb = ("⏸", "pausiert") if paused else ("▶️", "fortgesetzt")
        lines.append((d.key, f"{icon} {node.name}: Uploads {verb} – {d.reason}", paused))
    db.commit()
    return lines


def _set_owned(settings, peer: PoolPeer | None, owned: bool) -> None:
    if peer is None:
        settings.pool_priority_paused = owned
    else:
        peer.priority_paused = owned
