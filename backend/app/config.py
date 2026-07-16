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
    # endpoint lets us request an exact byte count. The probe is time-boxed
    # (see bandwidth.PROBE_SAMPLE_SECONDS), so a generous size just keeps a fast
    # link streaming for the full sample window; slow links stop at the cap.
    default_probe_url: str = "https://speed.cloudflare.com/__down?bytes=100000000"

    # --- Updates -----------------------------------------------------------
    # GitHub repository (owner/name) used to check for new releases.
    github_repo: str = "W0rkingChr1s/OffgridCloud"
    # One-click self-update. When enabled, POST /api/updates/apply runs
    # ``update_command`` detached. On by default so the "Jetzt aktualisieren"
    # button just works — the installer always wires up the matching NOPASSWD
    # sudoers rule so ``sudo update.sh`` can run headless. Set OGC_SELF_UPDATE=false
    # (or an empty OGC_UPDATE_COMMAND) to hide the button, e.g. on Docker where
    # the box updates by replacing the image.
    self_update: bool = True
    update_command: str = "sudo /opt/offgridcloud/src/deploy/update.sh"

    # --- System power control ----------------------------------------------
    # Privileged commands for the "System steuern" panel: restart the
    # OffgridCloud service, reboot the box, or shut it down from the web UI.
    # On by default so the buttons work out of the box — the installer always
    # adds the matching NOPASSWD sudoers rules (systemctl restart / reboot /
    # poweroff) so the service user may run them headless. Clear a command
    # (empty string) to disable just that action; the button is then greyed out
    # in the portal and the endpoint returns 409.
    restart_service_command: str = "sudo /usr/bin/systemctl restart offgridcloud"
    reboot_command: str = "sudo /usr/bin/systemctl reboot"
    shutdown_command: str = "sudo /usr/bin/systemctl poweroff"

    # --- Network redundancy / AP fallback ----------------------------------
    # Where the desired network config is exported for the privileged apply
    # helper + root watchdog to consume. Contains Wi-Fi passphrases in the
    # clear (NetworkManager needs them so), so it is written 0600. Unset →
    # ``<data_dir>/network.json`` (see ``network_config_path``); set
    # OGC_NET_CONFIG_FILE to relocate.
    net_config_file: Path | None = None
    # Opt-in privileged command that applies the exported config (creates the
    # NetworkManager connections + AP). Wired up by
    # ``deploy/netfallback/install.sh`` via a NOPASSWD sudoers rule, off by
    # default — flipping real network state needs root, so it must be deliberate.
    net_apply_command: str = ""

    # --- HTTPS reverse proxy (Caddy) ---------------------------------------
    # Opt-in privileged command that re-renders the Caddyfile + sets the mDNS
    # hostname (see deploy/https/apply.sh). Wired up by deploy/https/install.sh
    # via a NOPASSWD sudoers rule; empty when HTTPS wasn't set up, so the System
    # UI shows the feature as unavailable and PUT /api/system/https returns 409.
    https_apply_command: str = ""

    # --- WebAuthn / passkeys -----------------------------------------------
    # Extra comma-separated origins allowed as passkey RP-IDs, beyond
    # <hostname>.local + the configured domain + localhost. Usually empty.
    webauthn_extra_origins: str = ""

    # --- Transfer engine ---------------------------------------------------
    rclone_binary: str = "rclone"

    # Background upload worker. Disabled in tests so job state can be driven
    # deterministically.
    worker_enabled: bool = True
    worker_poll_interval: float = 3.0  # seconds between idle polls
    worker_max_attempts: int = 5

    # Background reconciler: how often (seconds) to re-queue failed/stuck
    # transfers and backfill any missing jobs, so a temporary outage self-heals
    # once connectivity returns. 0 disables the loop. Gated per-instance by the
    # ``auto_resync`` system setting (default on).
    reconcile_interval: float = 900.0

    @property
    def database_url(self) -> str:
        return f"sqlite:///{(self.data_dir / 'offgridcloud.db').resolve()}"

    @property
    def network_config_path(self) -> Path:
        """Resolved location of the exported network config (see network.py)."""
        return self.net_config_file or (self.data_dir / "network.json")

    def ensure_dirs(self) -> None:
        """Create the data and buffer directories if they don't exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.buffer_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
