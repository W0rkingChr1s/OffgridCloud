"""Bulk media operations: multi-delete and ZIP bulk download."""

import io
import zipfile


def _folder(client, admin_auth, name="Tour"):
    return client.post("/api/folders", headers=admin_auth, json={"name": name}).json()


def _upload(client, auth, folder_id, filename, data: bytes):
    sess = client.post(
        f"/api/folders/{folder_id}/uploads",
        headers=auth,
        json={"filename": filename, "size": len(data)},
    ).json()
    client.put(f"/api/uploads/{sess['id']}", headers={**auth, "X-Offset": "0"}, content=data)
    return client.post(f"/api/uploads/{sess['id']}/complete", headers=auth).json()


def _token(client, email="admin@test.local", password="adminpass123"):
    return client.post(
        "/api/auth/login", json={"email": email, "password": password}
    ).json()["access_token"]


def test_bulk_delete_removes_all_selected(client, admin_auth):
    folder = _folder(client, admin_auth)
    a = _upload(client, admin_auth, folder["id"], "a.mp4", b"aaa")
    b = _upload(client, admin_auth, folder["id"], "b.mp4", b"bbb")

    resp = client.post(
        f"/api/folders/{folder['id']}/media/bulk-delete",
        headers=admin_auth,
        json={"media_ids": [a["id"], b["id"]]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["requested"] == 2
    assert body["deleted"] == 2
    assert body["not_found"] == []
    assert client.get(f"/api/folders/{folder['id']}/media", headers=admin_auth).json() == []


def test_bulk_delete_reports_unknown_ids(client, admin_auth):
    folder = _folder(client, admin_auth)
    a = _upload(client, admin_auth, folder["id"], "a.mp4", b"aaa")
    resp = client.post(
        f"/api/folders/{folder['id']}/media/bulk-delete",
        headers=admin_auth,
        json={"media_ids": [a["id"], 99999]},
    )
    body = resp.json()
    assert body["deleted"] == 1
    assert body["not_found"] == [99999]


def test_bulk_delete_requires_folder_access(client, admin_auth):
    folder = _folder(client, admin_auth)
    a = _upload(client, admin_auth, folder["id"], "a.mp4", b"aaa")
    # Create a user with no access to the folder.
    client.post(
        "/api/users",
        headers=admin_auth,
        json={"email": "u@test.local", "password": "userpass123", "role": "user"},
    )
    user_token = _token(client, "u@test.local", "userpass123")
    resp = client.post(
        f"/api/folders/{folder['id']}/media/bulk-delete",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"media_ids": [a["id"]]},
    )
    assert resp.status_code == 403


def test_bulk_delete_rejects_empty_list(client, admin_auth):
    folder = _folder(client, admin_auth)
    resp = client.post(
        f"/api/folders/{folder['id']}/media/bulk-delete",
        headers=admin_auth,
        json={"media_ids": []},
    )
    assert resp.status_code == 422


def test_bulk_download_zip_contains_selected_files(client, admin_auth):
    folder = _folder(client, admin_auth)
    a = _upload(client, admin_auth, folder["id"], "a.mp4", b"content-a")
    b = _upload(client, admin_auth, folder["id"], "b.mp4", b"content-b")
    token = _token(client)

    resp = client.get(
        f"/api/folders/{folder['id']}/download?ids={a['id']},{b['id']}&token={token}"
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = sorted(zf.namelist())
        assert names == ["a.mp4", "b.mp4"]
        assert zf.read("a.mp4") == b"content-a"
        assert zf.read("b.mp4") == b"content-b"


def test_bulk_download_whole_folder_when_no_ids(client, admin_auth):
    folder = _folder(client, admin_auth)
    _upload(client, admin_auth, folder["id"], "a.mp4", b"aaa")
    _upload(client, admin_auth, folder["id"], "b.mp4", b"bbb")
    token = _token(client)
    resp = client.get(f"/api/folders/{folder['id']}/download?token={token}")
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert len(zf.namelist()) == 2


def test_bulk_download_dedupes_colliding_names(client, admin_auth):
    folder = _folder(client, admin_auth)
    _upload(client, admin_auth, folder["id"], "same.mp4", b"one")
    _upload(client, admin_auth, folder["id"], "same.mp4", b"two")
    token = _token(client)
    resp = client.get(f"/api/folders/{folder['id']}/download?token={token}")
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        assert len(names) == 2
        assert len(set(names)) == 2  # no clobbering


def test_bulk_download_requires_valid_token(client, admin_auth):
    folder = _folder(client, admin_auth)
    _upload(client, admin_auth, folder["id"], "a.mp4", b"aaa")
    resp = client.get(f"/api/folders/{folder['id']}/download?token=garbage")
    assert resp.status_code == 401


def test_bulk_download_404_when_nothing_present(client, admin_auth):
    folder = _folder(client, admin_auth)
    token = _token(client)
    resp = client.get(f"/api/folders/{folder['id']}/download?token={token}")
    assert resp.status_code == 404
