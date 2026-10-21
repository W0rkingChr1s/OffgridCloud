"""Update-check logic and endpoints."""

from __future__ import annotations

import time
from pathlib import Path

import app as app_pkg
from app import __version__, _read_channel, _read_commit, _read_version
from app.updater import (
    PHASE_FAILED,
    PHASE_RUNNING,
    PHASE_SUCCESS,
    PHASE_UNKNOWN,
    UpdateState,
    check_for_update,
    clear_cache,
    is_newer,
    parse_version,
    read_log_tail,
    read_state,
    resolve_pending,
    start_update,
    write_state,
)


def test_read_version_prefers_stamped_file_then_falls_back():
    # The installer writes a VERSION file (from the git tag); the app reads it.
    vf = Path(app_pkg.__file__).with_name("VERSION")
    existed = vf.exists()
    backup = vf.read_text() if existed else None
    try:
        vf.write_text("2.3.4\n")
        assert _read_version() == "2.3.4"
        vf.unlink()
        assert _read_version() == "0.1.0"  # built-in fallback for dev checkouts
    finally:
        if existed:
            vf.write_text(backup)


def _with_stamps(stamps: dict[str, str | None], fn):
    """Run ``fn`` with the given stamp files (None = absent), restoring afterwards."""
    pkg = Path(app_pkg.__file__).parent
    saved = {n: (pkg / n).read_text() if (pkg / n).exists() else None for n in stamps}
    try:
        for name, value in stamps.items():
            if value is None:
                (pkg / name).unlink(missing_ok=True)
            else:
                (pkg / name).write_text(value + "\n")
        return fn()
    finally:
        for name, value in saved.items():
            if value is None:
                (pkg / name).unlink(missing_ok=True)
            else:
                (pkg / name).write_text(value)


def test_channel_and_commit_from_stamps():
    assert _with_stamps(
        {"VERSION": "0.4.10", "CHANNEL": "main", "COMMIT": "abc1234def"},
        lambda: (_read_channel(), _read_commit()),
    ) == ("main", "abc1234def")


def test_channel_inferred_from_describe_version_without_stamps():
    # Installs from before the CHANNEL stamp: a describe version past a tag is main.
    assert _with_stamps(
        {"VERSION": "0.4.10-44-g97b8eb0", "CHANNEL": None, "COMMIT": None},
        lambda: (_read_channel(), _read_commit()),
    ) == ("main", "97b8eb0")
    assert _with_stamps(
        {"VERSION": "0.4.10", "CHANNEL": None, "COMMIT": None},
        lambda: (_read_channel(), _read_commit()),
    ) == ("release", "")


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


def _compare(behind: int) -> dict:
    commits = [
        {
            "sha": f"c{i}".ljust(40, "0"),
            "commit": {"message": f"change {i}\n\nbody", "committer": {"date": "2026-10-19"}},
        }
        for i in range(1, behind + 1)
    ]
    return {"ahead_by": behind, "commits": commits, "html_url": "https://example/compare"}


def test_check_for_update_main_channel_flags_new_commits():
    clear_cache()
    seen = {}

    def compare(repo, base, head):
        seen.update(repo=repo, base=base, head=head)
        return _compare(3)

    info = check_for_update(
        "0.4.10-44-g97b8eb0",
        "owner/repo",
        channel="main",
        commit="97b8eb0",
        fetcher=lambda repo: {"tag_name": "v0.4.10"},  # must not be consulted
        compare_fetcher=compare,
        use_cache=False,
    )
    assert seen == {"repo": "owner/repo", "base": "97b8eb0", "head": "main"}
    assert info.update_available is True
    assert info.behind_by == 3
    assert info.latest == "main @ c300000"
    assert info.notes.splitlines()[0] == "* change 3"  # newest first, subject only
    assert info.release_url == "https://example/compare"


def test_check_for_update_main_channel_up_to_date_ignores_releases():
    # A main install ahead of the last release is current once it matches main.
    clear_cache()
    info = check_for_update(
        "0.4.10-44-g97b8eb0",
        "owner/repo",
        channel="main",
        commit="97b8eb0",
        fetcher=lambda repo: {"tag_name": "v9.9.9"},
        compare_fetcher=lambda repo, base, head: _compare(0),
        use_cache=False,
    )
    assert info.update_available is False
    assert info.behind_by == 0
    assert not info.error


def test_check_for_update_main_channel_without_commit_or_network():
    clear_cache()
    info = check_for_update("0.1.0", "owner/repo", channel="main", use_cache=False)
    assert info.update_available is False and info.error

    def boom(repo, base, head):
        raise OSError("no network")

    info = check_for_update(
        "0.1.0", "owner/repo", channel="main", commit="abc1234", compare_fetcher=boom,
        use_cache=False,
    )
    assert info.update_available is False and info.error


def test_updates_endpoint_returns_current_version(client, admin_auth, monkeypatch):
    clear_cache()
    # Avoid a real network call: inject an offline-safe fetcher result.
    import app.routers.updates as up
    from app.updater import UpdateInfo

    monkeypatch.setattr(
        up,
        "check_for_update",
        lambda current, repo, use_cache=True, **kw: UpdateInfo(current=current, error="offline"),
    )
    r = client.get("/api/updates", headers=admin_auth)
    assert r.status_code == 200
    body = r.json()
    assert body["current"] == __version__
    assert body["update_available"] is False
    # One-click self-update is enabled by default now.
    assert body["self_update_enabled"] is True


def test_updates_reports_docker(client, admin_auth, monkeypatch):
    # In a container the UI must point at pulling a new image, not update.sh.
    import app.routers.updates as up
    from app.updater import UpdateInfo

    monkeypatch.setattr(
        up,
        "check_for_update",
        lambda current, repo, use_cache=True, **kw: UpdateInfo(current=current),
    )
    monkeypatch.setenv("OGC_IN_DOCKER", "1")
    assert client.get("/api/updates", headers=admin_auth).json()["in_docker"] is True
    monkeypatch.setenv("OGC_IN_DOCKER", "0")
    assert client.get("/api/updates", headers=admin_auth).json()["in_docker"] is False


def test_updates_apply_enabled_by_default(client, admin_auth, monkeypatch):
    # Apply is on out of the box; stub the launcher so nothing actually spawns.
    import app.routers.updates as up

    started = {}
    monkeypatch.setattr(
        up,
        "start_update",
        lambda data_dir, command, version, current_version=None: started.update(command=command)
        or UpdateState(phase=PHASE_RUNNING),
    )
    r = client.post("/api/updates/apply", headers=admin_auth)
    assert r.status_code == 200
    assert r.json()["started"] is True
    assert "update.sh" in started["command"]


def test_updates_apply_disabled_when_opted_out(client, admin_auth, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "self_update", False)
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


# --- Observable self-update runner ----------------------------------------


def test_state_roundtrip_and_log_tail(tmp_path):
    write_state(tmp_path, UpdateState(phase=PHASE_RUNNING, from_version="1.0.0"))
    state = read_state(tmp_path)
    assert state.phase == PHASE_RUNNING
    assert state.from_version == "1.0.0"
    (tmp_path / "update.log").write_text("line1\nline2\n")
    assert "line2" in read_log_tail(tmp_path)


def test_log_tail_strips_ansi_colours(tmp_path):
    (tmp_path / "update.log").write_bytes(
        b"\n\x1b[1;36m>> Copying files to /opt/offgridcloud...\x1b[0m\n"
        b"   \x1b[1;32mUpdated to 0.8.1 and healthy.\x1b[0m\n"
    )
    tail = read_log_tail(tmp_path)
    assert "\x1b" not in tail
    assert ">> Copying files to /opt/offgridcloud..." in tail
    assert "Updated to 0.8.1 and healthy." in tail


def test_read_state_defaults_to_idle_when_missing(tmp_path):
    assert read_state(tmp_path).phase == "idle"


def test_start_update_records_success_via_monitor(tmp_path):
    state = start_update(
        tmp_path,
        "sh -c 'echo rebuilding; exit 0'",
        "1.0.0",
        current_version=lambda: "2.0.0",
    )
    assert state.phase == PHASE_RUNNING
    # The monitor thread settles the outcome once the command exits.
    for _ in range(50):
        s = read_state(tmp_path)
        if s.phase != PHASE_RUNNING:
            break
        time.sleep(0.05)
    s = read_state(tmp_path)
    assert s.phase == PHASE_SUCCESS
    assert s.returncode == 0
    assert s.to_version == "2.0.0"
    assert "rebuilding" in read_log_tail(tmp_path)


def test_start_update_records_failure_via_monitor(tmp_path):
    start_update(
        tmp_path, "sh -c 'echo boom >&2; exit 3'", "1.0.0", current_version=lambda: "1.0.0"
    )
    for _ in range(50):
        if read_state(tmp_path).phase != PHASE_RUNNING:
            break
        time.sleep(0.05)
    s = read_state(tmp_path)
    assert s.phase == PHASE_FAILED
    assert s.returncode == 3


def test_start_update_rejects_concurrent_run(tmp_path):
    write_state(tmp_path, UpdateState(phase=PHASE_RUNNING, from_version="1.0.0"))
    try:
        start_update(tmp_path, "sh -c 'true'", "1.0.0")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "läuft bereits" in str(exc)


def test_start_update_bad_command_marks_failed(tmp_path):
    try:
        start_update(tmp_path, "/no/such/binary-xyz", "1.0.0")
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass
    assert read_state(tmp_path).phase == PHASE_FAILED


def test_resolve_pending_success_on_version_bump(tmp_path):
    write_state(tmp_path, UpdateState(phase=PHASE_RUNNING, from_version="1.0.0", started_at=100.0))
    s = resolve_pending(tmp_path, "1.1.0", now=lambda: 110.0)
    assert s.phase == PHASE_SUCCESS
    assert s.to_version == "1.1.0"


def test_resolve_pending_uses_log_sentinels(tmp_path):
    write_state(tmp_path, UpdateState(phase=PHASE_RUNNING, from_version="1.0.0", started_at=100.0))
    (tmp_path / "update.log").write_text("...\nUpdated to 1.0.0 and healthy.\n")
    assert resolve_pending(tmp_path, "1.0.0", now=lambda: 110.0).phase == PHASE_SUCCESS

    write_state(tmp_path, UpdateState(phase=PHASE_RUNNING, from_version="1.0.0", started_at=100.0))
    (tmp_path / "update.log").write_text("Service did not answer — check journal\n")
    assert resolve_pending(tmp_path, "1.0.0", now=lambda: 110.0).phase == PHASE_FAILED


def test_resolve_pending_same_version_is_unknown(tmp_path):
    write_state(tmp_path, UpdateState(phase=PHASE_RUNNING, from_version="1.0.0", started_at=100.0))
    s = resolve_pending(tmp_path, "1.0.0", now=lambda: 110.0)
    assert s.phase == PHASE_UNKNOWN


def test_resolve_pending_restart_step_is_success(tmp_path):
    # Reaching the restart step + being back up = success, even at same version
    # (the previous bug reported this as "failed" via the SIGTERM exit code).
    write_state(tmp_path, UpdateState(phase=PHASE_RUNNING, from_version="0.3.4", started_at=100.0))
    (tmp_path / "update.log").write_text(">> Restarting the service...\n")
    s = resolve_pending(tmp_path, "0.3.4", now=lambda: 110.0)
    assert s.phase == PHASE_SUCCESS


def test_resolve_pending_fail_wins_over_restart(tmp_path):
    write_state(tmp_path, UpdateState(phase=PHASE_RUNNING, from_version="1.0.0", started_at=100.0))
    (tmp_path / "update.log").write_text(">> Restarting the service...\nService did not answer\n")
    assert resolve_pending(tmp_path, "1.0.0", now=lambda: 110.0).phase == PHASE_FAILED


def test_start_update_signal_kill_is_not_a_failure(tmp_path):
    # A command killed by a signal (returncode < 0) is what the restart does to
    # update.sh on success. The monitor must NOT flip it to failed — it stays
    # running so resolve_pending() decides after the service comes back.
    start_update(tmp_path, "sh -c 'kill -TERM $$'", "1.0.0", current_version=lambda: "1.0.0")
    time.sleep(0.5)  # let the monitor thread observe the signal death
    s = read_state(tmp_path)
    assert s.phase == PHASE_RUNNING
    assert s.returncode is None


def test_resolve_pending_leaves_settled_state(tmp_path):
    write_state(
        tmp_path, UpdateState(phase=PHASE_SUCCESS, from_version="1.0.0", to_version="1.1.0")
    )
    assert resolve_pending(tmp_path, "2.0.0", now=lambda: 1.0).phase == PHASE_SUCCESS


def test_progress_endpoint_reports_state(client, admin_auth):
    from app.config import get_settings

    data_dir = get_settings().data_dir
    write_state(data_dir, UpdateState(phase=PHASE_RUNNING, from_version="9.0.0", message="läuft"))
    (Path(data_dir) / "update.log").write_text("hello from update\n")
    r = client.get("/api/updates/progress", headers=admin_auth)
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "running"
    assert body["running"] is True
    assert body["from_version"] == "9.0.0"
    assert "hello from update" in body["log"]
    # Reset so it doesn't leak into other tests sharing the data dir.
    write_state(data_dir, UpdateState())


def test_dismiss_clears_finished_result(client, admin_auth):
    from app.config import get_settings

    data_dir = get_settings().data_dir
    write_state(data_dir, UpdateState(phase=PHASE_FAILED, message="stuck", from_version="0.3.5"))
    (Path(data_dir) / "update.log").write_text("old transcript\n")
    r = client.post("/api/updates/dismiss", headers=admin_auth)
    assert r.status_code == 200
    assert r.json()["phase"] == "idle"
    # Progress now reports idle with no leftover log.
    body = client.get("/api/updates/progress", headers=admin_auth).json()
    assert body["phase"] == "idle"
    assert body["log"] == ""
    # …but the transcript is kept for later diagnosis.
    assert (Path(data_dir) / "update.log.1").read_text() == "old transcript\n"


def test_dismiss_refuses_while_running(client, admin_auth):
    from app.config import get_settings

    data_dir = get_settings().data_dir
    write_state(data_dir, UpdateState(phase=PHASE_RUNNING, from_version="0.3.5"))
    r = client.post("/api/updates/dismiss", headers=admin_auth)
    assert r.status_code == 409
    # Reset so it doesn't leak into other tests sharing the data dir.
    write_state(data_dir, UpdateState())


def test_progress_endpoint_requires_admin(client, admin_auth):
    client.post(
        "/api/users",
        headers=admin_auth,
        json={"email": "plain2@test.local", "password": "userpass123"},
    )
    token = client.post(
        "/api/auth/login", json={"email": "plain2@test.local", "password": "userpass123"}
    ).json()["access_token"]
    r = client.get("/api/updates/progress", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_resolve_pending_flags_optional_feature_warnings(tmp_path):
    write_state(tmp_path, UpdateState(phase=PHASE_RUNNING, from_version="1.0.0", started_at=100.0))
    (tmp_path / "update.log").write_text(
        "   Bluetooth setup reported an issue — retried on the next update\n"
        ">> Restarting the service...\n"
    )
    s = resolve_pending(tmp_path, "1.1.0", now=lambda: 110.0)
    assert s.phase == PHASE_SUCCESS
    assert s.message == "Aktualisiert auf 1.1.0. Mit Warnungen, siehe Protokoll."


def test_start_update_keeps_previous_log(tmp_path):
    (tmp_path / "update.log").write_text("previous run\n")
    start_update(tmp_path, "sh -c 'echo again; exit 0'", "1.0.0", current_version=lambda: "1.0.0")
    for _ in range(50):
        if read_state(tmp_path).phase != PHASE_RUNNING:
            break
        time.sleep(0.05)
    assert (tmp_path / "update.log.1").read_text() == "previous run\n"
    assert "again" in read_log_tail(tmp_path)
