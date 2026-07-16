"""OffgridCloud FastAPI application.

Runs as a single process: it serves the JSON API under ``/api`` and the static
React UI from ``/`` — so there is no separate frontend service and no Node at
runtime (key for the 1 GB Raspberry Pi target).
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, announce
from .admin_ops import ensure_system_settings
from .bandwidth import ensure_policy
from .bootstrap import autostart_vpn, ensure_initial_admin
from .config import get_settings
from .db import init_db
from .integrity import run_startup_checks
from .network import ensure_network_settings
from .rclone import check_rclone
from .routers import (
    auth,
    bandwidth,
    descriptions,
    events,
    folders,
    groups,
    https,
    media,
    network,
    pool,
    providers,
    system,
    transfers,
    updates,
    uploads,
    users,
    vpn,
    webauthn,
)
from .transfers import reconcile_loop, worker_loop
from .updater import resolve_pending

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    init_db()
    ensure_initial_admin()
    ensure_policy()
    ensure_system_settings()
    ensure_network_settings()
    # Battery-bank hardening: repair any upload/media state left inconsistent by
    # a power cut before serving traffic (see integrity.py).
    run_startup_checks()
    # If we were restarted by a one-click update, settle its final status so the
    # portal shows success/failure instead of a perpetual "running" spinner.
    resolve_pending(settings.data_dir, __version__)
    autostart_vpn()

    stop = asyncio.Event()
    tasks: list[asyncio.Task] = []
    if settings.worker_enabled:
        tasks.append(asyncio.create_task(worker_loop(stop)))
        tasks.append(asyncio.create_task(reconcile_loop(stop)))
        # Watch the uplink so a dropped-then-restored connection pings the field.
        tasks.append(asyncio.create_task(announce.connectivity_loop(stop)))
        # Comprehensive "server up" summary. Off the event loop (it probes the
        # external IP and pools peers), so serving traffic is never delayed.
        threading.Thread(target=announce.announce_startup, daemon=True).start()
    try:
        yield
    finally:
        stop.set()
        for task in tasks:
            await task


app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)

app.include_router(auth.router)
app.include_router(webauthn.router)
app.include_router(users.router)
app.include_router(folders.router)
app.include_router(groups.router)
app.include_router(uploads.router)
app.include_router(providers.router)
app.include_router(transfers.router)
app.include_router(bandwidth.router)
app.include_router(events.router)
app.include_router(system.router)
app.include_router(https.router)
app.include_router(media.router)
app.include_router(descriptions.router)
app.include_router(pool.router)
app.include_router(updates.router)
app.include_router(network.router)
app.include_router(vpn.router)


@app.get("/api/health")
def health() -> dict:
    """Liveness + environment summary (used by the dashboard and CI)."""
    rclone = check_rclone()
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": __version__,
        "environment": settings.environment,
        "rclone": {
            "available": rclone.available,
            "version": rclone.version,
            "error": rclone.error,
        },
    }


# --- Static frontend ------------------------------------------------------
# The built React app (frontend/dist) is copied next to the backend during the
# build. If it isn't present (e.g. raw dev checkout), fall back to a hint.
_STATIC_DIR = Path(__file__).resolve().parent / "static"


class SPAStaticFiles(StaticFiles):
    """Serve static files, falling back to ``index.html`` for unknown paths.

    The UI uses client-side (HTML5 history) routing, so deep links like
    ``/folders/1`` or ``/admin/system`` have no matching file on disk. Without
    this fallback a browser refresh on such a route hits the server directly and
    gets a bare ``{"detail":"Not Found"}`` 404. Returning ``index.html`` instead
    lets the React router take over and render the right view. Genuinely missing
    assets (e.g. ``/assets/foo.js``) still 404 so broken references stay visible.
    """

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            unknown_route = (
                exc.status_code == 404
                and not path.startswith("api/")
                and not Path(path).suffix
            )
            if unknown_route:
                return await super().get_response("index.html", scope)
            raise


if _STATIC_DIR.is_dir():
    app.mount("/", SPAStaticFiles(directory=_STATIC_DIR, html=True), name="static")
else:

    @app.get("/")
    def _no_frontend() -> JSONResponse:
        return JSONResponse(
            {
                "message": (
                    "Frontend not built. Run 'npm run build' in ./frontend and "
                    "copy frontend/dist to backend/app/static, or use the Docker "
                    "image / install script which does this for you."
                ),
                "api_health": "/api/health",
            }
        )
