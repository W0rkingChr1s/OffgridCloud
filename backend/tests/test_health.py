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


def test_root_serves_ui_or_hint():
    # With a built frontend (app/static), root serves HTML; without it, a JSON
    # hint pointing at the API. Both are valid — assert the route responds.
    resp = client.get("/")
    assert resp.status_code == 200
    is_html = resp.text.lstrip().lower().startswith("<!doctype html")
    assert is_html or "api_health" in resp.json()
