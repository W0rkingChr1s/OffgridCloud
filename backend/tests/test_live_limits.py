"""Live bandwidth control of running uploads via rclone rc."""

import base64
import json
import stat
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import bandwidth, rclone, transfers
from app.config import get_settings
from app.db import SessionLocal

# Behaves like rclone for what run_upload needs: logs its argv and whether the
# rc password arrived via the environment, can refuse the rc port like real
# rclone does (level "critical", exit 1), and otherwise "uploads" successfully.
FAKE_RCLONE = """#!/usr/bin/env bash
echo "ARGS $*" >> "$LOG"
echo "RCPASS ${RCLONE_RC_PASS:+set}" >> "$LOG"
if [[ "$*" == *"--rc "* && -n "$RC_BUSY" ]]; then
  echo '{"level":"critical","msg":"remote control: bind: address already in use"}' >&2
  exit 1
fi
echo '{"level":"notice","stats":{"bytes":5,"totalBytes":5,"speed":5}}' >&2
"""


@pytest.fixture
def fake_rclone(tmp_path, monkeypatch):
    binary = tmp_path / "rclone"
    binary.write_text(FAKE_RCLONE)
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.log"
    monkeypatch.setenv("LOG", str(log))
    monkeypatch.setattr(get_settings(), "rclone_binary", str(binary))
    monkeypatch.setattr(get_settings(), "rclone_live_limits", True)
    src = tmp_path / "f.bin"
    src.write_bytes(b"hello")
    return src, log


def _calls(log):
    return log.read_text().splitlines()


def test_rc_password_travels_in_the_environment_not_argv(fake_rclone):
    src, log = fake_rclone
    assert rclone.run_upload(str(src), {"type": "local"}, "/x", 500).ok
    args, rcpass = _calls(log)
    assert "--rc --rc-addr 127.0.0.1:" in args and "--bwlimit 500k" in args
    assert "rc-pass" not in args and "rc-user" not in args
    assert rcpass == "RCPASS set"
    assert rclone.live_uploads() == []  # unregistered once done


def test_busy_rc_port_falls_back_to_a_plain_upload(fake_rclone, monkeypatch):
    src, log = fake_rclone
    monkeypatch.setenv("RC_BUSY", "1")
    result = rclone.run_upload(str(src), {"type": "local"}, "/x", 0)
    assert result.ok
    first, _, second, _ = _calls(log)
    assert "--rc " in first and "--rc" not in second


def test_live_limits_can_be_switched_off(fake_rclone, monkeypatch):
    src, log = fake_rclone
    monkeypatch.setattr(get_settings(), "rclone_live_limits", False)
    assert rclone.run_upload(str(src), {"type": "local"}, "/x", 0).ok
    assert "--rc" not in _calls(log)[0]


# --- core/bwlimit ----------------------------------------------------------------


@pytest.fixture
def rc_server():
    """Stands in for a running rclone's rc API (basic auth ogc:pw)."""
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            expected = "Basic " + base64.b64encode(b"ogc:pw").decode()
            ok = self.headers.get("Authorization") == expected and self.path == "/core/bwlimit"
            calls.append((ok, body))
            self.send_response(200 if ok else 401)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", calls
    server.shutdown()


def test_set_live_limit_talks_core_bwlimit(rc_server):
    url, calls = rc_server
    up = rclone.LiveUpload(url=url, user="ogc", password="pw", kbps=1000)
    assert rclone.set_live_limit(up, 300) is True
    assert calls == [(True, {"rate": "300k"})] and up.kbps == 300
    assert rclone.set_live_limit(up, 0) is True
    assert calls[-1] == (True, {"rate": "off"}) and up.kbps == 0

    wrong = rclone.LiveUpload(url=url, user="ogc", password="nope", kbps=1000)
    assert rclone.set_live_limit(wrong, 300) is False and wrong.kbps == 1000
    gone = rclone.LiveUpload(url="http://127.0.0.1:1", user="ogc", password="pw", kbps=5)
    assert rclone.set_live_limit(gone, 300) is False  # upload already finished


# --- the worker keeps running uploads on the current limit ------------------------


def _reset_policy():
    with SessionLocal() as db:
        policy = bandwidth.get_policy(db)
        policy.bwlimit_kbps, policy.parallel_uploads, policy.schedule_json = 0, 1, "[]"
        policy.pool_limit_kbps, policy.pool_limit_until = 0, None
        db.commit()


@pytest.fixture
def policy_reset(client):
    # Reset before, too: earlier tests (test_bandwidth) leave a 22:00–06:00
    # schedule in the shared DB, which made these fail only at night.
    _reset_policy()
    yield
    _reset_policy()
    rclone._live.clear()


def _set(**fields):
    with SessionLocal() as db:
        policy = bandwidth.get_policy(db)
        for key, value in fields.items():
            setattr(policy, key, value)
        db.commit()
        return transfers.upload_limit(policy)


def test_upload_limit_combines_own_pool_and_slots(policy_reset):
    assert _set(bwlimit_kbps=0) == 0
    assert _set(bwlimit_kbps=1000) == 1000
    with SessionLocal() as db:
        bandwidth.set_pool_limit(db, 600, "hub", transfers._utcnow_naive())
    assert _set() == 600
    assert _set(parallel_uploads=2) == 300


def test_sync_pushes_only_changed_limits(policy_reset, monkeypatch):
    pushed = []

    def fake_set(up, kbps):
        pushed.append((up.url, kbps))
        up.kbps = kbps
        return True

    monkeypatch.setattr(rclone, "set_live_limit", fake_set)
    assert transfers.sync_live_limits() == 0  # nothing running
    _set(bwlimit_kbps=800)
    a = rclone.LiveUpload(url="a", user="ogc", password="x", kbps=0)
    b = rclone.LiveUpload(url="b", user="ogc", password="x", kbps=800)
    rclone._live.update({id(a): a, id(b): b})
    assert transfers.sync_live_limits() == 1
    assert pushed == [("a", 800)]
    assert transfers.sync_live_limits() == 0  # already there
