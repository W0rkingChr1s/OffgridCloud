from sqlalchemy import select

from app.bandwidth import active_probe
from app.db import SessionLocal
from app.models import MediaItem, TransferJob
from app.transfers import maybe_notify, process_job
from tests.test_transfers import _folder, _provider, _upload, ok_fn

# --- Active bandwidth probe ----------------------------------------------


def test_active_probe_measures_kbps():
    # 100 KiB fetched "instantly" -> high kbps; fetcher is injected.
    kbps = active_probe("http://x", fetcher=lambda url: 100 * 1024)
    assert kbps > 0


def test_active_probe_empty_url_is_zero():
    assert active_probe("") == 0.0


def test_active_probe_handles_errors():
    def boom(url):
        raise OSError("no route")

    assert active_probe("http://x", fetcher=boom) == 0.0


def test_http_download_size_is_time_boxed_on_slow_link():
    # A slow link that never finishes the file must still yield a real sample:
    # the probe stops at the time budget instead of blocking / timing out.
    from app import bandwidth

    reads = {"n": 0}

    class _SlowResp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, _size):
            reads["n"] += 1
            return b"x" * 1024  # endless stream, one chunk per read

    def _fake_urlopen(url, timeout):
        return _SlowResp()

    monkeypatched = bandwidth.urllib.request.urlopen
    bandwidth.urllib.request.urlopen = _fake_urlopen
    try:
        size = bandwidth._http_download_size("http://slow", time_budget=0.05)
    finally:
        bandwidth.urllib.request.urlopen = monkeypatched

    assert size > 0  # streamed some bytes within the budget
    assert reads["n"] >= 1


def test_probe_endpoint_works_without_configured_url(client, admin_auth, monkeypatch):
    # Zero-config: with no probe URL set, the endpoint falls back to the
    # built-in default target instead of erroring — users enter nothing.
    import app.routers.bandwidth as bw

    monkeypatch.setattr(bw, "measure_probe", lambda url: (2048.0, None))
    client.put("/api/system", headers=admin_auth, json={"probe_url": ""})
    resp = client.post("/api/bandwidth/probe", headers=admin_auth)
    assert resp.status_code == 200
    assert resp.json()["last_kbps"] == 2048.0


def test_probe_endpoint_surfaces_error_reason(client, admin_auth, monkeypatch):
    # A failed measurement must report *why*, not just "unreachable", so the
    # admin can tell a 403/DNS/timeout apart.
    import app.routers.bandwidth as bw

    monkeypatch.setattr(bw, "measure_probe", lambda url: (0.0, "HTTPError: 403 Forbidden"))
    resp = client.post("/api/bandwidth/probe", headers=admin_auth)
    assert resp.status_code == 502
    assert "403 Forbidden" in resp.json()["detail"]


def test_measure_probe_sends_browser_user_agent():
    # The bare urllib User-Agent is 403'd by many CDNs; the probe must identify
    # as a normal client. Capture the Request the probe builds.
    from app import bandwidth

    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, _size):
            return b""  # nothing to read; we only care about the request headers

    def _fake_urlopen(req, timeout):
        captured["headers"] = req.headers
        return _Resp()

    original = bandwidth.urllib.request.urlopen
    bandwidth.urllib.request.urlopen = _fake_urlopen
    try:
        bandwidth._http_download_size("http://x")
    finally:
        bandwidth.urllib.request.urlopen = original

    # urllib title-cases header keys on the Request object.
    assert "User-agent" in captured["headers"]
    assert "python-urllib" not in captured["headers"]["User-agent"].lower()


# --- Webhook notification -------------------------------------------------


def test_webhook_called_once_when_media_done(client, admin_auth):
    client.put("/api/system", headers=admin_auth, json={"webhook_url": "http://hook.local/x"})
    prov = _provider(client, admin_auth)
    folder = _folder(client, admin_auth)
    client.post(
        f"/api/folders/{folder['id']}/providers",
        headers=admin_auth,
        json={"provider_id": prov["id"]},
    )
    media = _upload(client, admin_auth, folder["id"], "n.mp4", b"data")

    calls: list[tuple[str, dict]] = []

    def fake_send(url, payload):
        calls.append((url, payload))

    with SessionLocal() as db:
        job = db.scalar(select(TransferJob).where(TransferJob.media_id == media["id"]))
        process_job(db, job, ok_fn)
        # Notify is invoked inside process_job with the real sender (no webhook
        # server in tests); assert state, then exercise dedup with a fake sender.
        m = db.get(MediaItem, media["id"])
        # process_job tried to POST to an unreachable URL -> notified stays False.
        m.notified = False
        db.commit()
        maybe_notify(db, media["id"], send_fn=fake_send)
        maybe_notify(db, media["id"], send_fn=fake_send)  # second call deduped

    assert len(calls) == 1
    assert calls[0][1]["event"] == "media.done"
    assert calls[0][1]["filename"] == "n.mp4"


def test_webhook_not_sent_when_unset(client, admin_auth):
    client.put("/api/system", headers=admin_auth, json={"webhook_url": ""})
    prov = _provider(client, admin_auth)
    folder = _folder(client, admin_auth)
    client.post(
        f"/api/folders/{folder['id']}/providers",
        headers=admin_auth,
        json={"provider_id": prov["id"]},
    )
    media = _upload(client, admin_auth, folder["id"], "q.mp4", b"data")
    calls = []
    with SessionLocal() as db:
        maybe_notify(db, media["id"], send_fn=lambda u, p: calls.append(1))
    assert calls == []
