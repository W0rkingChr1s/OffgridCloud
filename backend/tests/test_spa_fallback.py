"""Tests for the SPA (client-side routing) static fallback.

The UI uses HTML5 history routing, so a browser refresh on a deep link such as
``/admin/system`` must serve ``index.html`` instead of a bare 404 — otherwise
the user sees ``{"detail":"Not Found"}``.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import SPAStaticFiles


def _client(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>OffgridCloud</title>")
    (static / "assets").mkdir()
    (static / "assets" / "app.js").write_text("console.log('ok')")

    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    app.mount("/", SPAStaticFiles(directory=static, html=True), name="static")
    return TestClient(app)


def test_deep_link_refresh_serves_index(tmp_path):
    resp = _client(tmp_path).get("/admin/system")
    assert resp.status_code == 200
    assert resp.text.lstrip().lower().startswith("<!doctype html")


def test_root_serves_index(tmp_path):
    resp = _client(tmp_path).get("/")
    assert resp.status_code == 200
    assert resp.text.lstrip().lower().startswith("<!doctype html")


def test_real_asset_is_served(tmp_path):
    resp = _client(tmp_path).get("/assets/app.js")
    assert resp.status_code == 200
    assert "console.log" in resp.text


def test_missing_asset_still_404s(tmp_path):
    # A file-looking path (has a suffix) that doesn't exist must stay a 404 so
    # broken references remain visible instead of silently returning HTML.
    resp = _client(tmp_path).get("/assets/missing.js")
    assert resp.status_code == 404


def test_unknown_api_path_does_not_serve_html(tmp_path):
    # Unmatched /api/* paths fall through to the static mount; they must not be
    # rewritten to index.html.
    resp = _client(tmp_path).get("/api/does-not-exist")
    assert resp.status_code == 404
    assert not resp.text.lstrip().lower().startswith("<!doctype html")
