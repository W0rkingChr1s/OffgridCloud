def test_provider_types_registry(client, admin_auth):
    types = client.get("/api/providers/types", headers=admin_auth).json()
    keys = {t["key"] for t in types}
    # A representative spread of the required providers.
    assert {"s3", "minio", "azureblob", "onedrive", "nextcloud", "sftp", "ftp", "smb"} <= keys
    s3 = next(t for t in types if t["key"] == "s3")
    secret_fields = {f["key"] for f in s3["fields"] if f["secret"]}
    assert "secret_access_key" in secret_fields


def test_registry_includes_major_providers(client, admin_auth):
    types = client.get("/api/providers/types", headers=admin_auth).json()
    keys = {t["key"] for t in types}
    # The "biggest" storage options must all be selectable in the wizard.
    assert {
        "googledrive", "dropbox", "box", "pcloud", "mega",
        "backblaze_b2", "cloudflare_r2", "wasabi", "gcs",
        "digitalocean_spaces", "idrive_e2", "scaleway", "storj",
    } <= keys
    # Every type carries wizard metadata.
    for t in types:
        assert t["category"]
        assert "popular" in t
        assert "description" in t


def test_provider_categories_endpoint(client, admin_auth):
    cats = client.get("/api/providers/categories", headers=admin_auth).json()
    keys = [c["key"] for c in cats]
    assert keys == ["object", "drive", "selfhosted", "protocol"]
    # Every provider type maps to a known category.
    types = client.get("/api/providers/types", headers=admin_auth).json()
    for t in types:
        assert t["category"] in keys


def test_oauth_provider_token_is_secret(client, admin_auth):
    types = client.get("/api/providers/types", headers=admin_auth).json()
    gdrive = next(t for t in types if t["key"] == "googledrive")
    token = next(f for f in gdrive["fields"] if f["key"] == "token")
    assert token["secret"] is True
    assert gdrive["category"] == "drive"


def test_create_googledrive_masks_token(client, admin_auth):
    resp = client.post(
        "/api/providers",
        headers=admin_auth,
        json={
            "name": "My Drive",
            "type": "googledrive",
            "config": {"token": '{"access_token":"xyz"}'},
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["config"]["token"] != '{"access_token":"xyz"}'
    assert set(body["config"]["token"]) == {"•"}


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


def test_obscure_roundtrip_matches_rclone_scheme():
    from app.rclone_obscure import obscure, reveal

    token = obscure("s3cr3t-pw")
    assert token != "s3cr3t-pw"
    # Fresh IV each call -> non-deterministic ciphertext.
    assert obscure("s3cr3t-pw") != token
    # ...but always reversible back to the original.
    assert reveal(token) == "s3cr3t-pw"
    assert obscure("") == ""


def test_smb_password_is_obscured_in_rclone_options():
    from app.providers_registry import get_type
    from app.rclone_obscure import reveal

    pt = get_type("smb")
    opts = pt.to_rclone_options(
        {"host": "192.168.178.5", "port": "445", "user": "u", "pass": "hunter2"}
    )
    # Host/port stay verbatim; the password is obscured (rclone-revealable).
    assert opts["host"] == "192.168.178.5"
    assert opts["port"] == "445"
    assert opts["pass"] != "hunter2"
    assert reveal(opts["pass"]) == "hunter2"


def test_s3_secret_is_not_obscured():
    from app.providers_registry import get_type

    pt = get_type("s3")
    opts = pt.to_rclone_options({"access_key_id": "AK", "secret_access_key": "plainsecret"})
    # S3 secrets are passed verbatim (not an rclone obscured-password field).
    assert opts["secret_access_key"] == "plainsecret"


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


# --- Nextcloud share link -------------------------------------------------


def test_share_link_parsing_variants():
    from app.nextcloud_share import parse_share_link

    assert parse_share_link("https://cloud.example.com/s/AbC123") == (
        "https://cloud.example.com/public.php/dav/files/AbC123/", "AbC123")
    # index.php variant, sub-path install, trailing slash, query/fragment.
    assert parse_share_link("https://example.com/nc/index.php/s/Tok9/?dir=/x#y") == (
        "https://example.com/nc/public.php/dav/files/Tok9/", "Tok9")
    for bad in ("", "cloud.example.com/s/AbC", "https://cloud.example.com/f/123",
                "ftp://cloud.example.com/s/AbC"):
        try:
            parse_share_link(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_share_link_rclone_options():
    from app.providers_registry import get_type
    from app.rclone_obscure import reveal

    pt = get_type("nextcloud_share")
    opts = pt.to_rclone_options({"link": "https://nx.example.de/s/8PAD7zy"})
    # vendor=nextcloud + /dav/files/<token> makes rclone upload in chunks via
    # /public.php/dav/uploads/<token>/ (one big PUT hits proxy limits -> 413).
    assert opts == {
        "type": "webdav",
        "url": "https://nx.example.de/public.php/dav/files/8PAD7zy/",
        "vendor": "nextcloud",
        "user": "8PAD7zy",
    }
    opts = pt.to_rclone_options({"link": "https://nx.example.de/s/8PAD7zy", "pass": "geheim"})
    assert reveal(opts["pass"]) == "geheim"


def test_create_share_without_password_and_reject_bad_link(client, admin_auth):
    ok = client.post(
        "/api/providers",
        headers=admin_auth,
        json={"name": "Share", "type": "nextcloud_share",
              "config": {"link": "https://cloud.example.com/s/AbC123"}},
    )
    assert ok.status_code == 201, ok.text
    bad = client.post(
        "/api/providers",
        headers=admin_auth,
        json={"name": "Kaputt", "type": "nextcloud_share",
              "config": {"link": "https://cloud.example.com/apps/files"}},
    )
    assert bad.status_code == 400
    assert "Freigabelink" in bad.json()["detail"]
    patched = client.patch(
        f"/api/providers/{ok.json()['id']}",
        headers=admin_auth,
        json={"config": {"link": "nonsense"}},
    )
    assert patched.status_code == 400


def _share_test(client, admin_auth, monkeypatch, perms, chunking=True):
    from app import nextcloud_share
    from app.rclone import TestResult
    from app.routers import providers

    monkeypatch.setattr(providers, "test_remote", lambda opts, sub="": TestResult(True, "ok"))
    monkeypatch.setattr(nextcloud_share, "_fetch_permissions", lambda *a: perms)
    monkeypatch.setattr(nextcloud_share, "_has_chunk_endpoint", lambda *a: chunking)
    return client.post(
        "/api/providers/test",
        headers=admin_auth,
        json={"type": "nextcloud_share",
              "config": {"link": "https://cloud.example.com/s/AbC123"}},
    ).json()


def test_share_test_flags_read_only_link(client, admin_auth, monkeypatch):
    res = _share_test(client, admin_auth, monkeypatch, "RG")
    assert res["ok"] is False
    assert "schreibgeschützt" in res["message"]


def test_share_test_passes_writable_link(client, admin_auth, monkeypatch):
    assert _share_test(client, admin_auth, monkeypatch, "RGDNVCK")["ok"] is True
    # Permissions unknown (network hiccup) -> don't block a working connection.
    assert _share_test(client, admin_auth, monkeypatch, None)["ok"] is True


def test_share_test_flags_server_without_chunk_endpoint(client, admin_auth, monkeypatch):
    res = _share_test(client, admin_auth, monkeypatch, "RGDNVCK", chunking=False)
    assert res["ok"] is False
    assert "Chunked-Upload" in res["message"]
