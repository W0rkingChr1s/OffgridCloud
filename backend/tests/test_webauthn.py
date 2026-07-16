"""WebAuthn ceremony endpoints. py_webauthn verify functions are monkeypatched
so tests exercise our server logic without a real authenticator."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

ORIGIN_HEADERS = {"Origin": "http://localhost:5173"}


def test_register_options_requires_auth(client):
    # No token → 401/403 from the bearer dependency.
    resp = client.post("/api/auth/webauthn/register/options", headers=ORIGIN_HEADERS)
    assert resp.status_code in (401, 403)


def test_register_options_returns_options_and_nonce(client, admin_auth):
    resp = client.post(
        "/api/auth/webauthn/register/options",
        headers={**admin_auth, **ORIGIN_HEADERS},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "nonce" in body
    # options_to_json emits the options object directly (challenge, rp, user…),
    # NOT nested under a "publicKey" key.
    assert "challenge" in body["options"]
    assert "rp" in body["options"]


def test_register_options_rejects_disallowed_origin(client, admin_auth):
    resp = client.post(
        "/api/auth/webauthn/register/options",
        headers={**admin_auth, "Origin": "https://evil.example.com"},
    )
    assert resp.status_code == 400


@dataclass
class _FakeVerifiedReg:
    credential_id: bytes = b"cred-123"
    credential_public_key: bytes = b"pubkey"
    sign_count: int = 0


def test_register_verify_persists_credential(client, admin_auth, monkeypatch):
    import app.routers.webauthn as wa

    # 1) get options to seed a challenge + nonce
    opts = client.post(
        "/api/auth/webauthn/register/options",
        headers={**admin_auth, **ORIGIN_HEADERS},
    ).json()

    # 2) stub py_webauthn verification
    monkeypatch.setattr(wa, "verify_registration_response", lambda **kw: _FakeVerifiedReg())

    resp = client.post(
        "/api/auth/webauthn/register/verify",
        headers={**admin_auth, **ORIGIN_HEADERS},
        json={"nonce": opts["nonce"], "credential": {"fake": "attestation"}, "name": "My Laptop"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "My Laptop"
    assert body["rp_id"] == "localhost"


def test_register_verify_rejects_stale_nonce(client, admin_auth, monkeypatch):
    import app.routers.webauthn as wa

    monkeypatch.setattr(wa, "verify_registration_response", lambda **kw: _FakeVerifiedReg())
    resp = client.post(
        "/api/auth/webauthn/register/verify",
        headers={**admin_auth, **ORIGIN_HEADERS},
        json={"nonce": "bogus", "credential": {}, "name": ""},
    )
    assert resp.status_code == 400
