def _create_user(client, admin_auth, email="editor@test.local", role="user"):
    return client.post(
        "/api/users",
        headers=admin_auth,
        json={"email": email, "password": "userpass123", "role": role},
    )


def test_admin_can_create_and_list_users(client, admin_auth):
    resp = _create_user(client, admin_auth)
    assert resp.status_code == 201, resp.text
    assert resp.json()["role"] == "user"

    listing = client.get("/api/users", headers=admin_auth)
    assert listing.status_code == 200
    emails = [u["email"] for u in listing.json()]
    assert "editor@test.local" in emails


def test_duplicate_email_rejected(client, admin_auth):
    _create_user(client, admin_auth, email="dup@test.local")
    again = _create_user(client, admin_auth, email="dup@test.local")
    assert again.status_code == 409


def test_non_admin_cannot_manage_users(client, admin_auth):
    _create_user(client, admin_auth, email="plain@test.local")
    login = client.post(
        "/api/auth/login", json={"email": "plain@test.local", "password": "userpass123"}
    )
    token = login.json()["access_token"]
    user_auth = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/users", headers=user_auth).status_code == 403


def test_admin_can_deactivate_and_login_blocked(client, admin_auth):
    created = _create_user(client, admin_auth, email="off@test.local").json()
    patch = client.patch(
        f"/api/users/{created['id']}", headers=admin_auth, json={"active": False}
    )
    assert patch.status_code == 200
    assert patch.json()["active"] is False

    login = client.post(
        "/api/auth/login", json={"email": "off@test.local", "password": "userpass123"}
    )
    assert login.status_code == 403


def test_admin_cannot_deactivate_self(client, admin_auth):
    me = client.get("/api/auth/me", headers=admin_auth).json()
    resp = client.patch(
        f"/api/users/{me['id']}", headers=admin_auth, json={"active": False}
    )
    assert resp.status_code == 400
