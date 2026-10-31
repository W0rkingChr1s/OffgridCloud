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


def _share_test(client, admin_auth, monkeypatch, perms, chunking=True, rclone=(1, 75, 2)):
    from app import nextcloud_share
    from app.rclone import TestResult
    from app.routers import providers

    monkeypatch.setattr(providers, "test_remote", lambda opts, sub="": TestResult(True, "ok"))
    monkeypatch.setattr(nextcloud_share, "_fetch_permissions", lambda *a: perms)
    monkeypatch.setattr(nextcloud_share, "_has_chunk_endpoint", lambda *a: chunking)
    monkeypatch.setattr(nextcloud_share, "_probe_chunk_endpoint", lambda *a: chunking)
    monkeypatch.setattr(nextcloud_share, "_server_version", lambda *a: "31.0.9")
    monkeypatch.setattr(nextcloud_share.rclone, "rclone_version", lambda: rclone)
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


def test_share_test_notes_server_without_chunk_endpoint(client, admin_auth, monkeypatch):
    # Works (whole files, videos split), but the admin should know the limit.
    res = _share_test(client, admin_auth, monkeypatch, "RGDNVCK", chunking=False)
    assert res["ok"] is True
    assert "Chunked-Upload" in res["message"]
    assert "Nextcloud 31.0.9" in res["message"]


def test_share_test_flags_rclone_without_chunking(client, admin_auth, monkeypatch):
    # rclone < 1.63 ignores vendor=nextcloud chunking and PUTs whole files.
    res = _share_test(client, admin_auth, monkeypatch, "RGDNVCK", rclone=(1, 60, 1))
    assert res["ok"] is False
    assert "rclone 1.60.1" in res["message"]
    # Unknown version -> don't block.
    assert _share_test(client, admin_auth, monkeypatch, "RGDNVCK", rclone=None)["ok"] is True


def test_parse_rclone_version():
    from app.rclone import parse_version

    assert parse_version("rclone v1.60.1-DEV") == (1, 60, 1)
    assert parse_version("rclone v1.75.2") == (1, 75, 2)
    assert parse_version("rclone 1.63") == (1, 63, 0)
    assert parse_version("") is None
    assert parse_version(None) is None


# --- Share links on servers without public chunking (Nextcloud < 32) -----


def _http_error(code):
    import urllib.error

    return urllib.error.HTTPError("u", code, "x", {}, None)


def test_share_chunk_probe_classifies_responses(monkeypatch):
    from app import nextcloud_share

    def fake(code):
        def _open(req, timeout):
            raise _http_error(code)
        return _open

    url = "https://c.example.com/public.php/dav/files/Tok/"
    monkeypatch.setattr(nextcloud_share.urllib.request, "urlopen", fake(404))
    assert nextcloud_share._probe_chunk_endpoint(url, "Tok", "") is False
    monkeypatch.setattr(nextcloud_share.urllib.request, "urlopen", fake(503))
    assert nextcloud_share._probe_chunk_endpoint(url, "Tok", "") is None


def test_share_upload_options_disable_chunking_without_endpoint(monkeypatch):
    from app import nextcloud_share

    nextcloud_share._chunk_cache.clear()
    calls = []

    def probe(url, token, pw):
        calls.append(url)
        return False

    monkeypatch.setattr(nextcloud_share, "_probe_chunk_endpoint", probe)
    cfg = {"link": "https://cloud.example.com/s/AbC123"}
    base = {"type": "webdav", "vendor": "nextcloud"}
    assert nextcloud_share.share_upload_options(cfg, base)["nextcloud_chunk_size"] == "0"
    nextcloud_share.share_upload_options(cfg, base)
    assert len(calls) == 1  # cached

    nextcloud_share._chunk_cache.clear()
    monkeypatch.setattr(nextcloud_share, "_probe_chunk_endpoint", lambda *a: True)
    assert "nextcloud_chunk_size" not in nextcloud_share.share_upload_options(cfg, base)

    # Unknown (network hiccup): keep chunking, don't cache.
    nextcloud_share._chunk_cache.clear()
    monkeypatch.setattr(nextcloud_share, "_probe_chunk_endpoint", lambda *a: None)
    assert "nextcloud_chunk_size" not in nextcloud_share.share_upload_options(cfg, base)
    assert nextcloud_share._chunk_cache == {}


def test_share_upload_error_gets_hint():
    from app.nextcloud_share import explain_upload_error

    msg = "making upload directory failed: Parent node does not exist: 409 Conflict"
    assert "Nextcloud 32" in explain_upload_error(msg)
    assert "Nextcloud 32" in explain_upload_error("413 Payload Too Large")
    assert explain_upload_error("connection refused") == "connection refused"


def test_process_job_applies_upload_options_and_error_hint(client, admin_auth, monkeypatch):
    import dataclasses

    from sqlalchemy import select

    from app import transfers
    from app.db import SessionLocal
    from app.models import TransferJob
    from app.rclone import UploadResult
    from tests.test_tether import _queue_job

    media = _queue_job(client, admin_auth, b"v" * 64)
    real_get_type = transfers.get_type

    def patched(key):
        pt = real_get_type(key)
        return dataclasses.replace(
            pt,
            upload_options=lambda cfg, opts: {**opts, "nextcloud_chunk_size": "0"},
            explain_error=lambda msg: msg + " | Hinweis",
        )

    monkeypatch.setattr(transfers, "get_type", patched)
    seen = []

    def upload(local, options, dest, bwlimit=0, on_progress=None):
        seen.append(options.get("nextcloud_chunk_size"))
        return UploadResult(False, 0, "413 Payload Too Large", 0.0)

    with SessionLocal() as db:
        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        transfers.process_job(db, job, upload)
        assert job.last_error == "413 Payload Too Large | Hinweis"
    assert seen == ["0"]



def test_saved_share_test_keeps_note_and_refresh_updates_it(client, admin_auth, monkeypatch):
    from datetime import datetime, timedelta

    from app import nextcloud_share, provider_health
    from app.db import SessionLocal
    from app.models import CloudProvider
    from app.rclone import TestResult
    from app.routers import providers

    monkeypatch.setattr(providers, "test_remote", lambda opts, sub="": TestResult(True, "ok"))
    monkeypatch.setattr(nextcloud_share, "_fetch_permissions", lambda *a: "RGDNVCK")
    monkeypatch.setattr(nextcloud_share, "_server_version", lambda *a: "31.0.9")
    monkeypatch.setattr(nextcloud_share.rclone, "rclone_version", lambda: (1, 75, 2))
    probe = {"v": False}
    monkeypatch.setattr(nextcloud_share, "_probe_chunk_endpoint", lambda *a: probe["v"])
    prov = client.post(
        "/api/providers", headers=admin_auth,
        json={"name": "FFW", "type": "nextcloud_share",
              "config": {"link": "https://cloud.example.com/s/AbC123", "split_mb": "80"}},
    ).json()
    out = client.post(f"/api/providers/{prov['id']}/test", headers=admin_auth).json()
    assert out["status"] == "ok"
    assert "Nextcloud 31.0.9" in out["note"] and "80 MB" in out["note"]

    # Server offline: the note stays.
    probe["v"] = None
    with SessionLocal() as db:
        db.get(CloudProvider, prov["id"]).note_checked_at = None
        db.commit()
        assert provider_health.refresh_notes(db) == 0
        assert db.get(CloudProvider, prov["id"]).note

    # Hetzner updated to Nextcloud 32: the note goes away, but not before it's due.
    probe["v"] = True
    with SessionLocal() as db:
        now = datetime(2030, 1, 1)
        db.get(CloudProvider, prov["id"]).note_checked_at = now - timedelta(hours=1)
        db.commit()
        assert provider_health.refresh_notes(db, now) == 0
        assert provider_health.refresh_notes(db, now + timedelta(hours=6)) == 1
        assert db.get(CloudProvider, prov["id"]).note == ""
