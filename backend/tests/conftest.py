"""Test configuration: isolate state in a temp dir BEFORE the app is imported."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="ogc-test-")
os.environ.setdefault("OGC_DATA_DIR", _tmp)
os.environ.setdefault("OGC_BUFFER_DIR", os.path.join(_tmp, "buffer"))
os.environ.setdefault("OGC_SECRET_KEY", "test-secret-key")
os.environ.setdefault("OGC_INITIAL_ADMIN_EMAIL", "admin@test.local")
os.environ.setdefault("OGC_INITIAL_ADMIN_PASSWORD", "adminpass123")

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
