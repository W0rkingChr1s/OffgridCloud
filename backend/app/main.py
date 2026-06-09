"""OffgridCloud FastAPI application.

Runs as a single process: it serves the JSON API under ``/api`` and the static
React UI from ``/`` — so there is no separate frontend service and no Node at
runtime (key for the 1 GB Raspberry Pi target).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import get_settings
from .db import init_db
from .rclone import check_rclone

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    init_db()
    yield


app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)


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

if _STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
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
