"""WebAuthn / passkey ceremonies + credential management.

Registration & management require a logged-in user; login is public. A
successful assertion issues the SAME JWT as the password login, so the frontend
token flow is unchanged. py_webauthn's verify_* functions are referenced at
module scope so tests can monkeypatch them.
"""

from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
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


# --- Login (public) ---------------------------------------------------------
@router.post("/login/options")
def login_options(
    payload: WebAuthnLoginOptionsRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    rp_id, _origin = _rp_for_request(request)
    allow: list[PublicKeyCredentialDescriptor] = []
    if payload.email:
        email = payload.email.strip().lower()
        user = db.scalar(select(User).where(User.email == email))
        if user is not None:
            creds = db.scalars(
                select(WebAuthnCredential).where(
                    WebAuthnCredential.user_id == user.id,
                    WebAuthnCredential.rp_id == rp_id,
                )
            ).all()
            allow = [PublicKeyCredentialDescriptor(id=c.credential_id) for c in creds]
        # Unknown email → empty allow list, still return options (no enumeration).
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=allow or None,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    nonce = challenges.put(options.challenge, meta={"rp_id": rp_id})
    return {"nonce": nonce, "options": json.loads(options_to_json(options))}


@router.post("/login/verify", response_model=TokenResponse)
def login_verify(
    payload: WebAuthnLoginVerify,
    request: Request,
    db: Session = Depends(get_db),
) -> TokenResponse:
    rp_id, origin = _rp_for_request(request)
    try:
        entry = challenges.take(payload.nonce)
    except KeyError:
        raise HTTPException(status_code=400, detail="Anmeldung abgelaufen, bitte erneut.") from None
    if entry.meta.get("rp_id") != rp_id:
        raise HTTPException(status_code=400, detail="Origin passt nicht zur Anmeldung.")

    # Locate the credential by its raw id (base64url in the browser payload).
    raw_id = payload.credential.get("rawId") or payload.credential.get("id")
    if not raw_id:
        raise HTTPException(status_code=400, detail="Ungültige Anmeldedaten.")
    cred_id = webauthn_config.b64url_decode(raw_id)
    cred = db.scalar(
        select(WebAuthnCredential).where(
            WebAuthnCredential.credential_id == cred_id,
            WebAuthnCredential.rp_id == rp_id,
        )
    )
    if cred is None:
        raise HTTPException(status_code=400, detail="Passkey unbekannt.")
    user = db.get(User, cred.user_id)
    if user is None or not user.active:
        raise HTTPException(status_code=403, detail="Konto deaktiviert.")

    try:
        verified = verify_authentication_response(
            credential=payload.credential,
            expected_challenge=entry.challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=cred.public_key,
            credential_current_sign_count=cred.sign_count,
            require_user_verification=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Passkey-Prüfung fehlgeschlagen: {exc}") from exc

    # Clone detection: a non-increasing counter (when the authenticator uses one)
    # means a possible cloned credential.
    if verified.new_sign_count and verified.new_sign_count <= cred.sign_count:
        audit(db, user, "auth.webauthn.signcount_regression", f"cred={cred.id}")
        raise HTTPException(status_code=400, detail="Passkey-Zähler ungültig.")

    from datetime import UTC, datetime

    cred.sign_count = verified.new_sign_count
    cred.last_used_at = datetime.now(UTC)
    db.commit()
    token = create_access_token(user_id=user.id, role=user.role.value)
    return TokenResponse(access_token=token)


# --- Credential management (logged-in, own credentials) --------------------
@router.get("/credentials", response_model=list[PasskeyOut])
def list_credentials(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[WebAuthnCredential]:
    return list(
        db.scalars(
            select(WebAuthnCredential)
            .where(WebAuthnCredential.user_id == user.id)
            .order_by(WebAuthnCredential.created_at.desc())
        )
    )


def _own_credential(db: Session, user: User, cred_id: int) -> WebAuthnCredential:
    cred = db.get(WebAuthnCredential, cred_id)
    if cred is None or cred.user_id != user.id:
        raise HTTPException(status_code=404, detail="Passkey nicht gefunden.")
    return cred


@router.patch("/credentials/{cred_id}", response_model=PasskeyOut)
def rename_credential(
    cred_id: int,
    payload: PasskeyRename,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WebAuthnCredential:
    cred = _own_credential(db, user, cred_id)
    cred.name = payload.name.strip() or cred.name
    db.commit()
    db.refresh(cred)
    return cred


@router.delete("/credentials/{cred_id}", status_code=204)
def delete_credential(
    cred_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    cred = _own_credential(db, user, cred_id)
    audit(db, user, "auth.webauthn.delete", f"name={cred.name}")
    db.delete(cred)
    db.commit()
    return Response(status_code=204)
