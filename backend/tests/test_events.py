from app.routers.events import build_snapshot


def _admin_id(client, admin_auth):
    return client.get("/api/auth/me", headers=admin_auth).json()["id"]


def _upload(client, auth, folder_id, filename, data: bytes):
    sess = client.post(
        f"/api/folders/{folder_id}/uploads",
        headers=auth,
        json={"filename": filename, "size": len(data)},
    ).json()
    client.put(f"/api/uploads/{sess['id']}", headers={**auth, "X-Offset": "0"}, content=data)
    return client.post(f"/api/uploads/{sess['id']}/complete", headers=auth).json()


def test_snapshot_includes_folder_progress(client, admin_auth):
    folder = client.post("/api/folders", headers=admin_auth, json={"name": "Tour"}).json()
    _upload(client, admin_auth, folder["id"], "a.mp4", b"data")

    snap = build_snapshot(_admin_id(client, admin_auth))
    assert snap is not None
    f = next(x for x in snap["folders"] if x["id"] == folder["id"])
    assert f["total"] == 1
    # Admin snapshot carries transfer + bandwidth sections.
    assert "transfers" in snap
    assert "bandwidth" in snap


def test_snapshot_scoped_for_regular_user(client, admin_auth):
    # Two folders; user only sees the shared one, and gets no admin sections.
    shared = client.post("/api/folders", headers=admin_auth, json={"name": "Shared"}).json()
    client.post("/api/folders", headers=admin_auth, json={"name": "Hidden"}).json()
    user = client.post(
        "/api/users",
        headers=admin_auth,
        json={"email": "viewer@test.local", "password": "userpass123"},
    ).json()
    client.put(
        f"/api/folders/{shared['id']}/access",
        headers=admin_auth,
        json={"user_ids": [user["id"]]},
    )

    snap = build_snapshot(user["id"])
    assert snap is not None
    assert [f["name"] for f in snap["folders"]] == ["Shared"]
    assert "transfers" not in snap
    assert "bandwidth" not in snap


def test_snapshot_none_for_unknown_user():
    assert build_snapshot(999999) is None
