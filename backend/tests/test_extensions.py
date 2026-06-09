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


def test_probe_endpoint_requires_url(client, admin_auth):
    client.put("/api/system", headers=admin_auth, json={"probe_url": ""})
    assert client.post("/api/bandwidth/probe", headers=admin_auth).status_code == 400


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
