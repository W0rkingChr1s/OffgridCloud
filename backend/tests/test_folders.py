import hashlib


def _make_user(client, admin_auth, email):
    return client.post(
        "/api/users",
        headers=admin_auth,
        json={"email": email, "password": "userpass123"},
    ).json()


def _auth(client, email, password="userpass123"):
    token = client.post(
        "/api/auth/login", json={"email": email, "password": password}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_admin_creates_folder_and_shares(client, admin_auth):
    user = _make_user(client, admin_auth, "f1@test.local")
    folder = client.post(
        "/api/folders", headers=admin_auth, json={"name": "Boot-Tour"}
    ).json()
    assert folder["name"] == "Boot-Tour"

    # User sees nothing yet.
    user_auth = _auth(client, "f1@test.local")
    assert client.get("/api/folders", headers=user_auth).json() == []

    # Share, then the user sees it.
    client.put(
        f"/api/folders/{folder['id']}/access",
        headers=admin_auth,
        json={"user_ids": [user["id"]]},
    )
    visible = client.get("/api/folders", headers=user_auth).json()
    assert [f["id"] for f in visible] == [folder["id"]]


def test_user_cannot_create_folder(client, admin_auth):
    _make_user(client, admin_auth, "f2@test.local")
    user_auth = _auth(client, "f2@test.local")
    assert client.post("/api/folders", headers=user_auth, json={"name": "x"}).status_code == 403


def _upload_file(client, auth, folder_id, filename, data: bytes, chunk=7):
    sess = client.post(
        f"/api/folders/{folder_id}/uploads",
        headers=auth,
        json={"filename": filename, "size": len(data)},
    ).json()
    uid = sess["id"]
    offset = sess["received"]
    while offset < len(data):
        part = data[offset : offset + chunk]
        resp = client.put(
            f"/api/uploads/{uid}",
            headers={**auth, "X-Offset": str(offset)},
            content=part,
        )
        assert resp.status_code == 200, resp.text
        offset = resp.json()["received"]
    return client.post(f"/api/uploads/{uid}/complete", headers=auth)


def test_chunked_upload_creates_media_with_hash(client, admin_auth):
    user = _make_user(client, admin_auth, "up@test.local")
    folder = client.post("/api/folders", headers=admin_auth, json={"name": "LKW"}).json()
    client.put(
        f"/api/folders/{folder['id']}/access",
        headers=admin_auth,
        json={"user_ids": [user["id"]]},
    )
    user_auth = _auth(client, "up@test.local")

    data = b"offgridcloud-media-payload-" * 10
    resp = _upload_file(client, user_auth, folder["id"], "clip.mp4", data)
    assert resp.status_code == 200, resp.text
    media = resp.json()
    assert media["filename"] == "clip.mp4"
    assert media["size"] == len(data)
    assert media["sha256"] == hashlib.sha256(data).hexdigest()
    assert media["status"] == "received"

    listing = client.get(f"/api/folders/{folder['id']}/media", headers=user_auth).json()
    assert [m["id"] for m in listing] == [media["id"]]


def test_upload_rejected_without_access(client, admin_auth):
    _make_user(client, admin_auth, "noacc@test.local")
    folder = client.post("/api/folders", headers=admin_auth, json={"name": "Secret"}).json()
    user_auth = _auth(client, "noacc@test.local")
    resp = client.post(
        f"/api/folders/{folder['id']}/uploads",
        headers=user_auth,
        json={"filename": "x.jpg", "size": 3},
    )
    assert resp.status_code == 403


def test_offset_mismatch_returns_409(client, admin_auth):
    folder = client.post("/api/folders", headers=admin_auth, json={"name": "R"}).json()
    sess = client.post(
        f"/api/folders/{folder['id']}/uploads",
        headers=admin_auth,
        json={"filename": "a.bin", "size": 10},
    ).json()
    # Send at a wrong offset.
    resp = client.put(
        f"/api/uploads/{sess['id']}",
        headers={**admin_auth, "X-Offset": "5"},
        content=b"xxxxx",
    )
    assert resp.status_code == 409


def test_filename_path_traversal_is_sanitised(client, admin_auth):
    folder = client.post("/api/folders", headers=admin_auth, json={"name": "S"}).json()
    resp = _upload_file(client, admin_auth, folder["id"], "../../etc/passwd", b"data")
    assert resp.status_code == 200
    assert resp.json()["filename"] == "passwd"
