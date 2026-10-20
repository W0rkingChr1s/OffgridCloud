"""Multi-server pooling core: this node's compact status + peer polling.

Pooling is mostly read-only aggregation: a hub node periodically reads each
peer's ``/api/pool/status`` (authenticated by the peer's shared token) and
presents a combined fleet view. No distributed coordination — which keeps it
safe and cheap enough for the Raspberry Pi target.

Each hub gets its own access token from the peer (scope "read" or "control"),
so one hub can be revoked without re-pairing the others. The one write path is
transfer control via ``POST /api/pool/control`` (pause/resume uploads, re-queue
failed jobs) for "control" tokens. Nothing a hub can trigger deletes data or
changes configuration.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import socket
import ssl
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import __version__
from .admin_ops import disk_usage, get_system_settings
from .bandwidth import get_policy
from .config import get_settings
from .crypto import decrypt
from .db import SessionLocal
from .models import (
    MediaItem,
    PoolAccessToken,
    PoolPeer,
    PoolTokenScope,
    TransferJob,
    TransferStatus,
)
from .transfers import control_state

logger = logging.getLogger(__name__)

PEER_TIMEOUT = 6.0  # seconds per peer poll
_MAX_POOL_WORKERS = 8
TOKEN_PREFIX = "ogcp_"
# Hubs poll every ~15 s; writing "last used" on every poll would be wasted I/O.
_LAST_USED_EVERY = timedelta(seconds=60)


def _node_name() -> str:
    try:
        host = socket.gethostname().strip()
    except OSError:
        host = ""
    return host or get_settings().app_name


# --- Access tokens this node issues to hubs ---------------------------------


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def issue_token(db: Session, name: str, scope: PoolTokenScope) -> tuple[PoolAccessToken, str]:
    """Create a token for one hub. Returns the row and the plaintext (shown once)."""
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = PoolAccessToken(name=name, token_hash=hash_token(raw), hint=raw[-4:], scope=scope)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, raw


def authenticate(db: Session, raw: str | None, hub: str | None = None) -> PoolAccessToken | None:
    """The token row for ``raw``, or None. Notes when and by whom it was used."""
    if not raw:
        return None
    row = db.scalar(select(PoolAccessToken).where(PoolAccessToken.token_hash == hash_token(raw)))
    if row is None:
        return None
    now = _utcnow()
    hub = (hub or "").strip()[:100]
    if (
        row.last_used_at is None
        or now - row.last_used_at >= _LAST_USED_EVERY
        or (hub and hub != row.last_used_hub)
    ):
        row.last_used_at = now
        if hub:
            row.last_used_hub = hub
        db.commit()
    return row


def migrate_legacy_token() -> None:
    """Move the old shared plaintext token into ``pool_access_tokens`` (read-only).

    Hubs configured with it keep working; the admin can then issue per-hub
    tokens and delete this one. Runs at startup, idempotent.
    """
    with SessionLocal() as db:
        settings_row = get_system_settings(db)
        legacy = settings_row.pool_token
        if not legacy:
            return
        digest = hash_token(legacy)
        exists = db.scalar(
            select(PoolAccessToken.id).where(PoolAccessToken.token_hash == digest)
        )
        if exists is None:
            db.add(
                PoolAccessToken(
                    name="Bisheriges Token",
                    token_hash=digest,
                    hint=legacy[-4:],
                    scope=PoolTokenScope.READ,
                )
            )
        settings_row.pool_token = ""
        db.commit()
        logger.info("Migrated the shared pool token to a per-hub access token")


def node_status(db: Session, control_enabled: bool = False) -> dict:
    """Compact status of THIS node, shared with the pool hub and shown locally.

    ``control_enabled`` tells the caller whether *its* credentials may control
    transfers here, so the hub only offers actions that will work.
    """
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
        "control_enabled": control_enabled,
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
                "Token dieses Hubs hat auf dem Peer nur Leserechte", status=409
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
