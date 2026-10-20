"""Bundled fleet alerts: the pool hub reports what changed on its peers.

Instead of every box messaging the team on its own, the hub polls its enabled
peers once a minute and sends ONE message per round listing every change:
a box went offline or came back, ran low on space, got paused or resumed, or
collected new failed transfers. Only transitions are reported — a box that
stays offline is mentioned once, not every minute.

State lives in memory. The first round after a (re)start only records the
current picture, so a restart never floods the channel with old news; the
startup summary already says how many pool devices are connected.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import pool as pool_core
from . import pool_priority
from .admin_ops import LOW_SPACE_FRACTION, LOW_SPACE_MIN_BYTES
from .models import PoolPeer

logger = logging.getLogger(__name__)

POOL_WATCH_INTERVAL = 60.0  # seconds between fleet checks
# A peer counts as offline only after this many failed polls in a row, so a
# single dropped packet on a shaky field link doesn't page anyone.
OFFLINE_AFTER = 2
# The box's auto-resync puts every failed job back in the queue every 15 min,
# so the failed count drops to 0 and climbs again while a target stays broken.
# Re-arm the "new failures" alert only after an hour without any failures.
REARM_AFTER_ZERO_ROUNDS = 60


@dataclass
class PeerState:
    online: bool  # last confirmed state
    misses: int = 0  # failed polls in a row
    paused: bool = False
    low_space: bool = False
    # Highest failed-transfer count reported in the current episode — only a
    # new high is news (see REARM_AFTER_ZERO_ROUNDS).
    failed_high: int = 0
    zero_rounds: int = 0  # rounds in a row with no failed transfers


def _gb(num: int) -> str:
    return f"{num / 1024**3:.1f} GB"


def _is_low(status: dict) -> bool:
    free, total = status.get("disk_free", 0), status.get("disk_total", 0)
    if not total:
        return False
    return free < LOW_SPACE_MIN_BYTES or free / total < LOW_SPACE_FRACTION


def evaluate(name: str, prev: PeerState | None, status: dict) -> tuple[PeerState, list[str]]:
    """Next state of one peer and the alert lines its new status warrants."""
    reachable = bool(status.get("reachable"))
    if prev is None:
        # First sight: remember, don't report.
        if not reachable:
            return PeerState(online=False, misses=OFFLINE_AFTER), []
        return PeerState(
            online=True,
            paused=bool(status.get("uploads_paused")),
            low_space=_is_low(status),
            failed_high=int(status.get("failed_transfers") or 0),
        ), []

    lines: list[str] = []
    if not reachable:
        state = PeerState(**{**prev.__dict__, "misses": prev.misses + 1})
        if prev.online and state.misses >= OFFLINE_AFTER:
            state.online = False
            reason = status.get("error") or "keine Antwort"
            lines.append(f"🔴 {name}: nicht erreichbar ({reason})")
        return state, lines

    state = PeerState(
        online=True,
        paused=prev.paused,
        low_space=prev.low_space,
        failed_high=prev.failed_high,
        zero_rounds=prev.zero_rounds,
    )
    if not prev.online:
        lines.append(f"🟢 {name}: wieder erreichbar")

    paused = bool(status.get("uploads_paused"))
    if paused != prev.paused:
        lines.append(f"⏸ {name}: Uploads pausiert" if paused else f"▶️ {name}: Uploads fortgesetzt")
    state.paused = paused

    low = _is_low(status)
    if low and not prev.low_space:
        free, total = status.get("disk_free", 0), status.get("disk_total", 0)
        used = round(100 * (1 - free / total)) if total else 0
        lines.append(f"💾 {name}: Speicher knapp – noch {_gb(free)} frei ({used} % belegt)")
    state.low_space = low

    failed = int(status.get("failed_transfers") or 0)
    if failed > prev.failed_high:
        new = failed - prev.failed_high
        lines.append(
            f"❌ {name}: {new} neue fehlgeschlagene Transfers (jetzt {failed})"
            if new > 1
            else f"❌ {name}: 1 neuer fehlgeschlagener Transfer (jetzt {failed})"
        )
        state.failed_high = failed
    if failed:
        state.zero_rounds = 0
    else:
        state.zero_rounds += 1
        if state.zero_rounds >= REARM_AFTER_ZERO_ROUNDS:
            state.failed_high = 0  # quiet for an hour: the next failure is news again
    return state, lines


_lock = threading.Lock()
_states: dict[int, PeerState] = {}


def check_once(db: Session) -> list[str]:
    """Poll the enabled peers, update state, apply priorities, send one bundled alert."""
    peers = list(db.scalars(select(PoolPeer).where(PoolPeer.enabled.is_(True))))
    polled = pool_core.poll_peers(peers) if peers else {}
    lines: list[tuple[str, str]] = []  # (box key, alert line)
    with _lock:
        for gone in set(_states) - {p.id for p in peers}:
            del _states[gone]  # removed or paused peers start fresh later
        for peer in sorted(peers, key=lambda p: p.name.lower()):
            status = polled.get(peer.id, {"reachable": False, "error": "keine Antwort"})
            _states[peer.id], peer_lines = evaluate(peer.name, _states.get(peer.id), status)
            lines += [(f"peer:{peer.id}", line) for line in peer_lines]
        # Priorities between boxes on a shared line act on what we just polled.
        for key, line, paused in pool_priority.apply_round(db, peers, polled):
            if line.startswith("✋"):
                # Says more than the plain "Uploads fortgesetzt" for the same box.
                lines = [(k, x) for k, x in lines if not (k == key and x.startswith("▶️"))]
            lines.append((key, line))
            state = _states.get(int(key.split(":")[1])) if key.startswith("peer:") else None
            if state is not None and paused is not None:
                state.paused = paused  # our own doing: no extra "Uploads pausiert" next round
    texts = [line for _, line in lines]
    if texts:
        _send(db, texts)
    return texts


def _send(db: Session, lines: list[str]) -> None:
    from .announce import _announce

    count = len(lines)
    title = "Pool: 1 Änderung" if count == 1 else f"Pool: {count} Änderungen"
    level = "warning" if any(line.startswith(("🔴", "💾", "❌")) for line in lines) else "info"
    _announce(
        db,
        event="pool.alert",
        level=level,
        title=title,
        message="\n".join(lines),
        payload={"event": "pool.alert", "changes": lines},
    )


def reset_state() -> None:
    """Forget every peer's state (tests)."""
    with _lock:
        _states.clear()


async def pool_watch_loop(stop: asyncio.Event, interval: float = POOL_WATCH_INTERVAL) -> None:
    from .db import SessionLocal

    def tick() -> None:
        with SessionLocal() as db:
            check_once(db)

    while not stop.is_set():
        try:
            # Polling blocks on HTTP; keep it off the event loop.
            await asyncio.to_thread(tick)
        except Exception:  # noqa: BLE001 - the watcher must never die
            logger.warning("Pool watch round failed", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass
