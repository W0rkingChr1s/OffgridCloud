"""Update-check logic and endpoints."""

from __future__ import annotations

from app import __version__
from app.updater import check_for_update, clear_cache, is_newer, parse_version


def test_parse_version_handles_prefixes_and_junk():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("0.0.1") == (0, 0, 1)
    assert parse_version("v2.0") == (2, 0)
    assert parse_version("") == (0,)
    assert parse_version("nonsense") == (0,)
    assert parse_version("v1.4.0-rc1") == (1, 4, 0)


def test_is_newer_compares_semantically():
    assert is_newer("v0.2.0", "0.1.9") is True
    assert is_newer("v1.0.0", "0.9.9") is True
    assert is_newer("0.0.1", "0.0.1") is False
    assert is_newer("v0.0.1", "0.1.0") is False
    # Shorter vs longer tuples pad with zeros.
    assert is_newer("1.2", "1.2.0") is False
    assert is_newer("1.2.1", "1.2") is True


def test_check_for_update_flags_available():
    clear_cache()
    info = check_for_update(
        "0.0.1",
        "owner/repo",
        fetcher=lambda repo: {
            "tag_name": "v9.9.9",
            "html_url": "https://example/releases/9",
            "name": "Big one",
            "published_at": "2026-01-01T00:00:00Z",
            "body": "notes",
        },
        use_cache=False,
    )
    assert info.update_available is True
    assert info.latest == "v9.9.9"
    assert info.release_url.endswith("/9")


def test_check_for_update_same_version_not_available():
    clear_cache()
    info = check_for_update(
        "5.0.0",
        "owner/repo",
        fetcher=lambda repo: {"tag_name": "v5.0.0"},
        use_cache=False,
    )
    assert info.update_available is False


def test_check_for_update_is_offline_safe():
    clear_cache()

    def boom(repo):
        raise OSError("no network")

    info = check_for_update("0.0.1", "owner/repo", fetcher=boom, use_cache=False)
    assert info.update_available is False
    assert info.error
    assert info.latest is None


def test_updates_endpoint_returns_current_version(client, admin_auth, monkeypatch):
    clear_cache()
    # Avoid a real network call: inject an offline-safe fetcher result.
    import app.routers.updates as up
    from app.updater import UpdateInfo

    monkeypatch.setattr(
        up,
        "check_for_update",
        lambda current, repo, use_cache=True: UpdateInfo(current=current, error="offline"),
    )
    r = client.get("/api/updates", headers=admin_auth)
    assert r.status_code == 200
    body = r.json()
    assert body["current"] == __version__
    assert body["update_available"] is False
    assert body["self_update_enabled"] is False


def test_updates_apply_disabled_by_default(client, admin_auth):
    r = client.post("/api/updates/apply", headers=admin_auth)
    assert r.status_code == 409


def test_updates_requires_admin(client, admin_auth):
    client.post(
        "/api/users",
        headers=admin_auth,
        json={"email": "plain@test.local", "password": "userpass123"},
    )
    token = client.post(
        "/api/auth/login", json={"email": "plain@test.local", "password": "userpass123"}
    ).json()["access_token"]
    r = client.get("/api/updates", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
