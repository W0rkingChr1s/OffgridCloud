"""The box side of a BLE session: database, REST API, system (§7, §10).

The bridge has no rights of its own: it mints a short-lived access token for
the user a device is bound to and forwards the request to the REST API on
localhost, which checks permissions exactly as for the web UI.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from .. import __version__
from ..admin_ops import audit, get_system_settings
from ..config import get_settings
from ..db import SessionLocal
from ..models import User
from ..pool import node_name
from ..power import run_power_command
from ..security import create_access_token
from . import registry
from .registry import DeviceInfo, PairingClaim
from .session import NOTICE_TEXT, BackendUnavailable, NotJson

TOKEN_TTL = timedelta(minutes=5)
TOKEN_RENEW = 240.0  # seconds; renew well before expiry
REQUEST_TIMEOUT = 15.0
HEALTH_TIMEOUT = 2.0


def _db(fn, *args):
    def run():
        with SessionLocal() as db:
            return fn(db, *args)

    return asyncio.to_thread(run)


# Never route localhost calls through an HTTP(S)_PROXY from the environment.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http(
    method: str, url: str, body: Any, headers: dict[str, str], timeout: float
) -> tuple[int, Any]:
    data = None
    headers = dict(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with _opener.open(req, timeout=timeout) as resp:
            status, raw, ctype = resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as err:
        status, raw, ctype = err.code, err.read(), err.headers.get("Content-Type", "")
    except (urllib.error.URLError, OSError) as err:
        raise BackendUnavailable(str(err)) from None
    if not raw:
        return status, None
    if "json" not in ctype:
        raise NotJson(ctype)
    return status, json.loads(raw)


def compact_status(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Shrink ``build_snapshot()`` to the ``status`` event of §7.7."""
    out: dict[str, Any] = {
        "folders": {
            key: sum(f.get(key, 0) for f in snapshot.get("folders", []))
            for key in ("queued", "uploading", "failed", "done")
        }
    }
    transfers = snapshot.get("transfers")
    if transfers is not None:
        counts = transfers.get("counts", {})
        out["transfers"] = {k: counts.get(k, 0) for k in ("running", "queued", "failed")}
        out["kbps"] = round(sum(a.get("kbps", 0.0) for a in transfers.get("active", [])), 1)
    bandwidth = snapshot.get("bandwidth")
    if bandwidth is not None:
        out["gated"] = bool(bandwidth.get("gated"))
        out["gate_reason"] = bandwidth.get("gate_reason") or None
    if "notices" in snapshot:
        # Plain texts (§7.7), like the companion app reads them.
        out["notices"] = [
            f"{n.get('title', '')} {n.get('message', '')}".strip()[:NOTICE_TEXT]
            for n in snapshot["notices"][-3:]
        ]
    return out


class BoxHost:
    """``session.Host`` backed by the real box."""

    def __init__(self, box_key: X25519PrivateKey, backend_url: str) -> None:
        self.box_key = box_key
        self._backend = backend_url.rstrip("/")
        self._enabled = True
        self._tokens: dict[int, tuple[str, float]] = {}
        self._started = time.monotonic()

    # Cached by the service's settings poll; sessions read it synchronously.
    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def ble_enabled(self) -> bool:
        return self._enabled

    def box_name(self) -> str:
        return node_name()

    async def find_device(self, public_key: bytes) -> DeviceInfo | None:
        return await _db(registry.find_device, public_key)

    async def claim_pairing(self, pid: int) -> PairingClaim | None:
        return await _db(registry.claim_pairing, pid)

    async def confirm_pairing(
        self, claim: PairingClaim, public_key: bytes, name: str
    ) -> DeviceInfo | None:
        return await _db(registry.confirm_pairing, claim, public_key, name)

    async def device_still_valid(self, device_id: int) -> bool:
        return await _db(registry.device_still_valid, device_id)

    def _token(self, device: DeviceInfo) -> str:
        cached = self._tokens.get(device.user_id)
        now = time.monotonic()
        if cached and now - cached[1] < TOKEN_RENEW:
            return cached[0]
        token = create_access_token(user_id=device.user_id, role=device.role, ttl=TOKEN_TTL)
        self._tokens[device.user_id] = (token, now)
        return token

    async def forward(
        self,
        device: DeviceInfo,
        method: str,
        path: str,
        query: dict[str, str] | None,
        body: Any,
    ) -> tuple[int, Any]:
        url = self._backend + path
        if query:
            url += "?" + urlencode({str(k): str(v) for k, v in query.items()})
        headers = {"Authorization": f"Bearer {self._token(device)}"}
        return await asyncio.to_thread(_http, method, url, body, headers, REQUEST_TIMEOUT)

    async def backend_up(self) -> bool:
        try:
            status, _ = await asyncio.to_thread(
                _http, "GET", self._backend + "/api/health", None, {}, HEALTH_TIMEOUT
            )
        except (BackendUnavailable, NotJson, ValueError):
            return False
        return status == 200

    async def health(self) -> dict[str, Any]:
        return {
            "backend_up": await self.backend_up(),
            "version": __version__,
            "uptime_s": int(time.monotonic() - self._started),
        }

    async def restart_backend(self) -> None:
        command = get_settings().restart_service_command
        if command.strip():
            await asyncio.to_thread(run_power_command, command, delay=1.0)

    async def status_snapshot(self, device: DeviceInfo) -> dict[str, Any] | None:
        from ..routers.events import build_snapshot

        snapshot = await asyncio.to_thread(build_snapshot, device.user_id)
        return None if snapshot is None else compact_status(snapshot)

    async def audit(self, device: DeviceInfo, action: str, detail: str) -> None:
        def write(db):
            audit(db, db.get(User, device.user_id), action, f"[ble] {detail}")

        await _db(write)

    # --- service-level helpers ------------------------------------------------

    async def read_enabled(self) -> bool:
        return await _db(lambda db: get_system_settings(db).ble_enabled)

    async def pairing_open(self) -> bool:
        return await _db(registry.pairing_open)
