"""WebAuthn / passkey ceremonies + credential management.

Registration & management require a logged-in user; login is public. A
successful assertion issues the SAME JWT as the password login, so the frontend
token flow is unchanged. py_webauthn's verify_* functions are referenced at
module scope so tests can monkeypatch them.
"""

from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .. import https_config, webauthn_config
from ..admin_ops import audit
from ..config import get_settings
from ..db import get_db
from ..deps import get_current_user
from ..models import User, WebAuthnCredential
from ..schemas import (
    PasskeyOut,
    PasskeyRename,
    TokenResponse,
    WebAuthnLoginOptionsRequest,
    WebAuthnLoginVerify,
    WebAuthnRegisterVerify,
)
from ..security import create_access_token

router = APIRouter(prefix="/api/auth/webauthn", tags=["webauthn"])

# Process-local challenge store (single-process uvicorn — see design).
challenges = webauthn_config.ChallengeStore(ttl_seconds=300)

RP_NAME = "OffgridCloud"


def _rp_for_request(request: Request) -> tuple[str, str]:
    """Derive + validate (rp_id, origin) from the request Origin header."""
    settings = get_settings()
    origin = request.headers.get("origin") or ""
    if not origin:
        # Fall back to Host (no scheme) — assume https unless localhost.
        host = request.headers.get("host", "")
        scheme = "http" if host.startswith("localhost") else "https"
        origin = f"{scheme}://{host}"
    state = https_config.read_state(settings.data_dir)
    extra = getattr(settings, "webauthn_extra_origins", "")
    allow = webauthn_config.build_allowlist(state=state, extra_origins=extra)
    try:
        return webauthn_config.resolve_rp(origin, allowlist=allow)
    except (webauthn_config.OriginNotAllowed, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Origin nicht erlaubt: {exc}") from exc


def _ensure_user_handle(db: Session, user: User) -> bytes:
    if not user.webauthn_user_handle:
        user.webauthn_user_handle = secrets.token_bytes(32)
        db.commit()
    return user.webauthn_user_handle


# --- Registration (logged-in) ----------------------------------------------
@router.post("/register/options")
def register_options(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    rp_id, _origin = _rp_for_request(request)
    handle = _ensure_user_handle(db, user)
    existing = db.scalars(
        select(WebAuthnCredential).where(
            WebAuthnCredential.user_id == user.id, WebAuthnCredential.rp_id == rp_id
        )
    ).all()
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=RP_NAME,
        user_id=handle,
        user_name=user.email,
        user_display_name=user.name or user.email,
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=c.credential_id) for c in existing
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    nonce = challenges.put(options.challenge, meta={"user_id": user.id, "rp_id": rp_id})
    return {"nonce": nonce, "options": json.loads(options_to_json(options))}


@router.post("/register/verify", response_model=PasskeyOut)
def register_verify(
    payload: WebAuthnRegisterVerify,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WebAuthnCredential:
    rp_id, origin = _rp_for_request(request)
    try:
        entry = challenges.take(payload.nonce)
    except KeyError:
        raise HTTPException(status_code=400, detail="Registrierung abgelaufen, bitte erneut.") from None
    if entry.meta.get("user_id") != user.id:
        raise HTTPException(status_code=400, detail="Ungültige Registrierungs-Sitzung.")

    try:
        verified = verify_registration_response(
            credential=payload.credential,
            expected_challenge=entry.challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            require_user_verification=False,
        )
    except Exception as exc:  # noqa: BLE001 — surface verification failure
        raise HTTPException(status_code=400, detail=f"Passkey-Prüfung fehlgeschlagen: {exc}") from exc

    if db.scalar(
        select(WebAuthnCredential).where(
            WebAuthnCredential.credential_id == verified.credential_id
        )
    ):
        raise HTTPException(status_code=409, detail="Dieser Passkey ist bereits registriert.")

    cred = WebAuthnCredential(
        user_id=user.id,
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        rp_id=rp_id,
        transports=json.dumps(payload.credential.get("response", {}).get("transports", [])),
        name=payload.name.strip() or "Passkey",
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    audit(db, user, "auth.webauthn.register", f"rp_id={rp_id} name={cred.name}")
    return cred
