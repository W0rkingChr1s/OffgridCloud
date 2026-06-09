def test_provider_types_registry(client, admin_auth):
    types = client.get("/api/providers/types", headers=admin_auth).json()
    keys = {t["key"] for t in types}
    # A representative spread of the required providers.
    assert {"s3", "minio", "azureblob", "onedrive", "nextcloud", "sftp", "ftp", "smb"} <= keys
    s3 = next(t for t in types if t["key"] == "s3")
    secret_fields = {f["key"] for f in s3["fields"] if f["secret"]}
    assert "secret_access_key" in secret_fields


def _create_s3(client, admin_auth, name="Backup S3"):
    return client.post(
        "/api/providers",
        headers=admin_auth,
        json={
            "name": name,
            "type": "s3",
            "config": {
                "access_key_id": "AKIA123",
                "secret_access_key": "supersecret",
                "region": "eu-central-1",
            },
        },
    )


def test_create_masks_secret_in_response(client, admin_auth):
    resp = _create_s3(client, admin_auth)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "unknown"
    # Non-secret stays visible, secret is masked, plaintext never returned.
    assert body["config"]["access_key_id"] == "AKIA123"
    assert body["config"]["secret_access_key"] != "supersecret"
    assert set(body["config"]["secret_access_key"]) == {"•"}


def test_secret_is_encrypted_at_rest(client, admin_auth):
    _create_s3(client, admin_auth, name="EncCheck")
    # Reach into the DB and confirm the plaintext secret is not stored.
    from app.db import SessionLocal
    from app.models import CloudProvider

    with SessionLocal() as db:
        p = db.query(CloudProvider).filter_by(name="EncCheck").one()
        assert "supersecret" not in p.config_encrypted


def test_missing_required_field_rejected(client, admin_auth):
    resp = client.post(
        "/api/providers",
        headers=admin_auth,
        json={"name": "Bad", "type": "s3", "config": {"access_key_id": "x"}},
    )
    assert resp.status_code == 400
    assert "Secret Access Key" in resp.json()["detail"]


def test_update_keeps_secret_when_mask_sent(client, admin_auth):
    pid = _create_s3(client, admin_auth, name="KeepSecret").json()["id"]
    # Send back the masked value -> secret must be preserved, region updated.
    client.patch(
        f"/api/providers/{pid}",
        headers=admin_auth,
        json={"config": {"secret_access_key": "••••••", "region": "us-east-1"}},
    )
    import json as _json

    from app.crypto import decrypt
    from app.db import SessionLocal
    from app.models import CloudProvider

    with SessionLocal() as db:
        p = db.get(CloudProvider, pid)
        cfg = _json.loads(decrypt(p.config_encrypted))
    assert cfg["secret_access_key"] == "supersecret"
    assert cfg["region"] == "us-east-1"


def test_test_endpoint_reports_when_rclone_missing(client, admin_auth):
    # rclone is not installed in CI/test -> test should fail gracefully, not crash.
    resp = client.post(
        "/api/providers/test",
        headers=admin_auth,
        json={
            "type": "s3",
            "config": {"access_key_id": "a", "secret_access_key": "b"},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_non_admin_cannot_list_providers(client, admin_auth):
    client.post(
        "/api/users",
        headers=admin_auth,
        json={"email": "p@test.local", "password": "userpass123"},
    )
    token = client.post(
        "/api/auth/login", json={"email": "p@test.local", "password": "userpass123"}
    ).json()["access_token"]
    resp = client.get("/api/providers", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
