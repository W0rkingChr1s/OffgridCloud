def _user(client, admin_auth, email):
    return client.post(
        "/api/users", headers=admin_auth, json={"email": email, "password": "userpass123"}
    ).json()


def _auth(client, email):
    token = client.post(
        "/api/auth/login", json={"email": email, "password": "userpass123"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_group_crud_and_membership(client, admin_auth):
    u = _user(client, admin_auth, "g1@test.local")
    group = client.post("/api/groups", headers=admin_auth, json={"name": "Crew"}).json()
    assert group["name"] == "Crew"

    upd = client.put(
        f"/api/groups/{group['id']}/members",
        headers=admin_auth,
        json={"user_ids": [u["id"]]},
    ).json()
    assert upd["member_ids"] == [u["id"]]


def test_duplicate_group_name_rejected(client, admin_auth):
    client.post("/api/groups", headers=admin_auth, json={"name": "Dupe"})
    again = client.post("/api/groups", headers=admin_auth, json={"name": "Dupe"})
    assert again.status_code == 409


def test_group_grants_folder_access(client, admin_auth):
    user = _user(client, admin_auth, "member@test.local")
    group = client.post("/api/groups", headers=admin_auth, json={"name": "Field"}).json()
    client.put(
        f"/api/groups/{group['id']}/members",
        headers=admin_auth,
        json={"user_ids": [user["id"]]},
    )
    folder = client.post("/api/folders", headers=admin_auth, json={"name": "Shared"}).json()

    user_auth = _auth(client, "member@test.local")
    # No access yet.
    assert client.get("/api/folders", headers=user_auth).json() == []

    # Share folder with the group -> the member now sees it and can upload.
    client.put(
        f"/api/folders/{folder['id']}/groups",
        headers=admin_auth,
        json={"group_ids": [group["id"]]},
    )
    visible = client.get("/api/folders", headers=user_auth).json()
    assert [f["id"] for f in visible] == [folder["id"]]

    sess = client.post(
        f"/api/folders/{folder['id']}/uploads",
        headers=user_auth,
        json={"filename": "x.jpg", "size": 3},
    )
    assert sess.status_code == 201


def test_removing_membership_revokes_access(client, admin_auth):
    user = _user(client, admin_auth, "rev@test.local")
    group = client.post("/api/groups", headers=admin_auth, json={"name": "Temp"}).json()
    folder = client.post("/api/folders", headers=admin_auth, json={"name": "TempFolder"}).json()
    client.put(
        f"/api/groups/{group['id']}/members",
        headers=admin_auth,
        json={"user_ids": [user["id"]]},
    )
    client.put(
        f"/api/folders/{folder['id']}/groups",
        headers=admin_auth,
        json={"group_ids": [group["id"]]},
    )
    user_auth = _auth(client, "rev@test.local")
    assert len(client.get("/api/folders", headers=user_auth).json()) == 1

    # Empty the group's membership -> access revoked.
    client.put(f"/api/groups/{group['id']}/members", headers=admin_auth, json={"user_ids": []})
    assert client.get("/api/folders", headers=user_auth).json() == []


def test_non_admin_cannot_manage_groups(client, admin_auth):
    _user(client, admin_auth, "plain@test.local")
    user_auth = _auth(client, "plain@test.local")
    assert client.get("/api/groups", headers=user_auth).status_code == 403
