"""Multi-server pooling core: this node's compact status + peer polling.

Pooling is read-only aggregation: a hub node periodically reads each peer's
``/api/pool/status`` (authenticated by the peer's shared token) and presents a
combined fleet view. No distributed coordination, no cross-node writes — which
keeps it safe and cheap enough for the Raspberry Pi target.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import __version__
from .admin_ops import disk_usage
from .bandwidth import get_policy
from .config import get_settings
from .crypto import decrypt
from .models import MediaItem, PoolPeer, TransferJob, TransferStatus

PEER_TIMEOUT = 6.0  # seconds per peer poll
_MAX_POOL_WORKERS = 8


def _node_name() -> str:
    try:
        host = socket.gethostname().strip()
    except OSError:
        host = ""
    return host or get_settings().app_name


def node_status(db: Session) -> dict:
    """Compact status of THIS node, shared with the pool hub and shown locally."""
    rows = db.execute(
        select(MediaItem.status, func.count()).group_by(MediaItem.status)
    ).all()
    media = {st.value: count for st, count in rows}
    total = sum(media.values())
    active = (
        db.scalar(
            select(func.count(TransferJob.id)).where(
                TransferJob.status == TransferStatus.RUNNING
            )
        )
        or 0
    )
    policy = get_policy(db)
    disk = disk_usage()
    return {
        "name": _node_name(),
        "version": __version__,
        "reachable": True,
        "error": "",
        "media": media,
        "media_total": total,
        "active_transfers": int(active),
        "throughput_kbps": round(policy.last_kbps, 1),
        "disk_free": disk["free"],
        "disk_total": disk["total"],
    }


def poll_peer(peer: PoolPeer) -> dict:
    """Fetch a peer's compact status. Never raises — errors become ``reachable=False``."""
    url = f"{peer.base_url.rstrip('/')}/api/pool/status"
    token = decrypt(peer.token_encrypted) if peer.token_encrypted else ""
    headers = {"X-Pool-Token": token, "User-Agent": "OffgridCloud-Pool"}
    try:
        req = urllib.request.Request(url, headers=headers)  # noqa: S310 (admin-set URL)
        with urllib.request.urlopen(req, timeout=PEER_TIMEOUT) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, dict):
            return {"reachable": False, "error": "Unerwartete Antwort"}
        data["reachable"] = True
        data["error"] = ""
        return data
    except urllib.error.HTTPError as exc:
        detail = "Pool-Token ungültig" if exc.code == 401 else f"HTTP {exc.code}"
        return {"reachable": False, "error": detail}
    except (urllib.error.URLError, TimeoutError) as exc:
        return {"reachable": False, "error": str(getattr(exc, "reason", exc))}
    except (ValueError, OSError) as exc:  # JSON / socket errors
        return {"reachable": False, "error": str(exc)}


def poll_peers(peers: list[PoolPeer]) -> dict[int, dict]:
    """Poll all peers concurrently, returning ``{peer_id: status_dict}``."""
    if not peers:
        return {}
    workers = min(_MAX_POOL_WORKERS, len(peers))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(poll_peer, peers))
    return {peer.id: result for peer, result in zip(peers, results, strict=True)}
