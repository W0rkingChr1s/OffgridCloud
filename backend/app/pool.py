"""Multi-server pooling core: this node's compact status + peer polling.

Pooling is mostly read-only aggregation: a hub node periodically reads each
peer's ``/api/pool/status`` (authenticated by the peer's shared token) and
presents a combined fleet view. No distributed coordination — which keeps it
safe and cheap enough for the Raspberry Pi target.

The one write path is opt-in transfer control: a peer whose admin enabled it
accepts ``POST /api/pool/control`` (pause/resume uploads, re-queue failed jobs).
Nothing a hub can trigger deletes data or changes configuration.
"""

from __future__ import annotations

import json
import socket
import ssl
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import __version__
from .admin_ops import disk_usage, get_system_settings
from .bandwidth import get_policy
from .config import get_settings
from .crypto import decrypt
from .models import MediaItem, PoolPeer, TransferJob, TransferStatus
from .transfers import control_state

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
        **control_state(db),
        "control_enabled": get_system_settings(db).pool_control_enabled,
    }


def normalize_base_url(raw: str) -> str:
    """Trim, drop trailing slashes and default a missing scheme to ``http://``.

    The backend port (8000) speaks plain HTTP, so a bare ``host:port`` means HTTP.
    """
    url = raw.strip().rstrip("/")
    if url and "://" not in url:
        url = f"http://{url}"
    return url


def _explain_error(exc: BaseException) -> str:
    """Turn low-level connection errors into an actionable hint for the admin."""
    reason = getattr(exc, "reason", exc)
    text = str(reason)
    if isinstance(reason, ssl.SSLError) and "WRONG_VERSION_NUMBER" in text:
        # TLS handshake against a plain-HTTP port (uvicorn on :8000 has no TLS).
        return "Peer spricht unter dieser Adresse kein HTTPS – URL mit http:// eintragen"
    if isinstance(reason, ssl.SSLCertVerificationError):
        # Caddy's 'tls internal' cert on <host>.local is self-signed.
        return (
            "Zertifikat des Peers nicht vertrauenswürdig (selbstsigniert) – "
            "http://<IP>:8000 verwenden"
        )
    return text


def _peer_headers(peer: PoolPeer) -> dict[str, str]:
    token = decrypt(peer.token_encrypted) if peer.token_encrypted else ""
    # X-Pool-Hub names the caller in the peer's audit log (headers are ASCII).
    hub = _node_name().encode("ascii", "replace").decode("ascii")
    return {"X-Pool-Token": token, "X-Pool-Hub": hub, "User-Agent": "OffgridCloud-Pool"}


class PeerControlError(Exception):
    """A control request the peer refused or never answered."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def control_peer(peer: PoolPeer, action: str) -> dict:
    """Send one transfer action to a peer; returns its new control state."""
    url = f"{normalize_base_url(peer.base_url)}/api/pool/control"
    headers = {**_peer_headers(peer), "Content-Type": "application/json"}
    body = json.dumps({"action": action}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310
        with urllib.request.urlopen(req, timeout=PEER_TIMEOUT) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise PeerControlError("Pool-Token ungültig") from exc
        if exc.code == 403:
            raise PeerControlError(
                "Fernsteuerung auf dem Peer nicht freigegeben", status=409
            ) from exc
        if exc.code in (404, 405):
            # Older peer without the endpoint.
            raise PeerControlError("Peer unterstützt keine Fernsteuerung (Update nötig)") from exc
        raise PeerControlError(f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise PeerControlError(_explain_error(exc)) from exc
    if not isinstance(data, dict):
        raise PeerControlError("Unerwartete Antwort")
    return data


def poll_peer(peer: PoolPeer) -> dict:
    """Fetch a peer's compact status. Never raises — errors become ``reachable=False``."""
    url = f"{normalize_base_url(peer.base_url)}/api/pool/status"
    try:
        req = urllib.request.Request(url, headers=_peer_headers(peer))  # noqa: S310 (admin-set URL)
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
        return {"reachable": False, "error": _explain_error(exc)}
    except (ValueError, OSError) as exc:  # JSON / socket errors
        return {"reachable": False, "error": _explain_error(exc)}


def poll_peers(peers: list[PoolPeer]) -> dict[int, dict]:
    """Poll all peers concurrently, returning ``{peer_id: status_dict}``."""
    if not peers:
        return {}
    workers = min(_MAX_POOL_WORKERS, len(peers))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(poll_peer, peers))
    return {peer.id: result for peer, result in zip(peers, results, strict=True)}
