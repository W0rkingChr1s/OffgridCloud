"""HTTPS reverse-proxy config — admin only.

Reads the state ``deploy/https/apply.sh`` writes and, on PUT, re-runs that
script (via the NOPASSWD sudoers rule the installer sets up) to re-render the
Caddyfile and set the mDNS hostname.

Two distinct flags in the status: ``enabled`` reports whether HTTPS is actually
serving (apply.sh ran / Caddy is up), while ``manageable`` reports whether the
UI may re-apply the config — the latter needs ``https_apply_command`` wired up.
A box reachable over https:// but never wired for UI management is therefore
``enabled`` but not ``manageable`` (no 409-guarded PUT form shown), instead of
being mislabelled "not set up". PUT still requires ``manageable`` → 409 when the
command is empty.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import https_config
from ..admin_ops import audit
from ..config import get_settings
from ..db import get_db
from ..deps import require_admin
from ..models import User
from ..schemas import HttpsConfigUpdate, HttpsStatusOut

router = APIRouter(
    prefix="/api/system/https", tags=["https"], dependencies=[Depends(require_admin)]
)


def _status() -> HttpsStatusOut:
    settings = get_settings()
    state = https_config.read_state(settings.data_dir)
    hostname = state["hostname"]
    domain = state["domain"]
    # "manageable" = the UI may re-apply the config (apply_command wired by the
    # installer). "enabled" = HTTPS is actually up — a box that can manage it is
    # trivially up, otherwise probe the real state (state file / running Caddy).
    # Keeping these apart stops the "not set up" warning from firing on a box
    # that already serves HTTPS but was never wired for UI management.
    manageable = bool(settings.https_apply_command.strip())
    enabled = manageable or https_config.is_active(settings.data_dir)
    return HttpsStatusOut(
        enabled=enabled,
        manageable=manageable,
        hostname=hostname,
        domain=domain,
        lan_url=f"https://{hostname}.local" if hostname else "",
        public_url=f"https://{domain}" if domain else "",
    )


@router.get("", response_model=HttpsStatusOut)
def get_https(_: User = Depends(require_admin)) -> HttpsStatusOut:
    return _status()


@router.put("", response_model=HttpsStatusOut)
def update_https(
    payload: HttpsConfigUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> HttpsStatusOut:
    settings = get_settings()
    command = settings.https_apply_command.strip()
    if not command:
        raise HTTPException(
            status_code=409,
            detail=(
                "HTTPS ist nicht eingerichtet. Den Installer erneut ausführen und "
                "HTTPS aktivieren, um die Caddy-Konfiguration und sudoers-Regel anzulegen."
            ),
        )

    current = https_config.read_state(settings.data_dir)
    # Patch semantics: fall back to the current value when a field is omitted.
    raw_hostname = payload.hostname if payload.hostname is not None else current["hostname"]
    raw_domain = payload.domain if payload.domain is not None else current["domain"]

    try:
        hostname = https_config.validate_hostname(
            https_config.normalise_hostname(raw_hostname)
        )
        domain = https_config.validate_domain(raw_domain)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        https_config.run_apply(command, hostname=hostname, domain=domain)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=f"apply.sh fehlgeschlagen: {exc}") from exc

    audit(db, admin, "system.https.update", f"hostname={hostname} domain={domain or '—'}")
    return _status()
