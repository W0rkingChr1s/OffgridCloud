from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["app"] == "OffgridCloud"
    assert "rclone" in body


def test_health_prtg_format():
    resp = client.get("/api/health", params={"format": "prtg"})
    assert resp.status_code == 200
    body = resp.json()
    channels = body["prtg"]["result"]
    names = [c["channel"] for c in channels]
    assert "Status" in names
    assert "rclone verfügbar" in names
    # Disk stats are best-effort but available in the test environment.
    assert "Speicher belegt" in names
    status = next(c for c in channels if c["channel"] == "Status")
    assert status["value"] == "1"
    assert status["limitmode"] == 1
    # Every value must be a PRTG-parsable number string.
    for channel in channels:
        float(channel["value"])
    assert "OffgridCloud" in body["prtg"]["text"]


def test_health_rejects_unknown_format():
    assert client.get("/api/health", params={"format": "xml"}).status_code == 422


def test_root_serves_ui_or_hint():
    # With a built frontend (app/static), root serves HTML; without it, a JSON
    # hint pointing at the API. Both are valid — assert the route responds.
    resp = client.get("/")
    assert resp.status_code == 200
    is_html = resp.text.lstrip().lower().startswith("<!doctype html")
    assert is_html or "api_health" in resp.json()
