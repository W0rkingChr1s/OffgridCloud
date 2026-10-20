"""USB tethering (iPhone/Android) as uplink and the metered-upload rules."""

from datetime import date

import pytest

from app import announce, bandwidth, network, transfers
from app.db import SessionLocal

ETH = {"device": "eth0", "type": "ethernet", "state": "unavailable", "connection": ""}


def _dev(name, type_="ethernet", conn="Wired connection 1"):
    return {"device": name, "type": type_, "state": "connected", "connection": conn}


# --------------------------------------------------------------------------- #
# Detection (pure)                                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("driver", "metered", "kind"),
    [
        ("ipheth", False, "iphone"),
        ("rndis_host", False, "android"),
        ("cdc_ncm", False, "android"),
        ("cdc_ether", True, "android"),
        ("cdc_ether", False, None),  # a USB-Ethernet dongle, not a phone
        ("r8152", False, None),
        ("", True, None),
    ],
)
def test_tether_kind(driver, metered, kind):
    assert network.tether_kind(driver, metered=metered) == kind


def test_parse_device_details():
    raw = (
        "GENERAL.DRIVER:ipheth\n"
        "GENERAL.METERED:yes (guessed)\n"
        "IP4.ADDRESS[1]:172.20.10.2/28\n"
        "IP4.ADDRESS[2]:10.0.0.9/24\n"
    )
    assert network.parse_device_details(raw) == {
        "driver": "ipheth",
        "metered": "yes",
        "ip": "172.20.10.2",
    }
    assert network.parse_device_details("GENERAL.METERED:no (guessed)\n")["metered"] == "no"


def test_build_status_iphone_tether_is_metered_uplink():
    status = network.build_status(
        devices=[_dev("eth1"), {**ETH}],
        connectivity="full",
        device_info={"eth1": {"driver": "ipheth", "metered": "no", "ip": "172.20.10.2"}},
        uplink_ip="172.20.10.2",
    )
    assert status.mode == "tether"
    assert status.tether == "iphone"
    assert status.tether_ip == "172.20.10.2"
    assert status.ethernet is False
    assert status.metered is True
    assert status.online is True
    assert network.describe_uplink(status) == "USB-Tethering (iPhone)"


def test_build_status_wifi_beats_plugged_in_phone():
    # Phone is plugged in, but the default route goes out through Wi-Fi.
    status = network.build_status(
        devices=[_dev("usb0"), _dev("wlan0", "wifi", "HomeWifi")],
        connectivity="full",
        device_info={
            "usb0": {"driver": "rndis_host", "metered": "yes", "ip": "192.168.42.10"},
            "wlan0": {"driver": "brcmfmac", "metered": "no", "ip": "192.168.1.50"},
        },
        uplink_ip="192.168.1.50",
    )
    assert status.mode == "client"
    assert status.tether == "android"
    assert status.metered is False
    assert status.wifi_ip == "192.168.1.50"


def test_build_status_without_route_info_prefers_cable_then_wifi_then_phone():
    info = {
        "usb0": {"driver": "cdc_ncm", "metered": "yes", "ip": "192.168.42.10"},
        "wlan0": {"driver": "brcmfmac", "metered": "no", "ip": "192.168.1.50"},
    }
    devices = [_dev("usb0"), _dev("wlan0", "wifi", "HomeWifi")]
    assert network.build_status(devices=devices, connectivity="full", device_info=info).mode == (
        "client"
    )
    only_phone = network.build_status(devices=devices[:1], connectivity="none", device_info=info)
    assert only_phone.mode == "tether"
    assert only_phone.metered is True


def test_build_status_metered_wifi_hotspot():
    # Phone hotspot over Wi-Fi: Android flags it via DHCP, NM reports metered.
    status = network.build_status(
        devices=[_dev("wlan0", "wifi", "Pixel")],
        connectivity="full",
        device_info={"wlan0": {"driver": "brcmfmac", "metered": "yes", "ip": "10.1.1.5"}},
        uplink_ip="10.1.1.5",
    )
    assert status.mode == "client"
    assert status.metered is True


def test_metered_uplink_unsupported_without_nmcli(monkeypatch):
    network.reset_metered_cache()
    monkeypatch.setattr(network, "nmcli_available", lambda: False)
    try:
        assert network.metered_uplink() == (False, "Unbekannt")
    finally:
        network.reset_metered_cache()


# --------------------------------------------------------------------------- #
# Metered gate (pure)                                                          #
# --------------------------------------------------------------------------- #
def test_metered_gate_rules():
    mb = 1024 * 1024
    assert bandwidth.metered_gate("pause", 500, 0, False, "x") == (True, "", None)
    assert bandwidth.metered_gate("allow", 500, 0, True, "x") == (True, "", None)
    ok, reason, _ = bandwidth.metered_gate("pause", 500, 0, True, "USB-Tethering (iPhone)")
    assert not ok and "iPhone" in reason
    assert bandwidth.metered_gate("limit", 500, 100 * mb, True, "x") == (True, "", 400 * mb)
    ok, reason, _ = bandwidth.metered_gate("limit", 500, 500 * mb, True, "x")
    assert not ok and "500 MB" in reason


# --------------------------------------------------------------------------- #
# Worker + API                                                                 #
# --------------------------------------------------------------------------- #
@pytest.fixture
def metered_policy():
    """Set the metered rule for one test and restore the shared DB afterwards.

    The min-bandwidth gate is switched off meanwhile, so leftovers from other
    tests can't be what blocks the worker.
    """
    with SessionLocal() as db:
        was_enabled = bandwidth.get_policy(db).enabled

    def set_policy(mode, budget_mb=500, used=0, enabled=False):
        with SessionLocal() as db:
            policy = bandwidth.get_policy(db)
            policy.enabled = enabled
            policy.metered_mode = mode
            policy.metered_budget_mb = budget_mb
            policy.metered_used_bytes = used
            policy.metered_day = date.today().isoformat()
            db.commit()

    yield set_policy
    set_policy("allow", 500, 0, enabled=was_enabled)
    announce.reset_state()


def _queue_job(client, admin_auth, data: bytes):
    from tests.test_transfers import _folder, _provider, _upload

    prov = _provider(client, admin_auth, name="Tether")
    folder = _folder(client, admin_auth, name="Tether")
    client.post(
        f"/api/folders/{folder['id']}/providers",
        headers=admin_auth,
        json={"provider_id": prov["id"]},
    )
    return _upload(client, admin_auth, folder["id"], "clip.mp4", data)


def test_worker_pauses_on_metered_uplink(client, admin_auth, monkeypatch, metered_policy):
    _queue_job(client, admin_auth, b"x" * 64)
    metered_policy("pause")
    monkeypatch.setattr(network, "metered_uplink", lambda: (True, "USB-Tethering (iPhone)"))
    ran = []
    monkeypatch.setattr(transfers, "process_job", lambda *a, **kw: ran.append(kw))
    assert transfers.process_one() is False
    assert ran == []

    # Same rule, but the box is back on Wi-Fi: uploads run unmetered.
    monkeypatch.setattr(network, "metered_uplink", lambda: (False, "WLAN Home"))
    assert transfers.process_one() is True
    assert [kw["metered"] for kw in ran] == [False]


def test_worker_limit_counts_bytes_and_stops_at_budget(
    client, admin_auth, monkeypatch, metered_policy
):
    media = _queue_job(client, admin_auth, b"y" * 2048)
    metered_policy("limit", budget_mb=1)
    monkeypatch.setattr(network, "metered_uplink", lambda: (True, "USB-Tethering (Android)"))

    def upload(local, options, dest, bwlimit=0, on_progress=None):
        from app.rclone import UploadResult

        return UploadResult(True, 2048, "", 100.0)

    with SessionLocal() as db:
        from sqlalchemy import select

        from app.models import TransferJob

        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        transfers.process_job(db, job, upload, metered=True)
        assert bandwidth.get_policy(db).metered_used_bytes == 2048

    # Budget used up: the worker gates instead of starting anything.
    metered_policy("limit", budget_mb=1, used=1024 * 1024)
    ran = []
    monkeypatch.setattr(transfers, "process_job", lambda *a, **kw: ran.append(kw))
    assert transfers.process_one() is False
    assert ran == []


def test_worker_limit_skips_files_bigger_than_remaining(
    client, admin_auth, monkeypatch, metered_policy
):
    from datetime import timedelta

    from sqlalchemy import select

    from app.models import TransferJob, TransferStatus

    media = _queue_job(client, admin_auth, b"z" * 4096)
    # The DB is shared across tests: park every other waiting job for a moment.
    with SessionLocal() as db:
        others = db.scalars(
            select(TransferJob).where(
                TransferJob.status == TransferStatus.QUEUED,
                TransferJob.media_id != media["id"],
            )
        ).all()
        parked = {j.id: j.next_attempt_at for j in others}
        for j in others:
            j.next_attempt_at = transfers._now() + timedelta(days=1)
        db.commit()
    try:
        # Only 100 bytes left today: the waiting 4 KB file doesn't fit.
        metered_policy("limit", budget_mb=1, used=1024 * 1024 - 100)
        monkeypatch.setattr(network, "metered_uplink", lambda: (True, "USB-Tethering (Android)"))
        ran = []
        monkeypatch.setattr(transfers, "process_job", lambda *a, **kw: ran.append(kw))
        assert transfers.process_one() is False
        assert ran == []

        # 8 KB left: now it fits and is sent as metered.
        metered_policy("limit", budget_mb=1, used=1024 * 1024 - 8192)
        assert transfers.process_one() is True
        assert [kw["metered"] for kw in ran] == [True]
    finally:
        with SessionLocal() as db:
            for job_id, when in parked.items():
                db.get(TransferJob, job_id).next_attempt_at = when
            db.commit()


def test_bandwidth_api_metered_settings(client, admin_auth, monkeypatch, metered_policy):
    monkeypatch.setattr(network, "metered_uplink", lambda: (True, "USB-Tethering (iPhone)"))
    from app.routers import bandwidth as bw_router

    monkeypatch.setattr(bw_router, "metered_uplink", lambda: (True, "USB-Tethering (iPhone)"))
    body = client.put(
        "/api/bandwidth",
        headers=admin_auth,
        json={"metered_mode": "pause", "metered_budget_mb": 250},
    ).json()
    assert body["metered_mode"] == "pause"
    assert body["metered_budget_mb"] == 250
    assert body["metered"] is True
    assert body["uplink"] == "USB-Tethering (iPhone)"
    assert body["gated"] is True
    assert "pausiert" in body["gate_reason"]

    bad = client.put("/api/bandwidth", headers=admin_auth, json={"metered_mode": "sometimes"})
    assert bad.status_code == 422
