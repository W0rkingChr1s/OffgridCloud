"""Tests for media tags and the cross-folder search endpoint."""

from tests.test_folders import _auth, _make_user
from tests.test_transfers import _folder, _upload


def test_put_normalises_and_get_returns_tags(client, admin_auth):
    folder = _folder(client, admin_auth)
    media = _upload(client, admin_auth, folder["id"], "clip.mp4", b"x")

    resp = client.put(
        f"/api/media/{media['id']}/tags",
        headers=admin_auth,
        json={"tags": ["Drone", "drone", " Eilig ", ""]},
    )
    assert resp.status_code == 200
    assert resp.json() == ["drone", "eilig"]  # trimmed, lower-cased, de-duped

    assert client.get(f"/api/media/{media['id']}/tags", headers=admin_auth).json() == [
        "drone",
        "eilig",
    ]


def test_tags_appear_in_folder_listing(client, admin_auth):
    folder = _folder(client, admin_auth)
    media = _upload(client, admin_auth, folder["id"], "a.mp4", b"x")
    client.put(
        f"/api/media/{media['id']}/tags", headers=admin_auth, json={"tags": ["interview"]}
    )

    listing = client.get(f"/api/folders/{folder['id']}/media", headers=admin_auth).json()
    item = next(m for m in listing if m["id"] == media["id"])
    assert item["tags"] == ["interview"]


def test_search_by_filename_tag_and_folder(client, admin_auth):
    # Unique tag/name tokens keep this robust against the module-shared DB.
    folder = _folder(client, admin_auth, name="Boot")
    m1 = _upload(client, admin_auth, folder["id"], "srch-sunset.mp4", b"x")
    m2 = _upload(client, admin_auth, folder["id"], "srch-interview.mov", b"y")
    client.put(f"/api/media/{m1['id']}/tags", headers=admin_auth, json={"tags": ["srch-drone"]})

    by_name = client.get("/api/media/search?q=srch-sun", headers=admin_auth).json()
    assert [m["id"] for m in by_name] == [m1["id"]]

    by_tag = client.get("/api/media/search?tag=srch-drone", headers=admin_auth).json()
    assert [m["id"] for m in by_tag] == [m1["id"]]
    assert by_tag[0]["folder_name"] == "Boot"
    assert by_tag[0]["tags"] == ["srch-drone"]

    by_folder = client.get(
        f"/api/media/search?folder_id={folder['id']}", headers=admin_auth
    ).json()
    assert {m["id"] for m in by_folder} == {m1["id"], m2["id"]}


def test_list_all_tags_sorted(client, admin_auth):
    folder = _folder(client, admin_auth)
    media = _upload(client, admin_auth, folder["id"], "a.mp4", b"x")
    client.put(
        f"/api/media/{media['id']}/tags", headers=admin_auth, json={"tags": ["ztag-b", "ztag-a"]}
    )
    tags = client.get("/api/media/tags", headers=admin_auth).json()
    assert tags == sorted(tags)  # globally sorted
    assert "ztag-a" in tags and "ztag-b" in tags


def test_tags_removed_when_media_deleted(client, admin_auth):
    folder = _folder(client, admin_auth)
    media = _upload(client, admin_auth, folder["id"], "a.mp4", b"x")
    client.put(f"/api/media/{media['id']}/tags", headers=admin_auth, json={"tags": ["del-gone"]})
    client.delete(f"/api/folders/{folder['id']}/media/{media['id']}", headers=admin_auth)

    assert client.get("/api/media/search?tag=del-gone", headers=admin_auth).json() == []


def test_search_is_scoped_to_accessible_folders(client, admin_auth):
    folder = _folder(client, admin_auth, name="Secret")
    media = _upload(client, admin_auth, folder["id"], "secret.mp4", b"x")
    client.put(f"/api/media/{media['id']}/tags", headers=admin_auth, json={"tags": ["scope-x"]})

    _make_user(client, admin_auth, "scoped@test.local")
    user_auth = _auth(client, "scoped@test.local")

    # No folder access → sees nothing and cannot tag.
    assert client.get("/api/media/search?tag=scope-x", headers=user_auth).json() == []
    assert client.get(f"/api/media/{media['id']}/tags", headers=user_auth).status_code == 403
    assert (
        client.put(
            f"/api/media/{media['id']}/tags", headers=user_auth, json={"tags": ["y"]}
        ).status_code
        == 403
    )
