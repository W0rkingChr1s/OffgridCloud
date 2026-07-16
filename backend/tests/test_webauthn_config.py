"""WebAuthn: model + pure helpers (origin/RP-ID derivation, challenge store)."""

from __future__ import annotations

import pytest


def test_webauthn_credential_model_importable():
    from app.models import WebAuthnCredential

    # The columns the ceremonies rely on exist.
    cols = WebAuthnCredential.__table__.columns.keys()
    for expected in (
        "id",
        "user_id",
        "credential_id",
        "public_key",
        "sign_count",
        "rp_id",
        "transports",
        "name",
        "created_at",
        "last_used_at",
    ):
        assert expected in cols


def test_user_has_webauthn_handle_column():
    from app.models import User

    assert "webauthn_user_handle" in User.__table__.columns.keys()
