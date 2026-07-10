"""Application configuration.

Loaded from environment variables (and an optional ``.env`` file). Designed to
run lean on a Raspberry Pi 3 — no external services required for the MVP.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    All values can be overridden via environment variables prefixed with
    ``OGC_`` (e.g. ``OGC_DATA_DIR``).
    """

    model_config = SettingsConfigDict(
        env_prefix="OGC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- General -----------------------------------------------------------
    app_name: str = "OffgridCloud"
    environment: str = "development"

    # --- Storage -----------------------------------------------------------
    # Directory for the SQLite database and app state.
    data_dir: Path = Path("./data")
    # Media buffer — on a Raspberry Pi this should point at an external USB SSD,
    # NOT the microSD card (write wear + capacity).
    buffer_dir: Path = Path("./data/buffer")

    # --- Security ----------------------------------------------------------
    # Used to sign JWTs / encrypt provider credentials. MUST be overridden in
    # production via OGC_SECRET_KEY.
    secret_key: str = "change-me-in-production"

    # Initial admin, created on first startup if no users exist.
    initial_admin_email: str = "admin@offgrid.local"
    initial_admin_password: str = "changeme"

    # --- Bandwidth measurement ---------------------------------------------
    # Default target for the active bandwidth probe, so admins don't have to
    # configure anything for "measure now" to work. Overridable per-instance
    # via the System settings (probe_url) or this env var. Cloudflare's speed
    # endpoint lets us request an exact byte count.
    default_probe_url: str = "https://speed.cloudflare.com/__down?bytes=10000000"

    # --- Updates -----------------------------------------------------------
    # GitHub repository (owner/name) used to check for new releases.
    github_repo: str = "W0rkingChr1s/OffgridCloud"
    # Opt-in one-click self-update. When enabled, POST /api/updates/apply runs
    # ``update_command`` detached. Off by default — updating from the web needs
    # elevated rights, so it must be wired up deliberately (install.sh --self-update).
    self_update: bool = False
    update_command: str = ""

    # --- Transfer engine ---------------------------------------------------
    rclone_binary: str = "rclone"

    # Background upload worker. Disabled in tests so job state can be driven
    # deterministically.
    worker_enabled: bool = True
    worker_poll_interval: float = 3.0  # seconds between idle polls
    worker_max_attempts: int = 5

    @property
    def database_url(self) -> str:
        return f"sqlite:///{(self.data_dir / 'offgridcloud.db').resolve()}"

    def ensure_dirs(self) -> None:
        """Create the data and buffer directories if they don't exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.buffer_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
