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


from app import webauthn_config  # noqa: E402


def test_parse_origin_extracts_rpid_and_origin():
    assert webauthn_config.parse_origin("https://offgridcloud.local") == (
        "offgridcloud.local",
        "https://offgridcloud.local",
    )
    # Port is kept in the origin but stripped from the RP-ID.
    assert webauthn_config.parse_origin("http://localhost:5173") == (
        "localhost",
        "http://localhost:5173",
    )


def test_parse_origin_rejects_garbage():
    for bad in ("", "not a url", "ftp://x"):
        with pytest.raises(ValueError):
            webauthn_config.parse_origin(bad)


def test_build_allowlist_from_state_and_localhost():
    allow = webauthn_config.build_allowlist(
        state={"hostname": "offgridcloud", "domain": "cloud.example.com"},
        extra_origins="",
    )
    assert "offgridcloud.local" in allow
    assert "cloud.example.com" in allow
    assert "localhost" in allow


def test_build_allowlist_empty_state_is_localhost_only():
    allow = webauthn_config.build_allowlist(state={"hostname": "", "domain": ""}, extra_origins="")
    assert allow == {"localhost"}


def test_build_allowlist_includes_extra_origins():
    allow = webauthn_config.build_allowlist(
        state={"hostname": "", "domain": ""}, extra_origins="box.example.net, other.local"
    )
    assert "box.example.net" in allow
    assert "other.local" in allow


def test_resolve_rejects_origin_not_in_allowlist():
    with pytest.raises(webauthn_config.OriginNotAllowed):
        webauthn_config.resolve_rp("https://evil.example.com", allowlist={"offgridcloud.local"})


def test_resolve_returns_rpid_and_origin_when_allowed():
    rp_id, origin = webauthn_config.resolve_rp(
        "https://offgridcloud.local", allowlist={"offgridcloud.local", "localhost"}
    )
    assert rp_id == "offgridcloud.local"
    assert origin == "https://offgridcloud.local"
