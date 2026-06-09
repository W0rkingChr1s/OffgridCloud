"""First-run bootstrap: create the initial admin if no users exist.

Self-registration is intentionally disabled — the admin creates all accounts.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from .config import get_settings
from .db import SessionLocal
from .models import Role, User
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
