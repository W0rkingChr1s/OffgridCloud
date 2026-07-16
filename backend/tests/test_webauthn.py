"""WebAuthn ceremony endpoints. py_webauthn verify functions are monkeypatched
so tests exercise our server logic without a real authenticator."""

from __future__ import annotations

from dataclasses import dataclass

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


@dataclass
class _FakeVerifiedAuth:
    new_sign_count: int = 5
    credential_id: bytes = b"cred-123"


def _register_a_credential(client, admin_auth, monkeypatch, cred_id=b"cred-123"):
    """Helper: drive register options+verify with a stubbed authenticator."""
    import app.routers.webauthn as wa

    opts = client.post(
        "/api/auth/webauthn/register/options",
        headers={**admin_auth, **ORIGIN_HEADERS},
    ).json()

    @dataclass
    class _Reg:
        credential_id: bytes = cred_id
        credential_public_key: bytes = b"pubkey"
        sign_count: int = 0

    monkeypatch.setattr(wa, "verify_registration_response", lambda **kw: _Reg())
    client.post(
        "/api/auth/webauthn/register/verify",
        headers={**admin_auth, **ORIGIN_HEADERS},
        json={"nonce": opts["nonce"], "credential": {}, "name": "Test"},
    )


def test_login_options_unknown_email_is_enumeration_safe(client):
    resp = client.post(
        "/api/auth/webauthn/login/options",
        headers=ORIGIN_HEADERS,
        json={"email": "nobody@test.local"},
    )
    # Still 200 with a challenge; no "user not found" leak.
    assert resp.status_code == 200
    assert "nonce" in resp.json()


def test_login_verify_issues_token(client, admin_auth, monkeypatch):
    import base64

    import app.routers.webauthn as wa

    _register_a_credential(client, admin_auth, monkeypatch)

    opts = client.post(
        "/api/auth/webauthn/login/options",
        headers=ORIGIN_HEADERS,
        json={"email": "admin@test.local"},
    ).json()

    monkeypatch.setattr(wa, "verify_authentication_response", lambda **kw: _FakeVerifiedAuth())
    raw = base64.urlsafe_b64encode(b"cred-123").rstrip(b"=").decode()
    resp = client.post(
        "/api/auth/webauthn/login/verify",
        headers=ORIGIN_HEADERS,
        json={"nonce": opts["nonce"], "credential": {"id": raw, "rawId": raw}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]


def test_login_verify_rejects_stale_nonce(client):
    resp = client.post(
        "/api/auth/webauthn/login/verify",
        headers=ORIGIN_HEADERS,
        json={"nonce": "bogus", "credential": {}},
    )
    assert resp.status_code == 400


def test_list_rename_delete_credentials(client, admin_auth, monkeypatch):
    _register_a_credential(client, admin_auth, monkeypatch)

    listed = client.get("/api/auth/webauthn/credentials", headers=admin_auth).json()
    assert len(listed) == 1
    cid = listed[0]["id"]

    renamed = client.patch(
        f"/api/auth/webauthn/credentials/{cid}",
        headers=admin_auth,
        json={"name": "Renamed Key"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed Key"

    deleted = client.delete(f"/api/auth/webauthn/credentials/{cid}", headers=admin_auth)
    assert deleted.status_code == 204
    assert client.get("/api/auth/webauthn/credentials", headers=admin_auth).json() == []


def test_credentials_require_auth(client):
    assert client.get("/api/auth/webauthn/credentials").status_code in (401, 403)


def test_cannot_delete_another_users_credential(client, admin_auth, monkeypatch):
    _register_a_credential(client, admin_auth, monkeypatch)
    listed = client.get("/api/auth/webauthn/credentials", headers=admin_auth).json()
    cid = listed[0]["id"]

    # Make a second user and log in as them.
    client.post(
        "/api/users",
        headers=admin_auth,
        json={"email": "other@test.local", "password": "otherpass123", "role": "user"},
    )
    token = client.post(
        "/api/auth/login", json={"email": "other@test.local", "password": "otherpass123"}
    ).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}

    assert client.delete(f"/api/auth/webauthn/credentials/{cid}", headers=other).status_code == 404
