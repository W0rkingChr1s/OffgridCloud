"""First-run bootstrap: create the initial admin if no users exist.

Self-registration is intentionally disabled — the admin creates all accounts.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select

from . import vpn as vpnsvc
from .config import get_settings
from .crypto import decrypt
from .db import SessionLocal
from .models import Role, User, VpnTunnel
from .security import hash_password

logger = logging.getLogger("offgridcloud.bootstrap")


def ensure_initial_admin() -> None:
    settings = get_settings()
    with SessionLocal() as db:
        if db.scalar(select(User).limit(1)) is not None:
            return  # already initialised
        admin = User(
            email=settings.initial_admin_email.strip().lower(),
            name="Administrator",
            role=Role.ADMIN,
            password_hash=hash_password(settings.initial_admin_password),
        )
        db.add(admin)
        db.commit()
        logger.warning(
            "Created initial admin '%s'. Change the password after first login.",
            settings.initial_admin_email,
        )


def autostart_vpn() -> None:
    """Bring up the first autostart-enabled VPN tunnel (best-effort).

    Failures (missing privileges, bad config) are logged, never fatal — the app
    must still start so an admin can fix the config from the UI.
    """
    caps = vpnsvc.capabilities()
    if not (caps.net_admin and caps.tun_device):
        return  # no point trying without the base requirements
    with SessionLocal() as db:
        tunnel = db.scalar(
            select(VpnTunnel).where(VpnTunnel.autostart.is_(True)).order_by(VpnTunnel.id).limit(1)
        )
        if tunnel is None:
            return
        try:
            data = json.loads(decrypt(tunnel.config_encrypted) or "{}")
        except json.JSONDecodeError:
            data = {}
        result = vpnsvc.connect(
            tunnel.id, tunnel.type.value, data.get("config", ""),
            data.get("username", ""), data.get("password", ""),
        )
        tunnel.last_error = "" if result.ok else result.message
        db.commit()
        if result.ok:
            logger.info("Autostarted VPN tunnel '%s'.", tunnel.name)
        else:
            logger.warning("VPN autostart for '%s' failed: %s", tunnel.name, result.message)
