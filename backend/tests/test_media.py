import io

from PIL import Image


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


def _folder(client, admin_auth, name="Pics"):
    return client.post("/api/folders", headers=admin_auth, json={"name": name}).json()


def _upload(client, auth, folder_id, filename, data: bytes):
    sess = client.post(
        f"/api/folders/{folder_id}/uploads",
        headers=auth,
        json={"filename": filename, "size": len(data)},
    ).json()
    client.put(f"/api/uploads/{sess['id']}", headers={**auth, "X-Offset": "0"}, content=data)
    return client.post(f"/api/uploads/{sess['id']}/complete", headers=auth).json()


def _token(client, email, password):
    return client.post(
        "/api/auth/login", json={"email": email, "password": password}
    ).json()["access_token"]


def test_image_thumbnail_is_generated(client, admin_auth):
    folder = _folder(client, admin_auth)
    media = _upload(client, admin_auth, folder["id"], "photo.png", _png_bytes())
    token = _token(client, "admin@test.local", "adminpass123")

    resp = client.get(f"/api/media/{media['id']}/thumbnail?token={token}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    # Output is a valid JPEG.
    Image.open(io.BytesIO(resp.content)).verify()


def test_non_image_has_no_thumbnail(client, admin_auth):
    folder = _folder(client, admin_auth)
    media = _upload(client, admin_auth, folder["id"], "notes.txt", b"hello world")
    token = _token(client, "admin@test.local", "adminpass123")
    resp = client.get(f"/api/media/{media['id']}/thumbnail?token={token}")
    assert resp.status_code == 404


def test_thumbnail_requires_access(client, admin_auth):
    folder = _folder(client, admin_auth)
    media = _upload(client, admin_auth, folder["id"], "p.png", _png_bytes())
    client.post(
        "/api/users",
        headers=admin_auth,
        json={"email": "outsider@test.local", "password": "userpass123"},
    )
    token = _token(client, "outsider@test.local", "userpass123")
    resp = client.get(f"/api/media/{media['id']}/thumbnail?token={token}")
    assert resp.status_code == 403


def test_thumbnail_bad_token(client, admin_auth):
    folder = _folder(client, admin_auth)
    media = _upload(client, admin_auth, folder["id"], "p.png", _png_bytes())
    resp = client.get(f"/api/media/{media['id']}/thumbnail?token=nope")
    assert resp.status_code == 401
