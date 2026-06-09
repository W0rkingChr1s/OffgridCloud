from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD


def test_login_success_and_me(client):
    resp = client.post(
        "/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == ADMIN_EMAIL
    assert body["role"] == "admin"


def test_login_wrong_password(client):
    resp = client.post(
        "/api/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"}
    )
    assert resp.status_code == 401


def test_me_requires_token(client):
    assert client.get("/api/auth/me").status_code in (401, 403)


def test_me_rejects_garbage_token(client):
    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401
