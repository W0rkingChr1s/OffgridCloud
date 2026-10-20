"""Test configuration: isolate state in a temp dir BEFORE the app is imported."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="ogc-test-")
os.environ.setdefault("OGC_DATA_DIR", _tmp)
os.environ.setdefault("OGC_BUFFER_DIR", os.path.join(_tmp, "buffer"))
os.environ.setdefault("OGC_SECRET_KEY", "test-secret-key")
os.environ.setdefault("OGC_INITIAL_ADMIN_EMAIL", "admin@test.local")
os.environ.setdefault("OGC_INITIAL_ADMIN_PASSWORD", "adminpass123")
# Drive transfer-job state deterministically in tests; no background worker.
os.environ.setdefault("OGC_WORKER_ENABLED", "false")
# No multicast traffic from the test suite.
os.environ.setdefault("OGC_MDNS_ENABLED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

ADMIN_EMAIL = "admin@test.local"
ADMIN_PASSWORD = "adminpass123"


@pytest.fixture
def client():
    # The context manager runs lifespan -> init_db + ensure_initial_admin.
    with TestClient(app) as c:
        yield c


def _token(client: TestClient, email: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture
def admin_auth(client):
    token = _token(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _no_telegram_network(monkeypatch):
    """Saving a bot token greets the chat in a background thread; keep the test
    suite off the real Bot API. Tests that care install their own fake."""
    from app import telegram_bot

    def offline(*_args, **_kwargs):
        raise telegram_bot.TelegramError("offline in tests")

    monkeypatch.setattr(telegram_bot, "api_call", offline)
