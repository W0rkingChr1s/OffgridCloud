# WebAuthn/Passkey Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add passkey (WebAuthn) login alongside the existing email/password login — users self-enroll passkeys, log in one-click (discoverable) or email-first, with credentials bound per-origin so both `offgridcloud.local` and a public domain work.

**Architecture:** New `webauthn_config.py` (pure helpers: per-request RP-ID/origin derivation validated against an allowlist, plus an in-memory TTL challenge store) and `routers/webauthn.py` (four ceremony endpoints + credential management). A new `webauthn_credentials` table and a `webauthn_user_handle` column on `users`. On a successful assertion the server issues the SAME JWT as the password login (`security.create_access_token`), so the frontend token flow is unchanged. Frontend gets a `webauthn.ts` browser-API wrapper, a passkey button on the login page, and a "Passkeys" settings section.

**Tech Stack:** FastAPI + Pydantic + SQLAlchemy (SQLite), `py_webauthn` (`webauthn` package), pytest; React + TypeScript + Vite, native `navigator.credentials` API.

**Spec:** `docs/superpowers/specs/2026-07-16-passkey-webauthn-design.md`

**Branch:** `claude/passkey-webauthn` (off `main`). Design docs are already committed here.

## ⚠️ Dependency: merge PR #57 first

This plan reuses `app.https_config.read_state(data_dir)` (returns `{"hostname", "domain"}`) from the HTTPS work in **PR #57** to build the origin allowlist. **Do not start implementation until #57 is merged into `main`**, then rebase this branch onto `main`. If for some reason #57 is delayed, Task 2 notes a minimal fallback.

---

## File Structure

**New files:**
- `backend/app/webauthn_config.py` — pure helpers: origin/RP-ID derivation + allowlist; TTL challenge store.
- `backend/app/routers/webauthn.py` — ceremony + management endpoints.
- `backend/tests/test_webauthn_config.py` — unit tests for the pure helpers.
- `backend/tests/test_webauthn.py` — endpoint tests (py_webauthn verify injected).
- `frontend/src/webauthn.ts` — browser-API wrapper (base64url, register/login).
- `frontend/src/pages/Passkeys.tsx` — settings section listing/adding/removing passkeys.

**Modified files:**
- `backend/requirements.txt` — add `webauthn`.
- `backend/app/models.py` — `WebAuthnCredential` model + `webauthn_user_handle` on `User`.
- `backend/app/db.py` — `_ADDED_COLUMNS` entry for `webauthn_user_handle`.
- `backend/app/schemas.py` — webauthn request/response schemas.
- `backend/app/main.py` — register the webauthn router.
- `frontend/src/api.ts` — `Passkey` interface + typed credential-management calls.
- `frontend/src/auth.tsx` — `loginWithPasskey`.
- `frontend/src/pages/Login.tsx` — passkey button + capability gating.
- `frontend/src/App.tsx` (or wherever routes live) — route to the Passkeys settings page (verify actual router file during implementation).

**Note on py_webauthn API (v2.x):** functions used — `generate_registration_options`, `verify_registration_response`, `generate_authentication_options`, `verify_authentication_response`, `options_to_json`; structs from `webauthn.helpers.structs`. `user_id` must be `bytes`. `verify_*` return objects with `.credential_id`, `.credential_public_key`, `.sign_count` (registration) and `.new_sign_count`, `.credential_id` (authentication).

---

## Task 1: Dependency + data model

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/app/models.py`
- Modify: `backend/app/db.py:54` (`_ADDED_COLUMNS`)
- Test: `backend/tests/test_webauthn_config.py` (new — starts with a model smoke test)

- [ ] **Step 1: Add the dependency and install it**

Append to `backend/requirements.txt`:

```
webauthn==2.5.2
```

Run: `cd backend && .venv/bin/pip install 'webauthn>=2.5,<3'`
Expected: installs cleanly. If `2.5.2` is unavailable, install the newest `<3` and update the pin in requirements.txt to the installed version (`.venv/bin/pip show webauthn | grep Version`).

- [ ] **Step 2: Write a failing model smoke test**

Create `backend/tests/test_webauthn_config.py`:

```python
"""WebAuthn: model + pure helpers (origin/RP-ID derivation, challenge store)."""

from __future__ import annotations

import pytest


def test_webauthn_credential_model_importable():
    from app.models import WebAuthnCredential

    # The columns the ceremonies rely on exist.
    cols = WebAuthnCredential.__table__.columns.keys()
    for expected in (
        "id",
        "user_id",
        "credential_id",
        "public_key",
        "sign_count",
        "rp_id",
        "transports",
        "name",
        "created_at",
        "last_used_at",
    ):
        assert expected in cols


def test_user_has_webauthn_handle_column():
    from app.models import User

    assert "webauthn_user_handle" in User.__table__.columns.keys()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_webauthn_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'WebAuthnCredential'`.

- [ ] **Step 4: Add the model + column**

In `backend/app/models.py`, add `LargeBinary` to the `sqlalchemy` import list, then add the column to `User` (after `created_at`, line ~73):

```python
    # WebAuthn: stable non-PII user handle for discoverable credentials.
    # Nullable + filled lazily on first passkey registration (existing users
    # created before this feature won't have one yet).
    webauthn_user_handle: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
```

Then add the model (after the `User` class):

```python
class WebAuthnCredential(Base):
    """A registered passkey. Bound to one RP-ID (origin) — a user may hold one
    credential per origin (e.g. offgridcloud.local and a public domain)."""

    __tablename__ = "webauthn_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, unique=True, index=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(default=0)
    rp_id: Mapped[str] = mapped_column(String(255))
    transports: Mapped[str] = mapped_column(String(255), default="")  # JSON list
    name: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

- [ ] **Step 5: Add the additive migration for the new user column**

In `backend/app/db.py`, append to `_ADDED_COLUMNS` (before the closing `]`, line ~77):

```python
    # WebAuthn user handle — added to an existing users table. Nullable (no
    # DEFAULT): filled on first passkey registration.
    ("users", "webauthn_user_handle", "BLOB"),
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_webauthn_config.py -v`
Expected: PASS (both).

- [ ] **Step 7: Commit**

```bash
git add backend/requirements.txt backend/app/models.py backend/app/db.py backend/tests/test_webauthn_config.py
git commit -m "feat(webauthn): credential model + user handle column + dependency"
```

---

## Task 2: Origin/RP-ID derivation + allowlist

**Files:**
- Create: `backend/app/webauthn_config.py`
- Test: `backend/tests/test_webauthn_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_webauthn_config.py`:

```python
from app import webauthn_config


def test_parse_origin_extracts_rpid_and_origin():
    assert webauthn_config.parse_origin("https://offgridcloud.local") == (
        "offgridcloud.local",
        "https://offgridcloud.local",
    )
    # Port is kept in the origin but stripped from the RP-ID.
    assert webauthn_config.parse_origin("http://localhost:5173") == (
        "localhost",
        "http://localhost:5173",
    )


def test_parse_origin_rejects_garbage():
    for bad in ("", "not a url", "ftp://x"):
        with pytest.raises(ValueError):
            webauthn_config.parse_origin(bad)


def test_build_allowlist_from_state_and_localhost():
    allow = webauthn_config.build_allowlist(
        state={"hostname": "offgridcloud", "domain": "cloud.example.com"},
        extra_origins="",
    )
    assert "offgridcloud.local" in allow
    assert "cloud.example.com" in allow
    assert "localhost" in allow


def test_build_allowlist_empty_state_is_localhost_only():
    allow = webauthn_config.build_allowlist(state={"hostname": "", "domain": ""}, extra_origins="")
    assert allow == {"localhost"}


def test_build_allowlist_includes_extra_origins():
    allow = webauthn_config.build_allowlist(
        state={"hostname": "", "domain": ""}, extra_origins="box.example.net, other.local"
    )
    assert "box.example.net" in allow
    assert "other.local" in allow


def test_resolve_rejects_origin_not_in_allowlist():
    with pytest.raises(webauthn_config.OriginNotAllowed):
        webauthn_config.resolve_rp("https://evil.example.com", allowlist={"offgridcloud.local"})


def test_resolve_returns_rpid_and_origin_when_allowed():
    rp_id, origin = webauthn_config.resolve_rp(
        "https://offgridcloud.local", allowlist={"offgridcloud.local", "localhost"}
    )
    assert rp_id == "offgridcloud.local"
    assert origin == "https://offgridcloud.local"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_webauthn_config.py -v -k "origin or allowlist or resolve"`
Expected: FAIL — `AttributeError`/`ImportError` (functions not defined).

- [ ] **Step 3: Implement the derivation + allowlist**

Create `backend/app/webauthn_config.py`:

```python
"""WebAuthn origin/RP-ID derivation, allowlist, and challenge store.

Pure logic, no HTTP — unit-testable in isolation (same split as power.py /
https_config.py). The RP-ID a passkey binds to is derived per-request from the
browser Origin and validated against an allowlist so a forged Host header can't
make us accept an arbitrary RP-ID.
"""

from __future__ import annotations

import secrets
import time
from urllib.parse import urlparse


class OriginNotAllowed(ValueError):
    """Raised when a request Origin is not in the configured allowlist."""


def parse_origin(origin: str) -> tuple[str, str]:
    """Return (rp_id, normalised_origin) for an ``https://host[:port]`` string.

    rp_id is the bare hostname (no scheme/port). Raises ValueError on anything
    that isn't an http(s) URL with a host.
    """
    parsed = urlparse(origin.strip())
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise ValueError(f"invalid origin: {origin!r}")
    normalised = f"{parsed.scheme}://{parsed.netloc}"
    return parsed.hostname, normalised


def build_allowlist(*, state: dict[str, str], extra_origins: str) -> set[str]:
    """Allowed RP-IDs: <hostname>.local + domain from https_state.json, always
    localhost (dev), plus any comma-separated OGC_WEBAUTHN_EXTRA_ORIGINS."""
    allow: set[str] = {"localhost"}
    hostname = (state.get("hostname") or "").strip()
    domain = (state.get("domain") or "").strip()
    if hostname:
        allow.add(f"{hostname}.local")
    if domain:
        allow.add(domain)
    for extra in extra_origins.split(","):
        extra = extra.strip()
        if extra:
            allow.add(extra)
    return allow


def resolve_rp(origin: str, *, allowlist: set[str]) -> tuple[str, str]:
    """Parse ``origin`` and confirm its RP-ID is allowed. Returns (rp_id, origin)."""
    rp_id, normalised = parse_origin(origin)
    if rp_id not in allowlist:
        raise OriginNotAllowed(f"origin not allowed: {rp_id}")
    return rp_id, normalised
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_webauthn_config.py -v -k "origin or allowlist or resolve"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/webauthn_config.py backend/tests/test_webauthn_config.py
git commit -m "feat(webauthn): origin/RP-ID derivation + allowlist"
```

**Fallback if #57 is NOT merged:** `build_allowlist` takes `state` as a plain dict, so it does not import `https_config`. The router (Task 5) will call `https_config.read_state`; if that module is absent, temporarily pass `{"hostname": "", "domain": ""}` and rely on `OGC_WEBAUTHN_EXTRA_ORIGINS`. Prefer merging #57.

---

## Task 3: In-memory TTL challenge store

**Files:**
- Modify: `backend/app/webauthn_config.py`
- Test: `backend/tests/test_webauthn_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_webauthn_config.py`:

```python
def test_challenge_store_put_take_roundtrip():
    store = webauthn_config.ChallengeStore(ttl_seconds=300, clock=lambda: 1000.0)
    nonce = store.put(b"challengebytes", meta={"user_id": 7})
    assert isinstance(nonce, str) and nonce
    entry = store.take(nonce)
    assert entry.challenge == b"challengebytes"
    assert entry.meta == {"user_id": 7}


def test_challenge_take_is_one_time():
    store = webauthn_config.ChallengeStore(ttl_seconds=300, clock=lambda: 1000.0)
    nonce = store.put(b"c", meta={})
    store.take(nonce)
    with pytest.raises(KeyError):
        store.take(nonce)


def test_challenge_expires_after_ttl():
    now = {"t": 1000.0}
    store = webauthn_config.ChallengeStore(ttl_seconds=300, clock=lambda: now["t"])
    nonce = store.put(b"c", meta={})
    now["t"] = 1000.0 + 301  # past TTL
    with pytest.raises(KeyError):
        store.take(nonce)


def test_challenge_unknown_nonce_raises():
    store = webauthn_config.ChallengeStore(ttl_seconds=300, clock=lambda: 1000.0)
    with pytest.raises(KeyError):
        store.take("nope")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_webauthn_config.py -v -k challenge`
Expected: FAIL — `AttributeError: module 'app.webauthn_config' has no attribute 'ChallengeStore'`.

- [ ] **Step 3: Implement the store**

Append to `backend/app/webauthn_config.py`:

```python
from dataclasses import dataclass, field


@dataclass
class _Entry:
    challenge: bytes
    meta: dict
    expires_at: float


@dataclass
class ChallengeStore:
    """Process-local, one-time, TTL'd WebAuthn challenges.

    Fine for the single-process uvicorn on the Pi (systemd unit has no
    --workers). Lost on restart, which is harmless — challenges live seconds.
    ``clock`` is injectable for tests.
    """

    ttl_seconds: float = 300.0
    clock: object = time.monotonic
    _entries: dict[str, _Entry] = field(default_factory=dict)

    def put(self, challenge: bytes, *, meta: dict) -> str:
        self._sweep()
        nonce = secrets.token_urlsafe(16)
        self._entries[nonce] = _Entry(
            challenge=challenge, meta=meta, expires_at=self.clock() + self.ttl_seconds
        )
        return nonce

    def take(self, nonce: str) -> _Entry:
        """Pop and return an entry. Raises KeyError if missing or expired."""
        entry = self._entries.pop(nonce, None)
        if entry is None:
            raise KeyError(nonce)
        if self.clock() > entry.expires_at:
            raise KeyError(nonce)
        return entry

    def _sweep(self) -> None:
        now = self.clock()
        expired = [n for n, e in self._entries.items() if now > e.expires_at]
        for n in expired:
            self._entries.pop(n, None)
```

Note: `clock` defaults to `time.monotonic` (a bound method reference); tests pass a `lambda`. Both are callables.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_webauthn_config.py -v -k challenge`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/webauthn_config.py backend/tests/test_webauthn_config.py
git commit -m "feat(webauthn): in-memory TTL challenge store"
```

---

## Task 4: Schemas

**Files:**
- Modify: `backend/app/schemas.py` (append near the other schemas, after `TokenResponse` ~line 32 area is fine, or at the end near auth schemas)

- [ ] **Step 1: Add the schemas**

Append to `backend/app/schemas.py`:

```python
# --- WebAuthn / passkeys ----------------------------------------------------
class WebAuthnRegisterVerify(BaseModel):
    """Attestation returned by navigator.credentials.create(), plus our nonce."""

    nonce: str
    credential: dict  # raw PublicKeyCredential JSON from the browser
    name: str = ""


class WebAuthnLoginOptionsRequest(BaseModel):
    email: str | None = None


class WebAuthnLoginVerify(BaseModel):
    nonce: str
    credential: dict


class PasskeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    rp_id: str
    created_at: datetime
    last_used_at: datetime | None


class PasskeyRename(BaseModel):
    name: str
```

- [ ] **Step 2: Verify import**

Run: `cd backend && .venv/bin/python -c "from app.schemas import WebAuthnRegisterVerify, WebAuthnLoginOptionsRequest, WebAuthnLoginVerify, PasskeyOut, PasskeyRename; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/app/schemas.py
git commit -m "feat(webauthn): request/response schemas"
```

---

## Task 5: Router — helpers + registration ceremony

**Files:**
- Create: `backend/app/routers/webauthn.py`
- Modify: `backend/app/main.py` (import + register)
- Test: `backend/tests/test_webauthn.py` (new)

This task builds the router skeleton, the shared per-request RP helper, a module-level `challenges` store, and the two registration endpoints. Login + management come in Tasks 6–7. The py_webauthn `verify_registration_response` is called through a module attribute so tests can monkeypatch it.

- [ ] **Step 1: Write the failing registration tests**

Create `backend/tests/test_webauthn.py`:

```python
"""WebAuthn ceremony endpoints. py_webauthn verify functions are monkeypatched
so tests exercise our server logic without a real authenticator."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

ORIGIN_HEADERS = {"Origin": "http://localhost:5173"}


def test_register_options_requires_auth(client):
    # No token → 401/403 from the bearer dependency.
    resp = client.post("/api/auth/webauthn/register/options", headers=ORIGIN_HEADERS)
    assert resp.status_code in (401, 403)


def test_register_options_returns_options_and_nonce(client, admin_auth):
    resp = client.post(
        "/api/auth/webauthn/register/options",
        headers={**admin_auth, **ORIGIN_HEADERS},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "nonce" in body
    # options_to_json emits the options object directly (challenge, rp, user…),
    # NOT nested under a "publicKey" key.
    assert "challenge" in body["options"]
    assert "rp" in body["options"]


def test_register_options_rejects_disallowed_origin(client, admin_auth):
    resp = client.post(
        "/api/auth/webauthn/register/options",
        headers={**admin_auth, "Origin": "https://evil.example.com"},
    )
    assert resp.status_code == 400


@dataclass
class _FakeVerifiedReg:
    credential_id: bytes = b"cred-123"
    credential_public_key: bytes = b"pubkey"
    sign_count: int = 0


def test_register_verify_persists_credential(client, admin_auth, monkeypatch):
    import app.routers.webauthn as wa

    # 1) get options to seed a challenge + nonce
    opts = client.post(
        "/api/auth/webauthn/register/options",
        headers={**admin_auth, **ORIGIN_HEADERS},
    ).json()

    # 2) stub py_webauthn verification
    monkeypatch.setattr(wa, "verify_registration_response", lambda **kw: _FakeVerifiedReg())

    resp = client.post(
        "/api/auth/webauthn/register/verify",
        headers={**admin_auth, **ORIGIN_HEADERS},
        json={"nonce": opts["nonce"], "credential": {"fake": "attestation"}, "name": "My Laptop"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "My Laptop"
    assert body["rp_id"] == "localhost"


def test_register_verify_rejects_stale_nonce(client, admin_auth, monkeypatch):
    import app.routers.webauthn as wa

    monkeypatch.setattr(wa, "verify_registration_response", lambda **kw: _FakeVerifiedReg())
    resp = client.post(
        "/api/auth/webauthn/register/verify",
        headers={**admin_auth, **ORIGIN_HEADERS},
        json={"nonce": "bogus", "credential": {}, "name": ""},
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_webauthn.py -v -k register`
Expected: FAIL — 404 (router not registered).

- [ ] **Step 3: Implement the router (helpers + registration)**

Create `backend/app/routers/webauthn.py`:

```python
"""WebAuthn / passkey ceremonies + credential management.

Registration & management require a logged-in user; login is public. A
successful assertion issues the SAME JWT as the password login, so the frontend
token flow is unchanged. py_webauthn's verify_* functions are referenced at
module scope so tests can monkeypatch them.
"""

from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .. import https_config, webauthn_config
from ..admin_ops import audit
from ..config import get_settings
from ..db import get_db
from ..deps import get_current_user
from ..models import User, WebAuthnCredential
from ..schemas import (
    PasskeyOut,
    PasskeyRename,
    TokenResponse,
    WebAuthnLoginOptionsRequest,
    WebAuthnLoginVerify,
    WebAuthnRegisterVerify,
)
from ..security import create_access_token

router = APIRouter(prefix="/api/auth/webauthn", tags=["webauthn"])

# Process-local challenge store (single-process uvicorn — see design).
challenges = webauthn_config.ChallengeStore(ttl_seconds=300)

RP_NAME = "OffgridCloud"


def _rp_for_request(request: Request) -> tuple[str, str]:
    """Derive + validate (rp_id, origin) from the request Origin header."""
    settings = get_settings()
    origin = request.headers.get("origin") or ""
    if not origin:
        # Fall back to Host (no scheme) — assume https unless localhost.
        host = request.headers.get("host", "")
        scheme = "http" if host.startswith("localhost") else "https"
        origin = f"{scheme}://{host}"
    state = https_config.read_state(settings.data_dir)
    extra = getattr(settings, "webauthn_extra_origins", "")
    allow = webauthn_config.build_allowlist(state=state, extra_origins=extra)
    try:
        return webauthn_config.resolve_rp(origin, allowlist=allow)
    except (webauthn_config.OriginNotAllowed, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Origin nicht erlaubt: {exc}") from exc


def _ensure_user_handle(db: Session, user: User) -> bytes:
    if not user.webauthn_user_handle:
        user.webauthn_user_handle = secrets.token_bytes(32)
        db.commit()
    return user.webauthn_user_handle


# --- Registration (logged-in) ----------------------------------------------
@router.post("/register/options")
def register_options(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    rp_id, _origin = _rp_for_request(request)
    handle = _ensure_user_handle(db, user)
    existing = db.scalars(
        select(WebAuthnCredential).where(
            WebAuthnCredential.user_id == user.id, WebAuthnCredential.rp_id == rp_id
        )
    ).all()
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=RP_NAME,
        user_id=handle,
        user_name=user.email,
        user_display_name=user.name or user.email,
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=c.credential_id) for c in existing
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    nonce = challenges.put(options.challenge, meta={"user_id": user.id, "rp_id": rp_id})
    return {"nonce": nonce, "options": json.loads(options_to_json(options))}


@router.post("/register/verify", response_model=PasskeyOut)
def register_verify(
    payload: WebAuthnRegisterVerify,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WebAuthnCredential:
    rp_id, origin = _rp_for_request(request)
    try:
        entry = challenges.take(payload.nonce)
    except KeyError:
        raise HTTPException(status_code=400, detail="Registrierung abgelaufen, bitte erneut.") from None
    if entry.meta.get("user_id") != user.id:
        raise HTTPException(status_code=400, detail="Ungültige Registrierungs-Sitzung.")

    try:
        verified = verify_registration_response(
            credential=payload.credential,
            expected_challenge=entry.challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            require_user_verification=False,
        )
    except Exception as exc:  # noqa: BLE001 — surface verification failure
        raise HTTPException(status_code=400, detail=f"Passkey-Prüfung fehlgeschlagen: {exc}") from exc

    if db.scalar(
        select(WebAuthnCredential).where(
            WebAuthnCredential.credential_id == verified.credential_id
        )
    ):
        raise HTTPException(status_code=409, detail="Dieser Passkey ist bereits registriert.")

    cred = WebAuthnCredential(
        user_id=user.id,
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        rp_id=rp_id,
        transports=json.dumps(payload.credential.get("response", {}).get("transports", [])),
        name=payload.name.strip() or "Passkey",
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    audit(db, user, "auth.webauthn.register", f"rp_id={rp_id} name={cred.name}")
    return cred
```

Note: remove the `from . import __name__ as _pkg` line if your linter flags it — it's only there to keep imports grouped; it has no effect. (Prefer deleting it.)

- [ ] **Step 4: Add the settings field for extra origins**

In `backend/app/config.py`, after the HTTPS block (the `https_apply_command` field from #57), add:

```python
    # --- WebAuthn / passkeys -----------------------------------------------
    # Extra comma-separated origins allowed as passkey RP-IDs, beyond
    # <hostname>.local + the configured domain + localhost. Usually empty.
    webauthn_extra_origins: str = ""
```

- [ ] **Step 5: Register the router**

In `backend/app/main.py`, add `webauthn` to the router import block and register it after `auth.router`:

```python
app.include_router(auth.router)
app.include_router(webauthn.router)
```

(Add `webauthn` to the `from .routers import (...)` list.)

- [ ] **Step 6: Run the registration tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_webauthn.py -v -k register`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/webauthn.py backend/app/main.py backend/app/config.py backend/tests/test_webauthn.py
git commit -m "feat(webauthn): registration ceremony endpoints"
```

---

## Task 6: Login ceremony

**Files:**
- Modify: `backend/app/routers/webauthn.py`
- Test: `backend/tests/test_webauthn.py`

- [ ] **Step 1: Write the failing login tests**

Append to `backend/tests/test_webauthn.py`:

```python
@dataclass
class _FakeVerifiedAuth:
    new_sign_count: int = 5
    credential_id: bytes = b"cred-123"


def _register_a_credential(client, admin_auth, monkeypatch, cred_id=b"cred-123"):
    """Helper: drive register options+verify with a stubbed authenticator."""
    import app.routers.webauthn as wa

    opts = client.post(
        "/api/auth/webauthn/register/options",
        headers={**admin_auth, **ORIGIN_HEADERS},
    ).json()

    @dataclass
    class _Reg:
        credential_id: bytes = cred_id
        credential_public_key: bytes = b"pubkey"
        sign_count: int = 0

    monkeypatch.setattr(wa, "verify_registration_response", lambda **kw: _Reg())
    client.post(
        "/api/auth/webauthn/register/verify",
        headers={**admin_auth, **ORIGIN_HEADERS},
        json={"nonce": opts["nonce"], "credential": {}, "name": "Test"},
    )


def test_login_options_unknown_email_is_enumeration_safe(client):
    resp = client.post(
        "/api/auth/webauthn/login/options",
        headers=ORIGIN_HEADERS,
        json={"email": "nobody@test.local"},
    )
    # Still 200 with a challenge; no "user not found" leak.
    assert resp.status_code == 200
    assert "nonce" in resp.json()


def test_login_verify_issues_token(client, admin_auth, monkeypatch):
    import app.routers.webauthn as wa

    _register_a_credential(client, admin_auth, monkeypatch)

    opts = client.post(
        "/api/auth/webauthn/login/options",
        headers=ORIGIN_HEADERS,
        json={"email": "admin@test.local"},
    ).json()

    monkeypatch.setattr(wa, "verify_authentication_response", lambda **kw: _FakeVerifiedAuth())
    resp = client.post(
        "/api/auth/webauthn/login/verify",
        headers=ORIGIN_HEADERS,
        json={"nonce": opts["nonce"], "credential": {"id": "x", "rawId": "x"}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]


def test_login_verify_rejects_stale_nonce(client):
    resp = client.post(
        "/api/auth/webauthn/login/verify",
        headers=ORIGIN_HEADERS,
        json={"nonce": "bogus", "credential": {}},
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_webauthn.py -v -k login`
Expected: FAIL — 404 (login endpoints not defined).

- [ ] **Step 3: Implement the login endpoints**

Append to `backend/app/routers/webauthn.py`:

```python
# --- Login (public) ---------------------------------------------------------
@router.post("/login/options")
def login_options(
    payload: WebAuthnLoginOptionsRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    rp_id, _origin = _rp_for_request(request)
    allow: list[PublicKeyCredentialDescriptor] = []
    if payload.email:
        email = payload.email.strip().lower()
        user = db.scalar(select(User).where(User.email == email))
        if user is not None:
            creds = db.scalars(
                select(WebAuthnCredential).where(
                    WebAuthnCredential.user_id == user.id,
                    WebAuthnCredential.rp_id == rp_id,
                )
            ).all()
            allow = [PublicKeyCredentialDescriptor(id=c.credential_id) for c in creds]
        # Unknown email → empty allow list, still return options (no enumeration).
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=allow or None,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    nonce = challenges.put(options.challenge, meta={"rp_id": rp_id})
    return {"nonce": nonce, "options": json.loads(options_to_json(options))}


@router.post("/login/verify", response_model=TokenResponse)
def login_verify(
    payload: WebAuthnLoginVerify,
    request: Request,
    db: Session = Depends(get_db),
) -> TokenResponse:
    rp_id, origin = _rp_for_request(request)
    try:
        entry = challenges.take(payload.nonce)
    except KeyError:
        raise HTTPException(status_code=400, detail="Anmeldung abgelaufen, bitte erneut.") from None
    if entry.meta.get("rp_id") != rp_id:
        raise HTTPException(status_code=400, detail="Origin passt nicht zur Anmeldung.")

    # Locate the credential by its raw id (base64url in the browser payload).
    raw_id = payload.credential.get("rawId") or payload.credential.get("id")
    if not raw_id:
        raise HTTPException(status_code=400, detail="Ungültige Anmeldedaten.")
    cred_id = webauthn_config.b64url_decode(raw_id)
    cred = db.scalar(
        select(WebAuthnCredential).where(
            WebAuthnCredential.credential_id == cred_id,
            WebAuthnCredential.rp_id == rp_id,
        )
    )
    if cred is None:
        raise HTTPException(status_code=400, detail="Passkey unbekannt.")
    user = db.get(User, cred.user_id)
    if user is None or not user.active:
        raise HTTPException(status_code=403, detail="Konto deaktiviert.")

    try:
        verified = verify_authentication_response(
            credential=payload.credential,
            expected_challenge=entry.challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=cred.public_key,
            credential_current_sign_count=cred.sign_count,
            require_user_verification=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Passkey-Prüfung fehlgeschlagen: {exc}") from exc

    # Clone detection: a non-increasing counter (when the authenticator uses one)
    # means a possible cloned credential.
    if verified.new_sign_count and verified.new_sign_count <= cred.sign_count:
        audit(db, user, "auth.webauthn.signcount_regression", f"cred={cred.id}")
        raise HTTPException(status_code=400, detail="Passkey-Zähler ungültig.")

    from datetime import UTC, datetime

    cred.sign_count = verified.new_sign_count
    cred.last_used_at = datetime.now(UTC)
    db.commit()
    token = create_access_token(user_id=user.id, role=user.role.value)
    return TokenResponse(access_token=token)
```

- [ ] **Step 4: Add the base64url helper used above**

Append to `backend/app/webauthn_config.py`:

```python
import base64


def b64url_decode(value: str) -> bytes:
    """Decode a base64url string (no padding needed) to bytes."""
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
```

- [ ] **Step 5: Run login tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_webauthn.py -v -k login`
Expected: PASS.

Note: the login-verify test locates the credential by `rawId`. The stub payload uses `"rawId": "x"` which base64url-decodes to a byte string that won't match `b"cred-123"`. **Adjust the test** so `rawId` encodes the registered id: in `test_login_verify_issues_token`, build the payload with the correct base64url of `b"cred-123"`. Update that test's `credential` to:

```python
    import base64
    raw = base64.urlsafe_b64encode(b"cred-123").rstrip(b"=").decode()
    resp = client.post(
        "/api/auth/webauthn/login/verify",
        headers=ORIGIN_HEADERS,
        json={"nonce": opts["nonce"], "credential": {"id": raw, "rawId": raw}},
    )
```

(Write the test this way from the start in Step 1 — this note documents the intent.)

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/webauthn.py backend/app/webauthn_config.py backend/tests/test_webauthn.py
git commit -m "feat(webauthn): login ceremony endpoints"
```

---

## Task 7: Credential management endpoints

**Files:**
- Modify: `backend/app/routers/webauthn.py`
- Test: `backend/tests/test_webauthn.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_webauthn.py`:

```python
def test_list_rename_delete_credentials(client, admin_auth, monkeypatch):
    _register_a_credential(client, admin_auth, monkeypatch)

    listed = client.get("/api/auth/webauthn/credentials", headers=admin_auth).json()
    assert len(listed) == 1
    cid = listed[0]["id"]

    renamed = client.patch(
        f"/api/auth/webauthn/credentials/{cid}",
        headers=admin_auth,
        json={"name": "Renamed Key"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed Key"

    deleted = client.delete(f"/api/auth/webauthn/credentials/{cid}", headers=admin_auth)
    assert deleted.status_code == 204
    assert client.get("/api/auth/webauthn/credentials", headers=admin_auth).json() == []


def test_credentials_require_auth(client):
    assert client.get("/api/auth/webauthn/credentials").status_code in (401, 403)


def test_cannot_delete_another_users_credential(client, admin_auth, monkeypatch):
    _register_a_credential(client, admin_auth, monkeypatch)
    listed = client.get("/api/auth/webauthn/credentials", headers=admin_auth).json()
    cid = listed[0]["id"]

    # Make a second user and log in as them.
    client.post(
        "/api/users",
        headers=admin_auth,
        json={"email": "other@test.local", "password": "otherpass123", "role": "user"},
    )
    token = client.post(
        "/api/auth/login", json={"email": "other@test.local", "password": "otherpass123"}
    ).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}

    assert client.delete(f"/api/auth/webauthn/credentials/{cid}", headers=other).status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_webauthn.py -v -k "credential or rename or delete"`
Expected: FAIL — 404/405 (endpoints not defined).

- [ ] **Step 3: Implement management endpoints**

Append to `backend/app/routers/webauthn.py`:

```python
from fastapi import Response


# --- Credential management (logged-in, own credentials) --------------------
@router.get("/credentials", response_model=list[PasskeyOut])
def list_credentials(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[WebAuthnCredential]:
    return list(
        db.scalars(
            select(WebAuthnCredential)
            .where(WebAuthnCredential.user_id == user.id)
            .order_by(WebAuthnCredential.created_at.desc())
        )
    )


def _own_credential(db: Session, user: User, cred_id: int) -> WebAuthnCredential:
    cred = db.get(WebAuthnCredential, cred_id)
    if cred is None or cred.user_id != user.id:
        raise HTTPException(status_code=404, detail="Passkey nicht gefunden.")
    return cred


@router.patch("/credentials/{cred_id}", response_model=PasskeyOut)
def rename_credential(
    cred_id: int,
    payload: PasskeyRename,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WebAuthnCredential:
    cred = _own_credential(db, user, cred_id)
    cred.name = payload.name.strip() or cred.name
    db.commit()
    db.refresh(cred)
    return cred


@router.delete("/credentials/{cred_id}", status_code=204)
def delete_credential(
    cred_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    cred = _own_credential(db, user, cred_id)
    audit(db, user, "auth.webauthn.delete", f"name={cred.name}")
    db.delete(cred)
    db.commit()
    return Response(status_code=204)
```

- [ ] **Step 4: Run the full backend webauthn suite**

Run: `cd backend && .venv/bin/python -m pytest tests/test_webauthn.py tests/test_webauthn_config.py -v`
Expected: PASS (all).

- [ ] **Step 5: Run the FULL backend suite (no regressions)**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/webauthn.py backend/tests/test_webauthn.py
git commit -m "feat(webauthn): credential management endpoints"
```

---

## Task 8: Frontend — webauthn.ts wrapper

**Files:**
- Create: `frontend/src/webauthn.ts`
- Modify: `frontend/src/api.ts` (Passkey interface + management calls)

- [ ] **Step 1: Add API types + management calls**

In `frontend/src/api.ts`, after the `SystemStatus`/`HttpsStatus` area, add:

```typescript
export interface Passkey {
  id: number;
  name: string;
  rp_id: string;
  created_at: string;
  last_used_at: string | null;
}

export async function listPasskeys(): Promise<Passkey[]> {
  return api<Passkey[]>("/api/auth/webauthn/credentials");
}

export async function renamePasskey(id: number, name: string): Promise<Passkey> {
  return api<Passkey>(`/api/auth/webauthn/credentials/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}

export async function deletePasskey(id: number): Promise<void> {
  await api<void>(`/api/auth/webauthn/credentials/${id}`, { method: "DELETE" });
}

/** True when this browser + origin can use passkeys (WebAuthn needs a secure
 * context: https, or http://localhost for dev). */
export function passkeysSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "PublicKeyCredential" in window &&
    (window.isSecureContext || window.location.hostname === "localhost")
  );
}
```

- [ ] **Step 2: Create the ceremony wrapper**

Create `frontend/src/webauthn.ts`:

```typescript
import { api, setToken } from "./api";

// WebAuthn exchanges ArrayBuffers; JSON needs base64url strings.
function b64urlToBuffer(value: string): ArrayBuffer {
  const pad = "=".repeat((4 - (value.length % 4)) % 4);
  const base64 = (value + pad).replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(base64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

function bufferToB64url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// Convert the server's PublicKeyCredentialCreationOptions JSON (base64url
// challenge/user.id/excludeCredentials.id) into the ArrayBuffer shapes the
// browser API requires.
function decodeCreationOptions(o: any): PublicKeyCredentialCreationOptions {
  return {
    ...o,
    challenge: b64urlToBuffer(o.challenge),
    user: { ...o.user, id: b64urlToBuffer(o.user.id) },
    excludeCredentials: (o.excludeCredentials ?? []).map((c: any) => ({
      ...c,
      id: b64urlToBuffer(c.id),
    })),
  };
}

function decodeRequestOptions(o: any): PublicKeyCredentialRequestOptions {
  return {
    ...o,
    challenge: b64urlToBuffer(o.challenge),
    allowCredentials: (o.allowCredentials ?? []).map((c: any) => ({
      ...c,
      id: b64urlToBuffer(c.id),
    })),
  };
}

function encodeAttestation(cred: PublicKeyCredential): any {
  const r = cred.response as AuthenticatorAttestationResponse;
  return {
    id: cred.id,
    rawId: bufferToB64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: bufferToB64url(r.clientDataJSON),
      attestationObject: bufferToB64url(r.attestationObject),
      transports: (r.getTransports?.() ?? []) as string[],
    },
  };
}

function encodeAssertion(cred: PublicKeyCredential): any {
  const r = cred.response as AuthenticatorAssertionResponse;
  return {
    id: cred.id,
    rawId: bufferToB64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: bufferToB64url(r.clientDataJSON),
      authenticatorData: bufferToB64url(r.authenticatorData),
      signature: bufferToB64url(r.signature),
      userHandle: r.userHandle ? bufferToB64url(r.userHandle) : null,
    },
  };
}

/** Register a new passkey for the logged-in user. */
export async function registerPasskey(name: string): Promise<void> {
  const { nonce, options } = await api<{ nonce: string; options: any }>(
    "/api/auth/webauthn/register/options",
    { method: "POST" },
  );
  const cred = (await navigator.credentials.create({
    publicKey: decodeCreationOptions(options.publicKey ?? options),
  })) as PublicKeyCredential;
  await api("/api/auth/webauthn/register/verify", {
    method: "POST",
    body: JSON.stringify({ nonce, credential: encodeAttestation(cred), name }),
  });
}

/** Log in with a passkey. email optional → one-click (discoverable). Sets the
 * token on success; caller then fetches /api/auth/me. */
export async function loginWithPasskey(email?: string): Promise<void> {
  const { nonce, options } = await api<{ nonce: string; options: any }>(
    "/api/auth/webauthn/login/options",
    { method: "POST", body: JSON.stringify({ email: email || null }) },
  );
  const cred = (await navigator.credentials.get({
    publicKey: decodeRequestOptions(options.publicKey ?? options),
  })) as PublicKeyCredential;
  const { access_token } = await api<{ access_token: string }>(
    "/api/auth/webauthn/login/verify",
    { method: "POST", body: JSON.stringify({ nonce, credential: encodeAssertion(cred) }) },
  );
  setToken(access_token);
}
```

Note: `options_to_json` from py_webauthn nests the fields under a top-level object; the server returns `{"options": <that json>}`. Depending on py_webauthn's exact shape, the create/get options may be the object itself (not under `.publicKey`). The `options.publicKey ?? options` guard handles both — verify the actual shape during the on-device test and simplify.

- [ ] **Step 3: Type-check**

Run: `cd frontend && npm run lint`
Expected: no TypeScript errors. (`getTransports` is optional-chained; DOM lib types cover `PublicKeyCredential`.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api.ts frontend/src/webauthn.ts
git commit -m "feat(webauthn): frontend browser-API wrapper + typed calls"
```

---

## Task 9: Frontend — login button + auth context

**Files:**
- Modify: `frontend/src/auth.tsx`
- Modify: `frontend/src/pages/Login.tsx`

- [ ] **Step 1: Add loginWithPasskey to the auth context**

In `frontend/src/auth.tsx`, extend the interface + provider:

Change the interface:

```typescript
interface AuthState {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  loginPasskey: (email?: string) => Promise<void>;
  logout: () => void;
}
```

Add the import at the top:

```typescript
import { loginWithPasskey } from "./webauthn";
```

Add the function inside `AuthProvider` (after `login`):

```typescript
  async function loginPasskey(email?: string) {
    await loginWithPasskey(email);
    setUser(await api<User>("/api/auth/me"));
  }
```

And include it in the context value:

```typescript
    <AuthContext.Provider value={{ user, loading, login, loginPasskey, logout }}>
```

- [ ] **Step 2: Add the passkey button to Login.tsx**

In `frontend/src/pages/Login.tsx`, add imports:

```typescript
import { ApiError, passkeysSupported } from "../api";
```

Pull `loginPasskey` from the hook:

```typescript
  const { login, loginPasskey } = useAuth();
```

Add a handler (after `submit`):

```typescript
  async function passkey() {
    setError(null);
    setBusy(true);
    try {
      await loginPasskey(email || undefined);
      navigate("/");
    } catch (err) {
      if (err instanceof DOMException && err.name === "NotAllowedError") {
        setError("Passkey-Anmeldung abgebrochen.");
      } else {
        setError(err instanceof ApiError ? err.message : "Passkey-Anmeldung fehlgeschlagen");
      }
    } finally {
      setBusy(false);
    }
  }
```

Add the button after the existing submit button (inside the form, but `type="button"` so it doesn't submit):

```tsx
        {passkeysSupported() && (
          <button
            type="button"
            onClick={passkey}
            disabled={busy}
            className="mt-3 w-full rounded-lg border border-white/15 py-2.5 font-semibold text-slate-200 transition hover:bg-white/5 disabled:opacity-50"
          >
            Mit Passkey anmelden
          </button>
        )}
```

- [ ] **Step 3: Type-check**

Run: `cd frontend && npm run lint`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/auth.tsx frontend/src/pages/Login.tsx
git commit -m "feat(webauthn): passkey login button + auth context"
```

---

## Task 10: Frontend — Passkeys settings section

**Files:**
- Create: `frontend/src/pages/Passkeys.tsx`
- Modify: the router file that defines app routes (find it — likely `frontend/src/App.tsx`) to add a `/passkeys` route, and add a nav link where the user's account/settings links live.

- [ ] **Step 1: Locate the router + nav**

Run: `cd frontend && grep -rn "createBrowserRouter\|<Routes>\|<Route " src | head`
Identify the routes file and how existing authenticated pages (e.g. System) are registered. Follow that exact pattern in Step 3.

- [ ] **Step 2: Create the Passkeys page**

Create `frontend/src/pages/Passkeys.tsx`:

```tsx
import { useEffect, useState } from "react";
import {
  ApiError,
  deletePasskey,
  listPasskeys,
  passkeysSupported,
  renamePasskey,
  type Passkey,
} from "../api";
import Layout from "../components/Layout";
import { useToast } from "../toast";
import { registerPasskey } from "../webauthn";

export default function Passkeys() {
  const toast = useToast();
  const [items, setItems] = useState<Passkey[]>([]);
  const [busy, setBusy] = useState(false);

  function load() {
    listPasskeys().then(setItems).catch(() => setItems([]));
  }
  useEffect(load, []);

  async function add() {
    const name = window.prompt("Name für diesen Passkey (z. B. iPhone)", "");
    if (name === null) return;
    setBusy(true);
    try {
      await registerPasskey(name || "Passkey");
      toast.info("Passkey", "Passkey hinzugefügt.");
      load();
    } catch (err) {
      if (err instanceof DOMException && err.name === "NotAllowedError") {
        toast.error("Passkey", "Registrierung abgebrochen.");
      } else {
        toast.error("Passkey", err instanceof ApiError ? err.message : "Fehlgeschlagen");
      }
    } finally {
      setBusy(false);
    }
  }

  async function rename(p: Passkey) {
    const name = window.prompt("Neuer Name", p.name);
    if (!name) return;
    await renamePasskey(p.id, name).then(load).catch(() => toast.error("Passkey", "Fehlgeschlagen"));
  }

  async function remove(p: Passkey) {
    if (!window.confirm(`Passkey „${p.name}" löschen?`)) return;
    await deletePasskey(p.id).then(load).catch(() => toast.error("Passkey", "Fehlgeschlagen"));
  }

  return (
    <Layout>
      <h2 className="mb-4 text-lg font-semibold">Passkeys</h2>
      {!passkeysSupported() && (
        <div className="mb-4 rounded-lg bg-slate-900/60 px-3 py-2 text-xs text-slate-400">
          Passkeys brauchen HTTPS (z. B. https://offgridcloud.local) oder localhost. Über eine
          nackte IP ohne HTTPS lassen sie sich nicht einrichten.
        </div>
      )}
      <button
        type="button"
        onClick={add}
        disabled={busy || !passkeysSupported()}
        className="mb-6 rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
      >
        {busy ? "…" : "Passkey hinzufügen"}
      </button>

      {items.length === 0 ? (
        <p className="text-sm text-slate-500">Noch keine Passkeys registriert.</p>
      ) : (
        <ul className="space-y-2">
          {items.map((p) => (
            <li
              key={p.id}
              className="flex items-center justify-between rounded-xl bg-slate-800/60 px-4 py-3 ring-1 ring-white/10"
            >
              <div>
                <div className="text-sm font-medium text-slate-200">{p.name}</div>
                <div className="text-xs text-slate-500">
                  {p.rp_id} · erstellt {new Date(p.created_at + "Z").toLocaleDateString()}
                  {p.last_used_at && ` · zuletzt ${new Date(p.last_used_at + "Z").toLocaleDateString()}`}
                </div>
              </div>
              <div className="flex gap-2">
                <button type="button" onClick={() => rename(p)} className="text-xs text-slate-400 hover:text-slate-200">
                  Umbenennen
                </button>
                <button type="button" onClick={() => remove(p)} className="text-xs text-red-400 hover:text-red-300">
                  Löschen
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Layout>
  );
}
```

- [ ] **Step 3: Wire the route + nav link**

Following the pattern found in Step 1, register `<Route path="/passkeys" element={<Passkeys />} />` (or the equivalent) and add a nav entry near the existing account/settings links. Match the exact router idiom already in the codebase.

- [ ] **Step 4: Type-check + build**

Run: `cd frontend && npm run lint && npm run build`
Expected: no errors; build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Passkeys.tsx frontend/src/App.tsx  # + any nav file touched
git commit -m "feat(webauthn): Passkeys settings page + route"
```

---

## Task 11: Docs

**Files:**
- Modify: `docs/BETRIEB.md` (add a short passkey subsection, e.g. under the auth/security area or a new numbered section)

- [ ] **Step 1: Add a passkey subsection**

Add to `docs/BETRIEB.md` (choose a sensible spot near the HTTPS/security sections):

```markdown
## Passkeys (WebAuthn)

Nutzer können sich zusätzlich zum Passwort per **Passkey** anmelden (Fingerabdruck,
Gesichtserkennung, Sicherheitsschlüssel). Voraussetzung ist HTTPS — also der per
Installer eingerichtete Zugang `https://offgridcloud.local` oder eine echte Domain
(siehe §3). Über eine nackte LAN-IP ohne HTTPS funktionieren Passkeys nicht.

- **Einrichten:** eingeloggt unter **Passkeys** → „Passkey hinzufügen".
- **Anmelden:** auf der Login-Seite „Mit Passkey anmelden" (mit ausgefüllter
  E-Mail gezielt, ohne E-Mail als Ein-Klick).
- **Zwei Zugänge:** Ein Passkey gilt nur für die Adresse, unter der er angelegt
  wurde. Wer sowohl lokal (`offgridcloud.local`) als auch über eine Domain
  zugreift, legt pro Adresse einen eigenen Passkey an.
- **Fallback:** Das Passwort bleibt immer gültig. Geht ein Gerät verloren, per
  Passwort anmelden und den Passkey unter **Passkeys** löschen; ein Admin kann
  zudem das Passwort zurücksetzen.
```

- [ ] **Step 2: Commit**

```bash
git add docs/BETRIEB.md
git commit -m "docs: passkey (WebAuthn) usage in BETRIEB.md"
```

---

## Final Verification

- [ ] **Backend suite green:** `cd backend && .venv/bin/python -m pytest -q` → all pass.
- [ ] **Frontend build:** `cd frontend && npm run lint && npm run build` → clean.
- [ ] **On-device E2E (manual, documented in the PR)** — needs HTTPS (`https://offgridcloud.local`) and either a real authenticator or Chrome DevTools → "WebAuthn" → enable a virtual authenticator (with "resident keys" + "user verification" on):
  1. Log in with password, open **Passkeys**, add a passkey → appears in the list.
  2. Log out. On the login page, click **Mit Passkey anmelden** with the email filled → logged in.
  3. Log out. Click **Mit Passkey anmelden** with the email empty (one-click / discoverable) → logged in.
  4. (If a domain is configured) access via the domain, register a second passkey → two entries, distinct `rp_id`.
  5. Rename and delete a passkey → list updates.
  6. Confirm password login still works.
- [ ] **Open the PR** with the manual E2E results in the description. (Base: `main`, after #57 is merged.)
```
