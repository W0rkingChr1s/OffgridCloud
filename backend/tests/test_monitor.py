"""Health monitor: host metric parsers, rating, renderers and the /api/monitor API."""

from __future__ import annotations

import pytest

from app import announce, health, sysmetrics


@pytest.fixture(autouse=True)
def _clean_state():
    health.reset_state()
    announce.reset_state()
    yield
    health.reset_state()
    announce.reset_state()


@pytest.fixture
def offline_probes(monkeypatch):
    """No real network from the snapshot: fixed IPs and latency."""
    monkeypatch.setattr(health.netinfo, "external_ip", lambda *a, **k: "203.0.113.7")
    monkeypatch.setattr(health.netinfo, "internal_ip", lambda *a, **k: "192.168.1.9")
    monkeypatch.setattr(health, "measure_latency_ms", lambda *a, **k: 42.0)


# --- parsers --------------------------------------------------------------


def test_cpu_sampler_needs_a_baseline_then_reports_percent():
    sampler = sysmetrics.CpuSampler()
    first = "cpu  100 0 100 800 0 0 0 0 0 0\n"
    second = "cpu  150 0 150 900 0 0 0 0 0 0\n"  # +100 busy, +100 idle
    assert sampler.percent(first) is None
    assert sampler.percent(second) == 50.0


def test_parse_meminfo_and_memory_percent():
    text = (
        "MemTotal:        1000 kB\n"
        "MemFree:          100 kB\n"
        "MemAvailable:     250 kB\n"
        "SwapTotal:        500 kB\n"
        "SwapFree:         400 kB\n"
    )
    mem = sysmetrics.memory(text)
    assert mem["total"] == 1000 * 1024
    assert mem["percent_used"] == 75.0
    assert mem["swap_used"] == 100 * 1024


def test_cpu_temperature_prefers_cpu_zone(tmp_path):
    zones = tmp_path / "class" / "thermal"
    for name, kind, temp in (
        ("thermal_zone0", "acpitz", "30000"),
        ("thermal_zone1", "cpu-thermal", "61234"),
    ):
        (zones / name).mkdir(parents=True)
        (zones / name / "type").write_text(kind)
        (zones / name / "temp").write_text(temp)
    assert sysmetrics.cpu_temperature(tmp_path) == 61.2


def test_parse_throttled_splits_now_and_since_boot():
    state = sysmetrics.parse_throttled(0x50005)  # under-voltage now + since boot, throttled now
    assert state["now"]["under_voltage"] and state["now"]["throttled"]
    assert state["since_boot"]["under_voltage"] and not state["since_boot"]["soft_temp_limit"]


def test_throttling_from_sysfs(tmp_path):
    fw = tmp_path / "devices" / "platform" / "soc" / "soc:firmware"
    fw.mkdir(parents=True)
    (fw / "get_throttled").write_text("50000\n")
    state = sysmetrics.throttling(tmp_path)
    assert state["since_boot"]["under_voltage"] and not state["now"]["under_voltage"]


def test_throttling_absent_off_pi(tmp_path):
    assert sysmetrics.throttling(tmp_path) is None


def test_parse_wireless():
    text = (
        "Inter-| sta-|   Quality        |   Discarded packets\n"
        " face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22\n"
        "wlan0: 0000   49.  -61.  -256        0      0      0      0     12        0\n"
    )
    assert sysmetrics.parse_wireless(text) == [
        {"interface": "wlan0", "quality_percent": 70, "signal_dbm": -61}
    ]


# --- rating ---------------------------------------------------------------


def _snap(**over):
    snap = {
        "system": {"temperature_c": 50.0, "memory": {"percent_used": 40.0}, "throttling": None},
        "storage": {"buffer": {"percent_used": 30.0}},
        "network": {"online": True, "latency_ms": 40.0, "outages_24h": 0, "wifi": []},
        "rclone": {"available": True, "version": "rclone v1.70"},
        "providers": {"total": 1, "ok": 1, "error": 0},
        "transfers": {"queued": 0, "running": 0, "failed": 0},
    }
    for key, value in over.items():
        section, _, field = key.partition("__")
        snap[section][field] = value
    return snap


def _status(checks, check_id):
    return next(c["status"] for c in checks if c["id"] == check_id)


def test_all_good_is_ok():
    checks = health.build_checks(_snap())
    assert health.overall(checks) == "ok"


def test_hot_cpu_is_warning_then_error():
    assert (
        _status(health.build_checks(_snap(system__temperature_c=72.0)), "temperature") == "warning"
    )
    assert _status(health.build_checks(_snap(system__temperature_c=81.0)), "temperature") == "error"


def test_under_voltage_now_is_error_and_past_is_warning():
    now = sysmetrics.parse_throttled(0x1)
    past = sysmetrics.parse_throttled(0x10000)
    assert _status(health.build_checks(_snap(system__throttling=now)), "power") == "error"
    assert _status(health.build_checks(_snap(system__throttling=past)), "power") == "warning"


def test_offline_is_error_and_frequent_outages_warn():
    checks = health.build_checks(_snap(network__online=False))
    assert _status(checks, "internet") == "error"
    assert health.overall(checks) == "error"
    assert _status(health.build_checks(_snap(network__outages_24h=3)), "internet") == "warning"


def test_weak_wifi_warns():
    wifi = [{"interface": "wlan0", "signal_dbm": -80, "quality_percent": 30}]
    assert _status(health.build_checks(_snap(network__wifi=wifi)), "wifi_wlan0") == "warning"


def test_failed_transfers_warn():
    transfers = {"queued": 2, "running": 0, "failed": 1}
    snap = _snap()
    snap["transfers"] = transfers
    assert _status(health.build_checks(snap), "transfers") == "warning"


# --- connectivity stats ---------------------------------------------------


def test_connectivity_state_counts_outages():
    announce.note_online(True, now=0)
    for i in range(announce.OFFLINE_THRESHOLD):
        announce.note_online(False, now=20 * (i + 1))
    state = announce.connectivity_state()
    assert state["online"] is False
    announce.note_online(True, now=200)
    state = announce.connectivity_state()
    assert state["online"] is True
    assert state["outages_24h"] == 1
    assert state["last_outage"]["seconds"] == 180


# --- API ------------------------------------------------------------------


def test_monitor_requires_auth(client):
    assert client.get("/api/monitor").status_code == 401
    assert client.get("/api/monitor/metrics").status_code == 401
    assert client.get("/api/monitor?token=ogcm_nope").status_code == 401


def test_monitor_snapshot_for_admin(client, admin_auth, offline_probes):
    resp = client.get("/api/monitor", headers=admin_auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] in {"ok", "warning", "error"}
    assert {"system", "storage", "network", "checks", "transfers"} <= body.keys()
    assert body["storage"]["buffer"]["total"] > 0


def test_monitor_forbidden_for_plain_user(client, admin_auth):
    client.post(
        "/api/users",
        headers=admin_auth,
        json={"email": "mon@test.local", "password": "userpass123", "role": "user"},
    )
    token = client.post(
        "/api/auth/login", json={"email": "mon@test.local", "password": "userpass123"}
    ).json()["access_token"]
    assert (
        client.get("/api/monitor", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    )


def test_monitor_token_lifecycle(client, admin_auth, offline_probes):
    created = client.post("/api/monitor/tokens", headers=admin_auth, json={"name": "Grafana"})
    assert created.status_code == 201, created.text
    raw = created.json()["token"]
    assert raw.startswith("ogcm_")

    bearer = {"Authorization": f"Bearer {raw}"}
    metrics = client.get("/api/monitor/metrics", headers=bearer)
    assert metrics.status_code == 200
    assert metrics.headers["content-type"].startswith("text/plain")
    assert "offgridcloud_health_status" in metrics.text
    assert "offgridcloud_disk_free_bytes" in metrics.text

    prtg = client.get(f"/api/monitor?format=prtg&token={raw}")
    assert prtg.status_code == 200
    channels = [c["channel"] for c in prtg.json()["prtg"]["result"]]
    assert channels[0] == "Gesamtzustand" and "Speicher belegt" in channels

    listing = client.get("/api/monitor/tokens", headers=admin_auth).json()
    assert listing[0]["name"] == "Grafana" and "token" not in listing[0]
    assert listing[0]["last_used_at"] is not None

    # A monitoring token can read, but never manage tokens.
    assert client.get("/api/monitor/tokens", headers=bearer).status_code == 401

    assert (
        client.delete(f"/api/monitor/tokens/{listing[0]['id']}", headers=admin_auth).status_code
        == 204
    )
    assert client.get("/api/monitor/metrics", headers=bearer).status_code == 401


def test_history_records_points(client, admin_auth, offline_probes):
    from app.db import SessionLocal

    with SessionLocal() as db:
        health.record_sample(db)
        health.record_sample(db)
    body = client.get("/api/monitor/history", headers=admin_auth).json()
    assert body["interval_seconds"] == 60
    assert len(body["points"]) == 2
    assert body["points"][1]["latency_ms"] == 42.0


def test_history_step_merges_buckets(client, admin_auth, monkeypatch):
    now = 1_800_000_000 - 1_800_000_000 % 900
    monkeypatch.setattr("app.routers.monitor.time.time", lambda: now + 1800)
    base = {"memory_percent": None, "temperature_c": 50.0, "disk_percent": 10.0}
    with health._history_lock:
        for i, (cpu, online) in enumerate([(10.0, 1), (30.0, 0), (20.0, 1)]):
            point = {"t": now + i * 60, "cpu_percent": cpu, "online": online, **base}
            health._history.append(point)
        health._history.append({"t": now + 900, "cpu_percent": 5.0, "online": 1, **base})

    body = client.get("/api/monitor/history?step=900", headers=admin_auth).json()
    assert body["interval_seconds"] == 900
    first, second = body["points"]
    assert first == {
        "t": now,
        "cpu_percent": 20.0,
        "online": 0,  # the outage in the bucket wins
        "memory_percent": None,
        "temperature_c": 50.0,
        "disk_percent": 10.0,
    }
    assert second["t"] == now + 900 and second["cpu_percent"] == 5.0


def test_history_hours_and_step_bounds(client, admin_auth, monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr("app.routers.monitor.time.time", lambda: now)
    with health._history_lock:
        health._history.append({"t": now - 3 * 3600, "cpu_percent": 1.0})
        health._history.append({"t": now - 60, "cpu_percent": 2.0})

    body = client.get("/api/monitor/history?hours=1", headers=admin_auth).json()
    assert [p["cpu_percent"] for p in body["points"]] == [2.0]
    for bad in ("step=30", "step=7200", "hours=0", "hours=25"):
        assert client.get(f"/api/monitor/history?{bad}", headers=admin_auth).status_code == 422


def test_history_full_day_in_15_minute_steps_fits_one_ble_message(client, admin_auth):
    import json

    from app.db import SessionLocal

    with SessionLocal() as db:
        point = health.record_sample(db)
    with health._history_lock:
        health._history.clear()
        for i in range(health.HISTORY_POINTS):
            health._history.append({**point, "t": point["t"] - i * 60})

    body = client.get("/api/monitor/history?step=900", headers=admin_auth).json()
    assert len(body["points"]) <= 97
    assert len(json.dumps(body)) < 65_535 // 2


def test_prometheus_escapes_labels():
    p = health._Prom()
    p.add("info", "x", 1, {"hostname": 'a"b'})
    assert 'hostname="a\\"b"' in p.text()
