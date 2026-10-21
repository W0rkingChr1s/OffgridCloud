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

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, announce, buffers, pool_watch, setup_wizard, telegram_bot
from .admin_ops import disk_usage, ensure_system_settings, get_system_settings
from .bandwidth import ensure_policy
from .bootstrap import autostart_vpn, ensure_initial_admin
from .config import get_settings
from .db import SessionLocal, init_db
from .discovery import discovery
from .integrity import run_startup_checks
from .network import ensure_network_settings
from .pool import ensure_node_id, node_name
from .pool import migrate_legacy_token as migrate_legacy_pool_token
from .prtg import health_to_prtg
from .rclone import check_rclone
from .routers import (
    auth,
    bandwidth,
    ble,
    descriptions,
    events,
    folders,
    groups,
    https,
    media,
    network,
    pool,
    providers,
    setup,
    storage,
    system,
    transfers,
    updates,
    uploads,
    users,
    vpn,
    webauthn,
)
from .transfers import live_limit_loop, reconcile_loop, worker_loop
from .updater import resolve_pending

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    init_db()
    ensure_initial_admin()
    ensure_policy()
    ensure_system_settings()
    migrate_legacy_pool_token()
    node_id = ensure_node_id()
    ensure_network_settings()
    # Mount buffer drives first, so their files are checked rather than
    # mistaken for lost ones.
    buffers.sync_startup()
    # Battery-bank hardening: repair any upload/media state left inconsistent by
    # a power cut before serving traffic (see integrity.py).
    run_startup_checks()
    # If we were restarted by a one-click update, settle its final status so the
    # portal shows success/failure instead of a perpetual "running" spinner.
    resolve_pending(settings.data_dir, __version__)
    autostart_vpn()

    stop = asyncio.Event()
    bot_stop = threading.Event()
    tasks: list[asyncio.Task] = []
    if settings.worker_enabled:
        tasks.append(asyncio.create_task(worker_loop(stop)))
        # Running uploads follow limit changes (pool share, schedule) live.
        tasks.append(asyncio.create_task(live_limit_loop(stop)))
        tasks.append(asyncio.create_task(reconcile_loop(stop)))
        # Re-mount buffer drives when they are plugged back in.
        tasks.append(asyncio.create_task(buffers.watch_loop(stop)))
        # Watch the uplink so a dropped-then-restored connection pings the field.
        tasks.append(asyncio.create_task(announce.connectivity_loop(stop)))
        # Pool hub: one bundled message when peers change (offline, full, …).
        tasks.append(asyncio.create_task(pool_watch.pool_watch_loop(stop)))
        # Comprehensive "server up" summary. Off the event loop (it probes the
        # external IP and pools peers), so serving traffic is never delayed.
        threading.Thread(target=announce.announce_startup, daemon=True).start()
        # Interactive Telegram bot (/status, /queue …). A daemon thread rather
        # than a task: its long-poll must never hold up shutdown.
        threading.Thread(
            target=telegram_bot.run_bot, args=(bot_stop,), name="telegram-bot", daemon=True
        ).start()
    if settings.mdns_enabled:
        # Announce this box and look for others (Pool page). Never fatal.
        with SessionLocal() as db:
            announce_box = get_system_settings(db).pool_announce
        await discovery.start(
            node_id=node_id,
            name=node_name(),
            version=__version__,
            port=settings.mdns_port,
            announce=announce_box,
        )
    try:
        yield
    finally:
        stop.set()
        bot_stop.set()
        for task in tasks:
            await task
        await discovery.stop()


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
app.include_router(https.public_router)
app.include_router(media.router)
app.include_router(descriptions.router)
app.include_router(pool.router)
app.include_router(updates.router)
app.include_router(network.router)
app.include_router(vpn.router)
app.include_router(storage.router)
app.include_router(storage.public_router)
app.include_router(setup.router)
app.include_router(ble.router)

# API paths that stay open while the image's setup wizard has not run yet.
_SETUP_OPEN_API = ("/api/setup/", "/api/health")


@app.middleware("http")
async def setup_gate(request: Request, call_next):
    """Setup mode (prebuilt image, no account yet): lock everything else.

    The API answers 503 so nothing can be used before an admin exists, and the
    captive-portal probes of phones on the setup Wi-Fi get redirected to the
    wizard (see ``setup_wizard.captive_redirect``).
    """
    if not setup_wizard.setup_required():
        return await call_next(request)
    path = request.url.path
    if path.startswith("/api/"):
        if not path.startswith(_SETUP_OPEN_API):
            return JSONResponse({"detail": "setup_required"}, status_code=503)
    elif request.method == "GET":
        peer = request.client.host if request.client else ""
        target = setup_wizard.captive_redirect(
            request.headers.get("host", ""),
            setup_wizard.client_ip(peer, request.headers.get("x-forwarded-for", "")),
        )
        if target:
            return RedirectResponse(target, status_code=302)
    return await call_next(request)


@app.get("/api/health")
def health(format: str = Query("json", pattern="^(json|prtg)$")) -> dict:
    """Liveness + environment summary (used by the dashboard and CI).

    ``?format=prtg`` renders the same data as a PRTG *HTTP Data Advanced*
    result set (channels + thresholds) so the endpoint can be polled directly
    by PRTG without a custom script — see ``prtg.py`` and ``docs/BETRIEB.md``.
    """
    rclone = check_rclone()
    payload = {
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
    if format != "prtg":
        return payload
    try:
        disk = disk_usage()
    except OSError:
        # Buffer dir unreadable (unmounted USB disk, permissions): still report
        # liveness rather than failing the whole sensor.
        disk = None
    return health_to_prtg(payload, disk)


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
