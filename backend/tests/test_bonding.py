"""Link bundling via net-merge: when uploads go through the proxy, and how."""

import socket

import pytest

from app import bonding, network, rclone, transfers
from app.db import SessionLocal

PROXY = "socks5://127.0.0.1:1080"


def _public(host, *_a, **_kw):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


def _private(host, *_a, **_kw):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.178.5", 0))]


def _unresolvable(host, *_a, **_kw):
    raise socket.gaierror("no such host")


def _decide(options, **kw):
    args = {
        "enabled": True,
        "options": options,
        "metered_mode": "allow",
        "metered_present": False,
        "openvpn_active": False,
        "proxy": PROXY,
        "alive": lambda _url: True,
        "resolver": _public,
    }
    args.update(kw)
    return bonding.decide(**args)


# --------------------------------------------------------------------------- #
# Decision (pure)                                                              #
# --------------------------------------------------------------------------- #
def test_bundles_public_s3_target():
    assert _decide({"type": "s3", "provider": "AWS"}).proxy == PROXY


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"enabled": False}, "aus"),
        ({"proxy": ""}, "nicht eingerichtet"),
        ({"alive": lambda _url: False}, "antwortet nicht"),
        ({"openvpn_active": True}, "OpenVPN"),
        ({"metered_present": True, "metered_mode": "limit"}, "getaktet"),
        ({"metered_present": True, "metered_mode": "pause"}, "getaktet"),
        ({"resolver": _private}, "internes Ziel"),
        ({"resolver": _unresolvable}, "internes Ziel"),
    ],
)
def test_goes_direct_when_unsafe(kw, reason):
    decision = _decide({"type": "webdav", "url": "https://cloud.example.com/dav"}, **kw)
    assert decision.proxy == ""
    assert reason in decision.reason


def test_phone_is_bundled_when_metered_rule_allows_it():
    assert _decide({"type": "s3"}, metered_present=True, metered_mode="allow").proxy == PROXY


def test_smb_is_never_proxied():
    decision = _decide({"type": "smb", "host": "nas"})
    assert decision.proxy == "" and "smb" in decision.reason


@pytest.mark.parametrize(
    ("options", "host"),
    [
        ({"type": "s3", "endpoint": "https://minio.example.com:9000"}, "minio.example.com"),
        ({"type": "webdav", "url": "https://cloud.example.com/dav"}, "cloud.example.com"),
        ({"type": "sftp", "host": "192.168.178.5"}, "192.168.178.5"),
        ({"type": "sftp", "host": "box.example.com:2222"}, "box.example.com"),
        ({"type": "sftp", "host": "[fd00::5]"}, "fd00::5"),
        ({"type": "s3", "provider": "AWS"}, ""),
    ],
)
def test_destination_host(options, host):
    assert bonding.destination_host(options) == host


def test_internal_addresses():
    assert bonding.is_internal_host("192.168.1.10", _private)
    assert not bonding.is_internal_host("", _private)  # provider default host
    assert not bonding.is_internal_host("s3.amazonaws.com", _public)


# --------------------------------------------------------------------------- #
# rclone environment                                                           #
# --------------------------------------------------------------------------- #
def test_rclone_env_http_backend_uses_https_proxy():
    env = rclone._remote_env({"type": "s3", rclone.PROXY_OPTION: PROXY})
    assert env["HTTPS_PROXY"] == PROXY
    assert "RCLONE_CONFIG_OGCTEST__OGC_PROXY" not in env


def test_rclone_env_sftp_uses_socks_option():
    env = rclone._remote_env({"type": "sftp", "host": "h", rclone.PROXY_OPTION: PROXY})
    assert env["RCLONE_CONFIG_OGCTEST_SOCKS_PROXY"] == "127.0.0.1:1080"
    assert env.get("HTTPS_PROXY") != PROXY


def test_rclone_env_without_proxy_is_untouched(monkeypatch):
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    env = rclone._remote_env({"type": "s3"})
    assert "HTTPS_PROXY" not in env
    assert "RCLONE_CONFIG_OGCTEST_SOCKS_PROXY" not in env


# --------------------------------------------------------------------------- #
# net-merge list                                                               #
# --------------------------------------------------------------------------- #
def test_parse_list():
    raw = (
        "USE   wlan0  v4 192.168.1.20  (index 3)\n"
        "USE   eth1  v4 172.20.10.2  (index 5)\n"
        "skip  ogc-wg: not a hardware adapter\n"
        "skip  eth0: no default gateway\n"
    )
    links = bonding.parse_list(raw)
    assert [(link.name, link.used) for link in links] == [
        ("wlan0", True),
        ("eth1", True),
        ("ogc-wg", False),
        ("eth0", False),
    ]
    assert links[0].detail == "v4 192.168.1.20"
    assert links[3].detail == "no default gateway"


def test_build_status_flags_phone_in_reserve():
    status = network.build_status(
        devices=[
            {"device": "wlan0", "type": "wifi", "state": "connected", "connection": "Home"},
            {"device": "eth1", "type": "ethernet", "state": "connected", "connection": "W1"},
        ],
        connectivity="full",
        device_info={
            "wlan0": {"driver": "brcmfmac", "metered": "no", "ip": "192.168.1.20"},
            "eth1": {"driver": "ipheth", "metered": "no", "ip": "172.20.10.2"},
        },
        uplink_ip="192.168.1.20",
    )
    assert status.metered is False  # Wi-Fi carries the default route …
    assert status.metered_present is True  # … but bundling would also use the phone


# --------------------------------------------------------------------------- #
# Worker + API                                                                 #
# --------------------------------------------------------------------------- #
def test_process_job_passes_proxy_to_upload(client, admin_auth, monkeypatch):
    from sqlalchemy import select

    from app.models import TransferJob
    from app.rclone import UploadResult
    from tests.test_tether import _queue_job

    media = _queue_job(client, admin_auth, b"b" * 128)
    monkeypatch.setattr(network, "metered_link_present", lambda: False)
    monkeypatch.setattr(bonding, "proxy_url", lambda: PROXY)
    monkeypatch.setattr(bonding, "proxy_alive", lambda _url: True)
    monkeypatch.setattr(bonding, "is_internal_host", lambda *_a, **_kw: False)
    seen = []

    def upload(local, options, dest, bwlimit=0, on_progress=None):
        seen.append(options.get(rclone.PROXY_OPTION))
        return UploadResult(True, 128, "", 10.0)

    with SessionLocal() as db:
        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        transfers.process_job(db, job, upload, bonding=True)
        job.status = transfers.TransferStatus.QUEUED  # run it again, now unbundled
        db.commit()
        transfers.process_job(db, job, upload, bonding=False)
    assert seen == [PROXY, None]


def test_bandwidth_api_refuses_bonding_without_net_merge(client, admin_auth, monkeypatch):
    monkeypatch.setattr(bonding, "proxy_url", lambda: "")
    resp = client.put("/api/bandwidth", headers=admin_auth, json={"bonding_enabled": True})
    assert resp.status_code == 409
    body = client.get("/api/bandwidth", headers=admin_auth).json()
    assert body["bonding_enabled"] is False
    assert body["bonding_available"] is False


def test_bandwidth_api_toggles_bonding(client, admin_auth, monkeypatch):
    monkeypatch.setattr(bonding, "proxy_url", lambda: PROXY)
    monkeypatch.setattr(
        bonding,
        "get_status",
        lambda: bonding.Status(
            installed=True, running=True, links=[bonding.Link("wlan0", True, "v4 10.0.0.2")]
        ),
    )
    try:
        body = client.put(
            "/api/bandwidth", headers=admin_auth, json={"bonding_enabled": True}
        ).json()
        assert body["bonding_enabled"] is True
        assert body["bonding_running"] is True
        assert body["bonding_links"] == [{"name": "wlan0", "used": True, "detail": "v4 10.0.0.2"}]
    finally:
        client.put("/api/bandwidth", headers=admin_auth, json={"bonding_enabled": False})


def test_bandwidth_api_reports_why_bundling_is_bypassed(client, admin_auth, monkeypatch):
    from app.routers import bandwidth as bw_router

    monkeypatch.setattr(bonding, "proxy_url", lambda: PROXY)
    monkeypatch.setattr(
        bonding, "get_status", lambda: bonding.Status(installed=True, running=True)
    )
    monkeypatch.setattr(bw_router, "metered_link_present", lambda: True)
    try:
        client.put(
            "/api/bandwidth",
            headers=admin_auth,
            json={"bonding_enabled": True, "metered_mode": "pause"},
        )
        body = client.get("/api/bandwidth", headers=admin_auth).json()
        assert "Handy" in body["bonding_bypass"]
        client.put("/api/bandwidth", headers=admin_auth, json={"metered_mode": "allow"})
        body = client.get("/api/bandwidth", headers=admin_auth).json()
        assert body["bonding_bypass"] == ""
    finally:
        client.put(
            "/api/bandwidth",
            headers=admin_auth,
            json={"bonding_enabled": False, "metered_mode": "allow"},
        )
