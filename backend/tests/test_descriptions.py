"""Thematic descriptions and their generated .txt cloud sidecars."""

import io

from PIL import Image


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (200, 40, 40)).save(buf, "PNG")
    return buf.getvalue()


def _folder(client, admin_auth, name="Reise"):
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


def test_create_description_generates_sidecar(client, admin_auth):
    folder = _folder(client, admin_auth)
    a = _upload(client, admin_auth, folder["id"], "img1.png", _png_bytes())
    b = _upload(client, admin_auth, folder["id"], "clip.mp4", b"not really a video")

    resp = client.post(
        f"/api/folders/{folder['id']}/descriptions",
        headers=admin_auth,
        json={
            "title": "Bootsfahrt am Morgen",
            "body": "Zwei Aufnahmen vom Ablegen im Nebel.",
            "media_ids": [a["id"], b["id"]],
        },
    )
    assert resp.status_code == 201, resp.text
    desc = resp.json()
    assert desc["title"] == "Bootsfahrt am Morgen"
    assert set(desc["media_ids"]) == {a["id"], b["id"]}
    assert desc["txt_media_id"] is not None
    assert desc["txt_filename"].endswith(".txt")

    # The sidecar is a real media item in the folder and downloadable.
    media = client.get(f"/api/folders/{folder['id']}/media", headers=admin_auth).json()
    sidecar = next(m for m in media if m["id"] == desc["txt_media_id"])
    assert sidecar["filename"] == desc["txt_filename"]

    token = _token(client, "admin@test.local", "adminpass123")
    dl = client.get(f"/api/media/{desc['txt_media_id']}/download?token={token}")
    assert dl.status_code == 200
    text = dl.content.decode("utf-8")
    assert "Bootsfahrt am Morgen" in text
    assert "Zwei Aufnahmen vom Ablegen im Nebel." in text
    assert "img1.png" in text
    assert "clip.mp4" in text


def test_body_is_required(client, admin_auth):
    folder = _folder(client, admin_auth)
    resp = client.post(
        f"/api/folders/{folder['id']}/descriptions",
        headers=admin_auth,
        json={"title": "Leer", "body": "  ", "media_ids": []},
    )
    # Whitespace-only body collapses to empty -> validation rejects it.
    assert resp.status_code == 422


def test_description_without_media_is_allowed(client, admin_auth):
    folder = _folder(client, admin_auth)
    resp = client.post(
        f"/api/folders/{folder['id']}/descriptions",
        headers=admin_auth,
        json={"body": "Allgemeine Notiz zum Ordner.", "media_ids": []},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["media_ids"] == []


def test_list_and_update_regenerates_sidecar(client, admin_auth):
    folder = _folder(client, admin_auth)
    a = _upload(client, admin_auth, folder["id"], "photo.png", _png_bytes())
    created = client.post(
        f"/api/folders/{folder['id']}/descriptions",
        headers=admin_auth,
        json={"title": "Alt", "body": "Erste Fassung.", "media_ids": [a["id"]]},
    ).json()

    listing = client.get(
        f"/api/folders/{folder['id']}/descriptions", headers=admin_auth
    ).json()
    assert len(listing) == 1
    assert listing[0]["id"] == created["id"]

    resp = client.patch(
        f"/api/descriptions/{created['id']}",
        headers=admin_auth,
        json={"body": "Zweite Fassung mit mehr Details."},
    )
    assert resp.status_code == 200, resp.text
    # Sidecar filename stays stable across edits (no cloud orphan).
    assert resp.json()["txt_filename"] == created["txt_filename"]

    token = _token(client, "admin@test.local", "adminpass123")
    text = client.get(
        f"/api/media/{created['txt_media_id']}/download?token={token}"
    ).content.decode("utf-8")
    assert "Zweite Fassung mit mehr Details." in text
    assert "Erste Fassung." not in text


def test_delete_description_removes_sidecar(client, admin_auth):
    folder = _folder(client, admin_auth)
    created = client.post(
        f"/api/folders/{folder['id']}/descriptions",
        headers=admin_auth,
        json={"body": "Wird gelöscht.", "media_ids": []},
    ).json()
    sidecar_id = created["txt_media_id"]

    resp = client.delete(f"/api/descriptions/{created['id']}", headers=admin_auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] is True

    # Both the note and its generated file are gone.
    assert client.get(
        f"/api/folders/{folder['id']}/descriptions", headers=admin_auth
    ).json() == []
    media_ids = [
        m["id"]
        for m in client.get(f"/api/folders/{folder['id']}/media", headers=admin_auth).json()
    ]
    assert sidecar_id not in media_ids


def test_deleting_covered_media_shrinks_group(client, admin_auth):
    folder = _folder(client, admin_auth)
    a = _upload(client, admin_auth, folder["id"], "a.png", _png_bytes())
    b = _upload(client, admin_auth, folder["id"], "b.png", _png_bytes())
    client.post(
        f"/api/folders/{folder['id']}/descriptions",
        headers=admin_auth,
        json={"body": "Zwei Bilder.", "media_ids": [a["id"], b["id"]]},
    )

    client.delete(f"/api/folders/{folder['id']}/media/{a['id']}", headers=admin_auth)
    listing = client.get(
        f"/api/folders/{folder['id']}/descriptions", headers=admin_auth
    ).json()
    assert listing[0]["media_ids"] == [b["id"]]


def test_sidecar_is_queued_for_linked_provider(client, admin_auth):
    """The generated .txt fans out to the cloud like any other media item."""
    prov = client.post(
        "/api/providers",
        headers=admin_auth,
        json={
            "name": "S3",
            "type": "s3",
            "config": {"access_key_id": "a", "secret_access_key": "b"},
        },
    ).json()
    folder = _folder(client, admin_auth)
    client.post(
        f"/api/folders/{folder['id']}/providers",
        headers=admin_auth,
        json={"provider_id": prov["id"], "dest_path": "bucket/reise"},
    )
    created = client.post(
        f"/api/folders/{folder['id']}/descriptions",
        headers=admin_auth,
        json={"body": "Wird in die Cloud geladen.", "media_ids": []},
    ).json()

    # The sidecar media item is queued for the linked provider.
    media = client.get(f"/api/folders/{folder['id']}/media", headers=admin_auth).json()
    sidecar = next(m for m in media if m["id"] == created["txt_media_id"])
    assert sidecar["status"] == "queued"
    transfers = client.get("/api/transfers", headers=admin_auth).json()
    assert any(t["media_id"] == created["txt_media_id"] for t in transfers)


def test_access_is_folder_scoped(client, admin_auth):
    folder = _folder(client, admin_auth)
    created = client.post(
        f"/api/folders/{folder['id']}/descriptions",
        headers=admin_auth,
        json={"body": "Nur für Berechtigte.", "media_ids": []},
    ).json()

    client.post(
        "/api/users",
        headers=admin_auth,
        json={"email": "outsider@test.local", "password": "userpass123"},
    )
    outsider = {"Authorization": f"Bearer {_token(client, 'outsider@test.local', 'userpass123')}"}

    assert client.get(
        f"/api/folders/{folder['id']}/descriptions", headers=outsider
    ).status_code == 403
    assert client.post(
        f"/api/folders/{folder['id']}/descriptions",
        headers=outsider,
        json={"body": "hey", "media_ids": []},
    ).status_code == 403
    assert client.delete(
        f"/api/descriptions/{created['id']}", headers=outsider
    ).status_code == 403
