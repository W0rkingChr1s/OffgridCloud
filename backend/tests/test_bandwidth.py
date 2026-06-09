from datetime import datetime, timedelta

from app.bandwidth import effective_bwlimit, should_start


def test_effective_bwlimit_base_when_no_window():
    assert effective_bwlimit([], 100, datetime(2026, 1, 1, 12, 0)) == 100


def test_effective_bwlimit_daytime_window():
    schedule = [{"start": "08:00", "end": "20:00", "kbps": 50}]
    assert effective_bwlimit(schedule, 0, datetime(2026, 1, 1, 12, 0)) == 50  # in window
    assert effective_bwlimit(schedule, 0, datetime(2026, 1, 1, 23, 0)) == 0  # outside -> base


def test_effective_bwlimit_overnight_window_wraps():
    schedule = [{"start": "22:00", "end": "06:00", "kbps": 0}]
    base = 80
    assert effective_bwlimit(schedule, base, datetime(2026, 1, 1, 23, 30)) == 0  # night, full
    assert effective_bwlimit(schedule, base, datetime(2026, 1, 1, 3, 0)) == 0  # still night
    assert effective_bwlimit(schedule, base, datetime(2026, 1, 1, 12, 0)) == base  # day


def test_gate_open_when_disabled_or_no_minimum():
    now = datetime(2026, 1, 1, 12, 0)
    assert should_start(False, 100, 10, now, now)[0] is True
    assert should_start(True, 0, 10, now, now)[0] is True


def test_gate_blocks_when_recent_measurement_below_minimum():
    now = datetime(2026, 1, 1, 12, 0)
    recent = now - timedelta(seconds=10)
    ok, reason = should_start(True, 100, 40.0, recent, now)
    assert ok is False
    assert "Minimum" in reason


def test_gate_opens_when_measurement_stale_for_remeasure():
    now = datetime(2026, 1, 1, 12, 0)
    stale = now - timedelta(seconds=600)
    assert should_start(True, 100, 40.0, stale, now)[0] is True


def test_gate_never_measured_allows_first_transfer():
    now = datetime(2026, 1, 1, 12, 0)
    assert should_start(True, 100, 0.0, None, now)[0] is True


def test_bandwidth_api_get_and_update(client, admin_auth):
    # Defaults: disabled, no limits.
    body = client.get("/api/bandwidth", headers=admin_auth).json()
    assert body["enabled"] is False
    assert body["effective_bwlimit_kbps"] == 0

    upd = client.put(
        "/api/bandwidth",
        headers=admin_auth,
        json={
            "enabled": True,
            "min_bandwidth_kbps": 50,
            "bwlimit_kbps": 200,
            "schedule": [{"start": "22:00", "end": "06:00", "kbps": 0}],
        },
    ).json()
    assert upd["enabled"] is True
    assert upd["bwlimit_kbps"] == 200
    assert len(upd["schedule"]) == 1


def test_bandwidth_requires_admin(client, admin_auth):
    client.post(
        "/api/users",
        headers=admin_auth,
        json={"email": "bw@test.local", "password": "userpass123"},
    )
    token = client.post(
        "/api/auth/login", json={"email": "bw@test.local", "password": "userpass123"}
    ).json()["access_token"]
    resp = client.get("/api/bandwidth", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
