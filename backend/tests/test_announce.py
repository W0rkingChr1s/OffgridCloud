"""Tests for the operational status announcements (startup / reconnect /
bandwidth gate) and the in-app notices ring."""

from __future__ import annotations

import pytest

from app import announce, notices


@pytest.fixture(autouse=True)
def _clean_state():
    """Each test starts with a fresh notices ring and transition state."""
    notices.reset()
    announce.reset_state()
    yield
    notices.reset()
    announce.reset_state()


# --- notices ring ---------------------------------------------------------


def test_notices_push_assigns_incrementing_ids():
    a = notices.push("info", "A")
    b = notices.push("success", "B", "body")
    assert a["id"] == 1 and b["id"] == 2
    recent = notices.recent()
    assert [n["title"] for n in recent] == ["A", "B"]
    assert recent[1]["message"] == "body"


def test_notices_ring_is_bounded():
    for i in range(notices._MAX_NOTICES + 5):
        notices.push("info", f"n{i}")
    recent = notices.recent()
    assert len(recent) == notices._MAX_NOTICES
    # Oldest dropped: the last id must be the total count pushed.
    assert recent[-1]["id"] == notices._MAX_NOTICES + 5


# --- connectivity transition (pure) ---------------------------------------


def test_note_online_baseline_never_reconnects():
    # First observation only seeds the baseline.
    assert announce.note_online(True) is None
    assert announce.note_online(True) is None


def test_note_online_reports_reconnect_edge():
    announce.note_online(True)  # baseline online
    assert announce.note_online(False) is None  # went offline: no message
    assert announce.note_online(True) == "reconnect"  # recovered
    assert announce.note_online(True) is None  # stays online: no repeat


# --- bandwidth-gate transitions -------------------------------------------


def test_bandwidth_gate_pause_then_resume(monkeypatch):
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        announce, "announce_bandwidth_paused", lambda db, reason: events.append(("paused", reason))
    )
    monkeypatch.setattr(
        announce, "announce_bandwidth_resumed", lambda db, kbps: events.append(("resumed", kbps))
    )

    # Gated while work waits -> paused (once).
    announce.note_bandwidth_gate(None, gated=True, reason="langsam", has_queued=True, last_kbps=10)
    announce.note_bandwidth_gate(None, gated=True, reason="langsam", has_queued=True, last_kbps=10)
    # Gate opens -> resumed (once).
    announce.note_bandwidth_gate(None, gated=False, reason="", has_queued=True, last_kbps=800)
    announce.note_bandwidth_gate(None, gated=False, reason="", has_queued=True, last_kbps=800)

    assert [e[0] for e in events] == ["paused", "resumed"]
    assert events[0][1] == "langsam"
    assert events[1][1] == 800


def test_bandwidth_gate_no_pause_without_queue(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(
        announce, "announce_bandwidth_paused", lambda db, reason: events.append("paused")
    )
    # Gated but nothing queued -> nothing to pause.
    announce.note_bandwidth_gate(None, gated=True, reason="x", has_queued=False, last_kbps=10)
    assert events == []


# --- startup report formatting (pure) -------------------------------------


def _report(**over) -> dict:
    base = {
        "started_at": "14.07.2026 15:30",
        "disk": {"free": 12 * 1024**3, "total": 32 * 1024**3, "percent_used": 62.5},
        "providers": {"total": 3, "connected": 2, "connected_names": ["S3", "Drive"]},
        "vpn_connected": True,
        "external_ip": "203.0.113.7",
        "internal_ip": "192.168.1.50",
        "queued": 4,
        "bandwidth_kbps": 850.0,
        "pool": {"total": 2, "online": 1},
    }
    base.update(over)
    return base


def test_format_startup_lines_covers_all_fields():
    text = "\n".join(announce.format_startup_lines(_report()))
    assert "Start: 14.07.2026 15:30" in text
    assert "frei" in text and "belegt 62%" in text
    assert "Cloud-Ziele: 2/3 verbunden (S3, Drive)" in text
    assert "VPN: verbunden" in text
    assert "Externe IP: 203.0.113.7" in text
    assert "Interne IP: 192.168.1.50" in text
    assert "Warteschlange: 4" in text
    assert "Bandbreite: 850 KB/s" in text
    assert "Pool: 1/2 Geräte verbunden" in text


def test_format_startup_lines_omits_pool_when_no_peers():
    text = "\n".join(announce.format_startup_lines(_report(pool={"total": 0, "online": 0})))
    assert "Pool:" not in text


def test_format_startup_lines_handles_missing_data():
    text = "\n".join(
        announce.format_startup_lines(
            _report(
                external_ip=None,
                internal_ip=None,
                vpn_connected=False,
                bandwidth_kbps=0.0,
                providers={"total": 0, "connected": 0, "connected_names": []},
            )
        )
    )
    assert "Externe IP: unbekannt" in text
    assert "VPN: getrennt" in text
    assert "Bandbreite: unbekannt" in text
    assert "Cloud-Ziele: keine konfiguriert" in text


def test_format_kbps_scales_to_mb():
    assert announce.format_kbps(0) == "unbekannt"
    assert announce.format_kbps(500) == "500 KB/s"
    assert announce.format_kbps(2048) == "2.0 MB/s"


def test_reconnect_message_includes_ips_and_bandwidth():
    msg = announce.format_reconnect_message(800, "203.0.113.7", "192.168.1.50")
    assert "wiederhergestellt" in msg
    assert "800 KB/s" in msg
    assert "203.0.113.7" in msg and "192.168.1.50" in msg


# --- integration: gather + gating -----------------------------------------


def _no_network(monkeypatch):
    """Keep the report offline-fast: no real IP probe or pool poll in tests."""
    monkeypatch.setattr(announce.netinfo, "external_ip", lambda *a, **k: "203.0.113.7")
    monkeypatch.setattr(announce.netinfo, "internal_ip", lambda *a, **k: "192.168.1.9")


def test_gather_startup_report_shape(client, monkeypatch):
    from app.db import SessionLocal

    _no_network(monkeypatch)
    with SessionLocal() as db:
        report = announce.gather_startup_report(db)
    assert report["external_ip"] == "203.0.113.7"
    assert report["internal_ip"] == "192.168.1.9"
    assert set(report["disk"]) >= {"free", "total", "percent_used"}
    assert report["providers"]["total"] == 0
    assert report["pool"] == {"total": 0, "online": 0}
    assert report["queued"] == 0


def test_announce_startup_pushes_notice_when_enabled(client, monkeypatch):
    _no_network(monkeypatch)
    dispatched: list[str] = []
    monkeypatch.setattr(
        announce.notify, "dispatch", lambda *a, **k: dispatched.append(a[1]) or True
    )
    announce.announce_startup()
    recent = notices.recent()
    assert recent and recent[-1]["title"] == "OffgridCloud gestartet"
    assert dispatched == ["server.startup"]


def test_announce_startup_silent_when_disabled(client, monkeypatch):
    from app.admin_ops import get_system_settings
    from app.db import SessionLocal

    _no_network(monkeypatch)
    with SessionLocal() as db:
        get_system_settings(db).notify_on_startup = False
        db.commit()
    dispatched: list[str] = []
    monkeypatch.setattr(
        announce.notify, "dispatch", lambda *a, **k: dispatched.append(a[1]) or True
    )
    announce.announce_startup()
    assert notices.recent() == []
    assert dispatched == []

    with SessionLocal() as db:  # restore for the shared test DB
        get_system_settings(db).notify_on_startup = True
        db.commit()
