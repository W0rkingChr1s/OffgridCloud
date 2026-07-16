# Installer HTTPS Setup + Configurable mDNS Hostname — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a fresh OffgridCloud install reachable over HTTPS out of the box — self-signed `<hostname>.local` for offline/field use, plus an optional real Let's-Encrypt certificate when a public domain is configured — with the mDNS hostname chosen during install and the domain switchable later from the Settings UI.

**Architecture:** A new `deploy/https/` module (mirroring `deploy/vpn`, `deploy/netfallback`, `deploy/kiosk`) ships two scripts: `install.sh` (one-time: install Caddy + avahi, wire a NOPASSWD sudoers rule, seed config) and `apply.sh` (the idempotent workhorse that renders `/etc/caddy/Caddyfile`, validates it, reloads Caddy, sets the system hostname, and writes `data/https_state.json`). The FastAPI backend exposes an admin-only `GET/PUT /api/system/https` router that reads that state file and re-runs `apply.sh` via the sudoers rule — the exact pattern already used for one-click updates and power control. The frontend adds an `HttpsCard` to the System page. Both the LAN block and (when a domain is set) the public block are always present in the Caddyfile — there is no exclusive "mode" (design decision B).

**Tech Stack:** Bash (deploy scripts), Caddy (reverse proxy / auto-TLS), avahi (mDNS), FastAPI + Pydantic + SQLAlchemy (backend), pytest (backend tests), React + TypeScript + Tailwind (frontend), Vite.

**Spec:** `docs/superpowers/specs/2026-07-16-installer-https-design.md`

**Branch:** `claude/installer-https-setup` (already created; the spec is committed there).

---

## File Structure

**New files:**
- `deploy/https/apply.sh` — renders the Caddyfile, validates, reloads Caddy, sets hostname, writes state. Called by both `install.sh` and the backend.
- `deploy/https/install.sh` — installs Caddy + avahi, wires the sudoers rule + `.env`, runs `apply.sh` once.
- `backend/app/https_config.py` — pure helpers: read `https_state.json`, validate hostname/domain, run the apply command. Kept separate from the router so it's unit-testable without HTTP (mirrors `power.py`).
- `backend/app/routers/https.py` — `GET/PUT /api/system/https` (admin-only, own file like `updates.py`).
- `backend/tests/test_https.py` — helper + endpoint tests (mirrors `test_power.py`).

**Modified files:**
- `backend/app/config.py` — add `https_apply_command: str = ""`.
- `backend/app/schemas.py` — add `HttpsStatusOut`, `HttpsConfigUpdate`.
- `backend/app/main.py` — register the new router.
- `deploy/install.sh` — new interactive questions + call `deploy/https/install.sh`.
- `frontend/src/api.ts` — add `HttpsStatus` interface + `getHttpsStatus()` / `updateHttps()`.
- `frontend/src/pages/System.tsx` — add `HttpsCard`, render it after `PowerCard`.
- `deploy/Caddyfile` — update placeholder `offgrid.local` → `offgridcloud.local`.
- `deploy/nginx.conf.example` — update placeholder `offgrid.local` → `offgridcloud.local`.
- `docs/BETRIEB.md` — rewrite §3 (no more hand-editing).

**Note on ordering:** Backend tasks (1–5) come first because they're TDD-able in isolation. The bash scripts (6–8) are verified manually. Frontend (9) last. Docs (10) at the end.

---

## Task 1: Add `https_apply_command` setting

**Files:**
- Modify: `backend/app/config.py:93` (after the `net_apply_command` block)
- Test: `backend/tests/test_https.py` (new)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_https.py`:

```python
"""HTTPS reverse-proxy config: helpers + endpoints (self-signed LAN + optional domain)."""

from __future__ import annotations

import pytest

from app.config import Settings


def test_https_apply_command_defaults_empty():
    # Empty by default → feature counts as "not set up" (button hidden / 409),
    # exactly like restart_service_command et al. before the installer wires it.
    assert Settings().https_apply_command == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_https.py::test_https_apply_command_defaults_empty -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'https_apply_command'`

- [ ] **Step 3: Add the setting**

In `backend/app/config.py`, immediately after the `net_apply_command: str = ""` line (currently line 93), add:

```python

    # --- HTTPS reverse proxy (Caddy) ---------------------------------------
    # Opt-in privileged command that re-renders the Caddyfile + sets the mDNS
    # hostname (see deploy/https/apply.sh). Wired up by deploy/https/install.sh
    # via a NOPASSWD sudoers rule; empty when HTTPS wasn't set up, so the System
    # UI shows the feature as unavailable and PUT /api/system/https returns 409.
    https_apply_command: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_https.py::test_https_apply_command_defaults_empty -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/tests/test_https.py
git commit -m "feat(config): add https_apply_command setting"
```

---

## Task 2: Hostname/domain validation + state reader helpers

**Files:**
- Create: `backend/app/https_config.py`
- Test: `backend/tests/test_https.py`

These are pure functions: normalise/validate a hostname and domain, and read the JSON state file `apply.sh` writes. No subprocess yet (that's Task 3).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_https.py`:

```python
from pathlib import Path

from app import https_config


def test_normalise_hostname_strips_local_suffix_and_lowercases():
    assert https_config.normalise_hostname("OffgridCloud.local") == "offgridcloud"
    assert https_config.normalise_hostname("  box1  ") == "box1"


@pytest.mark.parametrize("bad", ["", "   ", "has space", "under_score", "-lead", "trail-", "a" * 64])
def test_validate_hostname_rejects_bad(bad):
    with pytest.raises(ValueError):
        https_config.validate_hostname(https_config.normalise_hostname(bad))


def test_validate_hostname_accepts_good():
    assert https_config.validate_hostname("offgridcloud") == "offgridcloud"
    assert https_config.validate_hostname("box-1") == "box-1"


@pytest.mark.parametrize("bad", ["no dots", "-lead.com", "http://x.com", "a..b.com", "space .com"])
def test_validate_domain_rejects_bad(bad):
    with pytest.raises(ValueError):
        https_config.validate_domain(bad)


def test_validate_domain_accepts_good_and_empty():
    # Empty domain is valid → "no public domain, LAN only".
    assert https_config.validate_domain("") == ""
    assert https_config.validate_domain("  Cloud.Example.COM ") == "cloud.example.com"


def test_read_state_missing_file_returns_defaults(tmp_path: Path):
    state = https_config.read_state(tmp_path)
    assert state == {"hostname": "", "domain": ""}


def test_read_state_reads_written_file(tmp_path: Path):
    (tmp_path / "https_state.json").write_text('{"hostname": "box1", "domain": "x.com"}')
    assert https_config.read_state(tmp_path) == {"hostname": "box1", "domain": "x.com"}


def test_read_state_tolerates_garbage(tmp_path: Path):
    (tmp_path / "https_state.json").write_text("not json{")
    assert https_config.read_state(tmp_path) == {"hostname": "", "domain": ""}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_https.py -v -k "hostname or domain or read_state"`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.https_config'`

- [ ] **Step 3: Implement the helpers**

Create `backend/app/https_config.py`:

```python
"""HTTPS reverse-proxy config helpers.

Pure logic (validation + reading the state file that ``deploy/https/apply.sh``
writes) kept separate from the FastAPI router so it's unit-testable without
HTTP — same split as ``power.py`` vs. ``routers/system.py``. The privileged
work (rendering the Caddyfile, reloading Caddy, setting the hostname) lives in
the bash script; Python only validates input and shells out to it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_STATE_FILENAME = "https_state.json"

# mDNS short name: DNS label rules — letters/digits/hyphen, no leading/trailing
# hyphen, 1–63 chars. Avahi appends ".local" itself.
_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
# Public domain: one or more dot-separated DNS labels (needs at least one dot).
_DOMAIN_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN_RE = re.compile(rf"^{_DOMAIN_LABEL}(\.{_DOMAIN_LABEL})+$")


def normalise_hostname(value: str) -> str:
    """Lowercase, trim, and strip a trailing ``.local`` (avahi adds it back)."""
    cleaned = value.strip().lower()
    if cleaned.endswith(".local"):
        cleaned = cleaned[: -len(".local")]
    return cleaned


def validate_hostname(value: str) -> str:
    """Return ``value`` if it's a valid mDNS short name, else raise ValueError."""
    if not _HOSTNAME_RE.match(value):
        raise ValueError(
            "Ungültiger Hostname. Erlaubt: Buchstaben, Ziffern und Bindestriche "
            "(kein Bindestrich am Anfang/Ende), 1–63 Zeichen."
        )
    return value


def validate_domain(value: str) -> str:
    """Normalise + validate a public domain. Empty string means 'no domain'."""
    cleaned = value.strip().lower()
    if cleaned == "":
        return ""
    if not _DOMAIN_RE.match(cleaned):
        raise ValueError(
            "Ungültige Domain. Erwartet z. B. cloud.example.com "
            "(ohne http://, mindestens ein Punkt)."
        )
    return cleaned


def read_state(data_dir: Path) -> dict[str, str]:
    """Read ``<data_dir>/https_state.json`` written by apply.sh.

    Missing or unreadable → defaults, so the endpoint never fails hard on a box
    where HTTPS was never set up.
    """
    path = Path(data_dir) / _STATE_FILENAME
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"hostname": "", "domain": ""}
    return {
        "hostname": str(raw.get("hostname", "")),
        "domain": str(raw.get("domain", "")),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_https.py -v -k "hostname or domain or read_state"`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add backend/app/https_config.py backend/tests/test_https.py
git commit -m "feat(https): hostname/domain validation + state reader"
```

---

## Task 3: Apply-command runner (subprocess, injectable)

**Files:**
- Modify: `backend/app/https_config.py`
- Test: `backend/tests/test_https.py`

Add a function that shells out to the configured `apply.sh` command with `--hostname`/`--domain`, captures output, and raises with the stderr tail on failure. The subprocess runner is injectable so tests never spawn a real process (mirrors `power.py`'s `popen=` seam).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_https.py`:

```python
def test_run_apply_builds_command_and_succeeds():
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs

        class R:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return R()

    https_config.run_apply(
        "sudo /opt/offgridcloud/deploy/https/apply.sh",
        hostname="box1",
        domain="cloud.example.com",
        run=fake_run,
    )

    # The command string is split (trusted, operator-configured) and the two
    # flags appended. Domain passed through because it's non-empty.
    assert captured["argv"] == [
        "sudo",
        "/opt/offgridcloud/deploy/https/apply.sh",
        "--hostname",
        "box1",
        "--domain",
        "cloud.example.com",
    ]
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["timeout"] == 30


def test_run_apply_omits_domain_flag_when_empty():
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    https_config.run_apply("sudo apply.sh", hostname="box1", domain="", run=fake_run)
    assert "--domain" not in captured["argv"]
    assert captured["argv"] == ["sudo", "apply.sh", "--hostname", "box1"]


def test_run_apply_raises_with_stderr_tail_on_failure():
    def fake_run(argv, **kwargs):
        class R:
            returncode = 1
            stdout = ""
            stderr = "caddy validate failed: bad domain\n"

        return R()

    with pytest.raises(RuntimeError) as exc:
        https_config.run_apply("sudo apply.sh", hostname="box1", domain="", run=fake_run)
    assert "caddy validate failed" in str(exc.value)


def test_run_apply_rejects_empty_command():
    with pytest.raises(ValueError):
        https_config.run_apply("   ", hostname="box1", domain="")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_https.py -v -k run_apply`
Expected: FAIL — `AttributeError: module 'app.https_config' has no attribute 'run_apply'`

- [ ] **Step 3: Implement `run_apply`**

Add to the top imports of `backend/app/https_config.py`:

```python
import shlex
import subprocess
```

Then append to `backend/app/https_config.py`:

```python
def run_apply(command: str, *, hostname: str, domain: str, run=subprocess.run) -> str:
    """Run the configured apply command with --hostname (and --domain if set).

    ``command`` is an operator-configured value from the .env (trusted, never
    user input) — we split it with shlex and append the validated flags. Returns
    stdout on success; raises RuntimeError with the stderr tail on a non-zero
    exit so the endpoint can surface *why* it failed. ``run`` is injectable for
    tests (mirrors power.run_power_command's ``popen`` seam).
    """
    if not command.strip():
        raise ValueError("empty https apply command")
    argv = [*shlex.split(command), "--hostname", hostname]
    if domain:
        argv += ["--domain", domain]
    result = run(argv, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip()[-500:]
        raise RuntimeError(tail or f"apply.sh exited with {result.returncode}")
    return result.stdout
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_https.py -v -k run_apply`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add backend/app/https_config.py backend/tests/test_https.py
git commit -m "feat(https): injectable apply-command runner"
```

---

## Task 4: Schemas for the HTTPS endpoint

**Files:**
- Modify: `backend/app/schemas.py` (append near the other `*Out`/`*Update` classes, e.g. after `UpdateProgressOut` around line 568)

- [ ] **Step 1: Add the schemas**

Append to `backend/app/schemas.py` after the `UpdateProgressOut` class:

```python
class HttpsStatusOut(BaseModel):
    """Current reverse-proxy state, read from data/https_state.json."""

    enabled: bool  # True when https_apply_command is wired up on this box
    hostname: str  # mDNS short name, reachable as <hostname>.local
    domain: str  # public domain, or "" if none
    lan_url: str  # e.g. https://offgridcloud.local
    public_url: str  # e.g. https://cloud.example.com, or "" if no domain


class HttpsConfigUpdate(BaseModel):
    """Patch semantics: only provided fields change. domain="" removes it."""

    hostname: str | None = None
    domain: str | None = None
```

- [ ] **Step 2: Verify it imports**

Run: `cd backend && python -c "from app.schemas import HttpsStatusOut, HttpsConfigUpdate; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/app/schemas.py
git commit -m "feat(https): request/response schemas"
```

---

## Task 5: HTTPS router (`GET`/`PUT /api/system/https`)

**Files:**
- Create: `backend/app/routers/https.py`
- Modify: `backend/app/main.py:29-46` (import) and `:104` (register)
- Test: `backend/tests/test_https.py`

- [ ] **Step 1: Write the failing endpoint tests**

Append to `backend/tests/test_https.py`:

```python
def test_https_status_disabled_by_default(client, admin_auth):
    # No https_apply_command configured in the test settings → enabled False.
    body = client.get("/api/system/https", headers=admin_auth).json()
    assert body["enabled"] is False
    assert body["hostname"] == ""
    assert body["lan_url"] == ""


def test_https_status_requires_admin(client, admin_auth):
    # No user_auth fixture exists — create a plain user inline (pattern from
    # test_users.test_non_admin_cannot_manage_users).
    client.post(
        "/api/users",
        headers=admin_auth,
        json={"email": "plain@test.local", "password": "userpass123", "role": "user"},
    )
    token = client.post(
        "/api/auth/login", json={"email": "plain@test.local", "password": "userpass123"}
    ).json()["access_token"]
    user_auth = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/system/https", headers=user_auth).status_code == 403


def test_https_put_returns_409_when_not_configured(client, admin_auth):
    resp = client.put("/api/system/https", headers=admin_auth, json={"hostname": "box1"})
    assert resp.status_code == 409
    assert "Installer" in resp.json()["detail"]


def test_https_status_reports_urls_from_state(client, admin_auth, monkeypatch):
    import app.routers.https as https_router

    monkeypatch.setattr(
        https_router.https_config,
        "read_state",
        lambda data_dir: {"hostname": "box1", "domain": "cloud.example.com"},
    )
    body = client.get("/api/system/https", headers=admin_auth).json()
    assert body["hostname"] == "box1"
    assert body["lan_url"] == "https://box1.local"
    assert body["public_url"] == "https://cloud.example.com"


def test_https_put_runs_apply_and_returns_new_state(client, admin_auth, monkeypatch):
    import app.routers.https as https_router
    from app.config import get_settings

    # Pretend the box is wired up.
    settings = get_settings()
    monkeypatch.setattr(settings, "https_apply_command", "sudo apply.sh", raising=False)

    calls = {}

    def fake_run_apply(command, *, hostname, domain, run=None):
        calls["hostname"] = hostname
        calls["domain"] = domain
        return "ok"

    monkeypatch.setattr(https_router.https_config, "run_apply", fake_run_apply)
    monkeypatch.setattr(
        https_router.https_config,
        "read_state",
        lambda data_dir: {"hostname": "box2", "domain": ""},
    )

    resp = client.put(
        "/api/system/https", headers=admin_auth, json={"hostname": "Box2.local", "domain": ""}
    )
    assert resp.status_code == 200
    # Hostname was normalised (.local stripped, lowercased) before apply.
    assert calls["hostname"] == "box2"
    assert calls["domain"] == ""
    assert resp.json()["hostname"] == "box2"


def test_https_put_rejects_bad_hostname(client, admin_auth, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "https_apply_command", "sudo apply.sh", raising=False)
    resp = client.put(
        "/api/system/https", headers=admin_auth, json={"hostname": "bad host!"}
    )
    assert resp.status_code == 422 or resp.status_code == 400
```

Note on the last test: our helper raises `ValueError`; the router converts it to a `400`. (FastAPI body-shape errors are 422, but here the field is a plain `str`, so the 400 branch is what fires — the `or` keeps the test robust.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_https.py -v -k "status or put"`
Expected: FAIL — 404s (route not registered yet).

- [ ] **Step 3: Implement the router**

Create `backend/app/routers/https.py`:

```python
"""HTTPS reverse-proxy config — admin only.

Reads the state ``deploy/https/apply.sh`` writes and, on PUT, re-runs that
script (via the NOPASSWD sudoers rule the installer sets up) to re-render the
Caddyfile and set the mDNS hostname. Same opt-in shape as updates/power: an
empty ``https_apply_command`` means "not set up" → the feature reports disabled
and PUT returns 409.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import https_config
from ..admin_ops import audit
from ..config import get_settings
from ..db import get_db
from ..deps import require_admin
from ..models import User
from ..schemas import HttpsConfigUpdate, HttpsStatusOut

router = APIRouter(prefix="/api/system/https", tags=["https"], dependencies=[Depends(require_admin)])


def _status() -> HttpsStatusOut:
    settings = get_settings()
    state = https_config.read_state(settings.data_dir)
    enabled = bool(settings.https_apply_command.strip())
    hostname = state["hostname"]
    domain = state["domain"]
    return HttpsStatusOut(
        enabled=enabled,
        hostname=hostname,
        domain=domain,
        lan_url=f"https://{hostname}.local" if hostname else "",
        public_url=f"https://{domain}" if domain else "",
    )


@router.get("", response_model=HttpsStatusOut)
def get_https(_: User = Depends(require_admin)) -> HttpsStatusOut:
    return _status()


@router.put("", response_model=HttpsStatusOut)
def update_https(
    payload: HttpsConfigUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> HttpsStatusOut:
    settings = get_settings()
    command = settings.https_apply_command.strip()
    if not command:
        raise HTTPException(
            status_code=409,
            detail=(
                "HTTPS ist nicht eingerichtet. Den Installer erneut ausführen und "
                "HTTPS aktivieren, um die Caddy-Konfiguration und sudoers-Regel anzulegen."
            ),
        )

    current = https_config.read_state(settings.data_dir)
    # Patch semantics: fall back to the current value when a field is omitted.
    raw_hostname = payload.hostname if payload.hostname is not None else current["hostname"]
    raw_domain = payload.domain if payload.domain is not None else current["domain"]

    try:
        hostname = https_config.validate_hostname(
            https_config.normalise_hostname(raw_hostname)
        )
        domain = https_config.validate_domain(raw_domain)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        https_config.run_apply(command, hostname=hostname, domain=domain)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=f"apply.sh fehlgeschlagen: {exc}") from exc

    audit(db, admin, "system.https.update", f"hostname={hostname} domain={domain or '—'}")
    return _status()
```

- [ ] **Step 4: Register the router**

In `backend/app/main.py`, add `https` to the router import block (line 29-46, keep alphabetical-ish with the existing list — insert after `groups,`):

```python
    groups,
    https,
    media,
```

Then after `app.include_router(system.router)` (line 98), add:

```python
app.include_router(https.router)
```

- [ ] **Step 5: Run the whole test file**

Run: `cd backend && python -m pytest tests/test_https.py -v`
Expected: PASS (all)

- [ ] **Step 6: Run the full backend suite (no regressions)**

Run: `cd backend && python -m pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/https.py backend/app/main.py backend/tests/test_https.py
git commit -m "feat(https): GET/PUT /api/system/https router"
```

---

## Task 6: `deploy/https/apply.sh`

**Files:**
- Create: `deploy/https/apply.sh`

This is the workhorse. No unit-test harness for bash in the repo — verify manually (Step 4). Keep it defensive: validate, render to a temp file, `caddy validate`, atomic move, reload, set hostname only if changed, write state.

- [ ] **Step 1: Write the script**

Create `deploy/https/apply.sh`:

```bash
#!/usr/bin/env bash
# Render the Caddy reverse-proxy config for OffgridCloud and (re)apply it.
#
# Always emits a LAN block for <hostname>.local with a self-signed cert
# (Caddy 'tls internal'). If --domain is given, ALSO emits a public block that
# gets a real Let's Encrypt certificate automatically. Both coexist — LAN access
# never depends on the domain (design decision B).
#
# Idempotent: run it again with the same values and nothing changes. Called by
# deploy/https/install.sh at install time and by the backend (PUT
# /api/system/https) when the operator sets/clears the domain from the UI.
#
# Usage:
#   sudo ./deploy/https/apply.sh --hostname NAME [--domain DOMAIN] [--prefix DIR]
set -euo pipefail

HOSTNAME_SHORT=""
DOMAIN=""
PREFIX="${OGC_PREFIX:-/opt/offgridcloud}"
CADDYFILE="/etc/caddy/Caddyfile"
BACKEND_PORT="${OGC_PORT:-8000}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hostname) HOSTNAME_SHORT="${2:?}"; shift 2 ;;
    --domain)   DOMAIN="${2:-}"; shift 2 ;;
    --prefix)   PREFIX="${2:?}"; shift 2 ;;
    --port)     BACKEND_PORT="${2:?}"; shift 2 ;;
    -h|--help)  sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then echo "Please run as root (sudo)." >&2; exit 1; fi

# Normalise: strip a trailing .local, lowercase. (The backend also validates,
# but apply.sh is called directly by the installer too.)
HOSTNAME_SHORT="$(printf '%s' "$HOSTNAME_SHORT" | tr '[:upper:]' '[:lower:]')"
HOSTNAME_SHORT="${HOSTNAME_SHORT%.local}"
if [[ ! "$HOSTNAME_SHORT" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]]; then
  echo "Invalid hostname: '$HOSTNAME_SHORT'" >&2; exit 2
fi
if [[ -n "$DOMAIN" && ! "$DOMAIN" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$ ]]; then
  echo "Invalid domain: '$DOMAIN'" >&2; exit 2
fi

step() { printf '\n\033[1;36m>> %s\033[0m\n' "$1"; }

# --- 1. Render the Caddyfile to a temp file --------------------------------
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

# Reusable proxy body: SSE-friendly (Caddy does not buffer, so /api/events just
# works) and no upload size cap.
render_site() {  # render_site <address> <extra-tls-line>
  cat <<SITE
$1 {
    $2
    reverse_proxy localhost:${BACKEND_PORT}
    request_body {
        max_size 0
    }
}
SITE
}

{
  echo "# Managed by deploy/https/apply.sh — edits are overwritten. Change the"
  echo "# hostname/domain via the installer or System → HTTPS in the web UI."
  echo
  render_site "${HOSTNAME_SHORT}.local" "tls internal"
  if [[ -n "$DOMAIN" ]]; then
    echo
    render_site "$DOMAIN" ""
  fi
} > "$TMP"

# --- 2. Validate BEFORE touching the live config ---------------------------
step "Validating Caddy config..."
if ! caddy validate --config "$TMP" --adapter caddyfile; then
  echo "   Caddy config invalid — keeping the existing $CADDYFILE unchanged." >&2
  exit 1
fi

# --- 3. Atomic swap + reload -----------------------------------------------
mkdir -p "$(dirname "$CADDYFILE")"
install -m 644 "$TMP" "$CADDYFILE"
step "Reloading Caddy..."
systemctl reload caddy 2>/dev/null || systemctl restart caddy

# --- 4. Set the system hostname (only if it changed) -----------------------
CURRENT_HOST="$(hostnamectl --static 2>/dev/null || cat /etc/hostname 2>/dev/null || echo "")"
if [[ "$CURRENT_HOST" != "$HOSTNAME_SHORT" ]]; then
  step "Setting hostname to '$HOSTNAME_SHORT'..."
  hostnamectl set-hostname "$HOSTNAME_SHORT"
  # Keep /etc/hosts in sync so sudo doesn't warn about an unresolvable host.
  if grep -qE '^\s*127\.0\.1\.1' /etc/hosts; then
    sed -i -E "s/^\s*127\.0\.1\.1.*/127.0.1.1\t${HOSTNAME_SHORT}/" /etc/hosts
  else
    printf '127.0.1.1\t%s\n' "$HOSTNAME_SHORT" >> /etc/hosts
  fi
  systemctl restart avahi-daemon 2>/dev/null || true
fi

# --- 5. Persist state for the backend to read ------------------------------
STATE_DIR="$PREFIX/data"
mkdir -p "$STATE_DIR"
printf '{"hostname": "%s", "domain": "%s"}\n' "$HOSTNAME_SHORT" "$DOMAIN" \
  > "$STATE_DIR/https_state.json"

step "Done. LAN: https://${HOSTNAME_SHORT}.local${DOMAIN:+  Public: https://$DOMAIN}"
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x deploy/https/apply.sh
```

- [ ] **Step 3: Shellcheck (if available) + syntax check**

Run: `bash -n deploy/https/apply.sh && command -v shellcheck >/dev/null && shellcheck deploy/https/apply.sh || echo "shellcheck not installed — syntax ok"`
Expected: no syntax errors.

- [ ] **Step 4: Manual verification (documented, run on a Pi/VM with Caddy installed)**

This step is a manual checklist — record the outcome in the commit message or PR:
1. `sudo OGC_PREFIX=/tmp/ogc ./deploy/https/apply.sh --hostname testbox` → renders `/etc/caddy/Caddyfile` with only a `testbox.local` block, `caddy validate` passes, `/tmp/ogc/data/https_state.json` shows `{"hostname":"testbox","domain":""}`.
2. Re-run with `--domain cloud.example.com` → Caddyfile now has two blocks; state file updated.
3. `sudo ./deploy/https/apply.sh --hostname 'bad host'` → exits non-zero, existing Caddyfile untouched.

- [ ] **Step 5: Commit**

```bash
git add deploy/https/apply.sh
git commit -m "feat(https): apply.sh renders Caddyfile + sets hostname"
```

---

## Task 7: `deploy/https/install.sh`

**Files:**
- Create: `deploy/https/install.sh`

Installs Caddy + avahi, wires the sudoers rule and `.env`, runs `apply.sh` once. Mirrors `deploy/netfallback/install.sh` structure.

- [ ] **Step 1: Write the script**

Create `deploy/https/install.sh`:

```bash
#!/usr/bin/env bash
# Set up HTTPS for OffgridCloud (Caddy reverse proxy + mDNS hostname).
#
# Opt-in, root-only. It:
#   1. installs Caddy (official apt repo) and avahi-daemon (mDNS),
#   2. renders the Caddyfile + sets the hostname via apply.sh,
#   3. grants the service user a NOPASSWD sudoers rule for apply.sh only, and
#   4. wires OGC_HTTPS_APPLY_COMMAND into .env so "System → HTTPS" in the UI works.
#
# Usage:
#   sudo ./deploy/https/install.sh --prefix DIR --hostname NAME [--domain DOMAIN]
#                                  [--service-user USER] [--port PORT]
set -euo pipefail

PREFIX="/opt/offgridcloud"
SERVICE_USER="offgrid"
HOSTNAME_SHORT="offgridcloud"
DOMAIN=""
PORT="8000"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="${2:?}"; shift 2 ;;
    --hostname) HOSTNAME_SHORT="${2:?}"; shift 2 ;;
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --service-user) SERVICE_USER="${2:?}"; shift 2 ;;
    --port) PORT="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then echo "Please run as root (sudo)." >&2; exit 1; fi

step() { printf '\n\033[1;36m>> %s\033[0m\n' "$1"; }
APPLY="$HERE/apply.sh"
chmod +x "$APPLY" 2>/dev/null || true

# --- 1. Caddy + avahi -------------------------------------------------------
step "Installing Caddy + avahi (mDNS)..."
if command -v apt-get >/dev/null 2>&1; then
  apt-get install -y --no-install-recommends avahi-daemon avahi-utils || \
    echo "   Could not install avahi — <hostname>.local may not resolve." >&2
  if ! command -v caddy >/dev/null 2>&1; then
    # Official Caddy apt repo (stable). Best-effort: on failure, point the
    # operator at the manual install and bail out of the HTTPS setup only.
    apt-get install -y --no-install-recommends debian-keyring debian-archive-keyring apt-transport-https curl gnupg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
      | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
      | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    apt-get update
    apt-get install -y caddy || {
      echo "   Caddy install failed. Install it manually (https://caddyserver.com/docs/install)" >&2
      echo "   then re-run this script." >&2
      exit 1
    }
  else
    echo "   caddy already present: $(caddy version 2>/dev/null | head -1)"
  fi
else
  echo "   No apt-get here — install caddy + avahi-daemon manually, then re-run." >&2
  exit 1
fi
systemctl enable --now caddy 2>/dev/null || true

# --- 2. Render config + set hostname ---------------------------------------
step "Applying HTTPS config..."
APPLY_ARGS=(--hostname "$HOSTNAME_SHORT" --prefix "$PREFIX" --port "$PORT")
[[ -n "$DOMAIN" ]] && APPLY_ARGS+=(--domain "$DOMAIN")
OGC_PREFIX="$PREFIX" OGC_PORT="$PORT" bash "$APPLY" "${APPLY_ARGS[@]}"

# --- 3. NOPASSWD sudoers rule for apply.sh only ----------------------------
step "Granting the service user permission to re-apply HTTPS config..."
SUDOERS=/etc/sudoers.d/offgridcloud-https
echo "$SERVICE_USER ALL=(root) NOPASSWD: $APPLY" > "$SUDOERS.tmp"
if visudo -cf "$SUDOERS.tmp" >/dev/null 2>&1; then
  install -m 440 "$SUDOERS.tmp" "$SUDOERS"; rm -f "$SUDOERS.tmp"
  echo "   HTTPS config is now changeable from System → HTTPS in the web UI."
else
  rm -f "$SUDOERS.tmp"
  echo "   Could not validate sudoers rule — skipped. Domain changes then need" >&2
  echo "   'sudo $APPLY --hostname … --domain …' on the box." >&2
fi

# --- 4. Wire OGC_HTTPS_APPLY_COMMAND into .env -----------------------------
ENV_FILE="$PREFIX/.env"
if [[ -f "$ENV_FILE" ]]; then
  grep -q '^OGC_HTTPS_APPLY_COMMAND=' "$ENV_FILE" || \
    echo "OGC_HTTPS_APPLY_COMMAND=sudo $APPLY" >> "$ENV_FILE"
  chown "$SERVICE_USER:$SERVICE_USER" "$ENV_FILE" 2>/dev/null || true
fi

step "HTTPS ready. LAN: https://${HOSTNAME_SHORT}.local${DOMAIN:+  Public: https://$DOMAIN}"
```

- [ ] **Step 2: Make executable + syntax check**

```bash
chmod +x deploy/https/install.sh
bash -n deploy/https/install.sh && echo "syntax ok"
```

Expected: `syntax ok`

- [ ] **Step 3: Commit**

```bash
git add deploy/https/install.sh
git commit -m "feat(https): install.sh installs Caddy + avahi, wires apply.sh"
```

---

## Task 8: Wire HTTPS into the main installer

**Files:**
- Modify: `deploy/install.sh` — detection block (~line 44-58), defaults block (~line 60-73), the questionnaire (~line 138-165), the summary (~line 175-195), and a new module-call block near the VPN/kiosk calls (~line 340+).

- [ ] **Step 1: Add detection + defaults**

In `deploy/install.sh`, in the detection block (after the `_has_chrome=...` lines, around line 46), add:

```bash
_has_https=0;    [[ -f /etc/caddy/Caddyfile ]] && _has_https=1
# Carry the existing hostname/domain so a re-run pre-fills them.
_det_hostname="offgridcloud"
_det_domain=""
if [[ -f "$PREFIX/data/https_state.json" ]]; then
  _v="$(sed -n 's/.*"hostname"[: ]*"\([^"]*\)".*/\1/p' "$PREFIX/data/https_state.json" | head -1)"; [[ -n "$_v" ]] && _det_hostname="$_v"
  _v="$(sed -n 's/.*"domain"[: ]*"\([^"]*\)".*/\1/p' "$PREFIX/data/https_state.json" | head -1)"; [[ -n "$_v" ]] && _det_domain="$_v"
fi
```

Then in the defaults block (after `WITH_SPEEDTEST=...`, around line 73), add:

```bash
# Recommended-on per the design: default 1 whether or not it's already set up.
WITH_HTTPS="${OGC_WITH_HTTPS:-1}"
HTTPS_HOSTNAME="${OGC_HTTPS_HOSTNAME:-$_det_hostname}"
HTTPS_DOMAIN="${OGC_HTTPS_DOMAIN:-$_det_domain}"
```

Note: `_has_https` from Step 1's detection block isn't consumed by any later step — it's optional. Keep it only if you want a "wird aktualisiert" hint later; otherwise you may drop that one line. `_det_hostname`/`_det_domain` ARE used (they pre-fill the questions on a re-run).

- [ ] **Step 2: Document the new env vars in the header comment**

In the header comment block of `deploy/install.sh` (around line 22, near the other `OGC_WITH_*` vars), add a line:

```bash
#   OGC_WITH_HTTPS=1                 OGC_HTTPS_HOSTNAME=offgridcloud   OGC_HTTPS_DOMAIN=
```

- [ ] **Step 3: Add the questions**

In the questionnaire, after the `ask_yn WITH_SPEEDTEST ...` line (around line 143), add:

```bash
ask_yn     WITH_HTTPS          "HTTPS aktivieren (empfohlen, Zugriff per https://<name>.local)?" "$WITH_HTTPS"
if [[ "$WITH_HTTPS" -eq 1 ]]; then
  ask      HTTPS_HOSTNAME      "  … mDNS-Hostname (erreichbar als <name>.local)"            "$HTTPS_HOSTNAME"
  ask      HTTPS_DOMAIN        "  … öffentliche Domain (leer lassen, falls keine)"          "$HTTPS_DOMAIN"
fi
```

- [ ] **Step 4: Add to the summary**

In the summary here-doc (around line 178, after the Speedtest line), add:

```bash
    HTTPS ................ $(yesno "$WITH_HTTPS")$([[ "$WITH_HTTPS" -eq 1 ]] && echo " ($HTTPS_HOSTNAME.local${HTTPS_DOMAIN:+ + $HTTPS_DOMAIN})")
```

- [ ] **Step 5: Add the module call**

After the VPN module block (before the kiosk block, around line 340), add:

```bash
# --- Optional: HTTPS reverse proxy (Caddy + mDNS hostname) -----------------
if [[ $WITH_HTTPS -eq 1 ]]; then
  step "Setting up HTTPS (Caddy reverse proxy + mDNS hostname)..."
  mkdir -p "$PREFIX/deploy"
  rm -rf "$PREFIX/deploy/https"   # replace, don't nest, on a re-run/update
  cp -r "$REPO_ROOT/deploy/https" "$PREFIX/deploy/https"
  chmod +x "$PREFIX/deploy/https/"*.sh 2>/dev/null || true
  HTTPS_ARGS=(--prefix "$PREFIX" --service-user "$SERVICE_USER" --port "$PORT" --hostname "$HTTPS_HOSTNAME")
  [[ -n "$HTTPS_DOMAIN" ]] && HTTPS_ARGS+=(--domain "$HTTPS_DOMAIN")
  bash "$PREFIX/deploy/https/install.sh" "${HTTPS_ARGS[@]}" \
    || echo "   HTTPS setup reported an issue — see docs/BETRIEB.md §3." >&2
fi
```

Note: this runs after the systemd service block (line ~320) so Caddy proxies a service that's already installed — matching where VPN/kiosk sit.

- [ ] **Step 6: Syntax check**

Run: `bash -n deploy/install.sh && echo "syntax ok"`
Expected: `syntax ok`

- [ ] **Step 7: Commit**

```bash
git add deploy/install.sh
git commit -m "feat(install): add HTTPS + mDNS hostname questions and module call"
```

---

## Task 9: Frontend — `HttpsCard` in the System page

**Files:**
- Modify: `frontend/src/api.ts` — add interface + two functions (after the `SystemStatus` interface, ~line 237).
- Modify: `frontend/src/pages/System.tsx` — add `HttpsCard`, render it after `PowerCard` (line 199).

- [ ] **Step 1: Add the API types + calls**

In `frontend/src/api.ts`, after the `SystemStatus` interface (line 237), add:

```typescript
export interface HttpsStatus {
  enabled: boolean;
  hostname: string;
  domain: string;
  lan_url: string;
  public_url: string;
}

export async function getHttpsStatus(): Promise<HttpsStatus> {
  return api<HttpsStatus>("/api/system/https");
}

export async function updateHttps(patch: {
  hostname?: string;
  domain?: string;
}): Promise<HttpsStatus> {
  return api<HttpsStatus>("/api/system/https", {
    method: "PUT",
    body: JSON.stringify(patch),
  });
}
```

- [ ] **Step 2: Import into System.tsx**

In `frontend/src/pages/System.tsx`, extend the import from `../api` (lines 2-9) to add `getHttpsStatus`, `updateHttps`, and `type HttpsStatus`:

```typescript
import {
  api,
  ApiError,
  getHttpsStatus,
  updateHttps,
  type AuditEvent,
  type HttpsStatus,
  type SystemStatus,
  type UpdateInfo,
  type UpdateProgress,
} from "../api";
```

- [ ] **Step 3: Add the `HttpsCard` component**

In `frontend/src/pages/System.tsx`, add this component just before the `PowerCard` function definition (before line 270):

```typescript
function HttpsCard() {
  const toast = useToast();
  const [https, setHttps] = useState<HttpsStatus | null>(null);
  const [domain, setDomain] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getHttpsStatus()
      .then((s) => {
        setHttps(s);
        setDomain(s.domain);
      })
      .catch(() => setHttps(null));
  }, []);

  async function saveDomain() {
    setBusy(true);
    try {
      const s = await updateHttps({ domain: domain.trim() });
      setHttps(s);
      setDomain(s.domain);
      toast.info("HTTPS", s.domain ? `Domain gesetzt: ${s.domain}` : "Domain entfernt.");
    } catch (e) {
      toast.error("HTTPS", e instanceof ApiError ? e.message : "Speichern fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  if (!https) return null;

  return (
    <div className="mb-6 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
      <div className="mb-1 flex items-center gap-1.5 text-sm font-medium text-slate-400">
        HTTPS
        <InfoTip text="Die Box ist im lokalen Netz per https://<name>.local mit einem selbstsignierten Zertifikat erreichbar. Trägst du eine öffentliche Domain ein, holt Caddy zusätzlich automatisch ein echtes Zertifikat (Let's Encrypt) — der lokale Zugriff bleibt bestehen." />
      </div>

      {!https.enabled ? (
        <div className="rounded-lg bg-slate-900/60 px-3 py-2 text-xs text-slate-400">
          HTTPS ist auf dieser Box nicht eingerichtet. Den{" "}
          <code className="rounded bg-black/30 px-1.5 py-0.5 text-slate-200">Installer</code>{" "}
          erneut ausführen und HTTPS aktivieren.
        </div>
      ) : (
        <>
          <p className="mb-3 text-xs text-slate-500">
            Lokal erreichbar unter{" "}
            <code className="rounded bg-black/30 px-1.5 py-0.5 text-slate-200">{https.lan_url}</code>
            {https.public_url && (
              <>
                {" "}und öffentlich unter{" "}
                <code className="rounded bg-black/30 px-1.5 py-0.5 text-slate-200">{https.public_url}</code>
              </>
            )}
            .
          </p>
          <label className="block text-sm">
            <span className="mb-1 block text-slate-300">Öffentliche Domain (optional)</span>
            <div className="flex gap-2">
              <input
                className="w-full rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-none focus:border-ogc-teal"
                placeholder="z. B. cloud.example.com"
                value={domain}
                onChange={(e) => setDomain(e.target.value)}
              />
              <button
                type="button"
                onClick={saveDomain}
                disabled={busy || domain.trim() === https.domain}
                className="rounded-lg border border-white/10 px-4 py-2 text-sm font-semibold text-slate-200 hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy ? "…" : "Speichern"}
              </button>
            </div>
            <span className="mt-1 block text-xs text-slate-500">
              Wichtig: Die Domain muss per DNS auf diese Box zeigen und die Ports 80/443 müssen von
              außen erreichbar sein (Portweiterleitung am Router). Das Zertifikat wird dann
              automatisch geholt — das kann bis zu einer Minute dauern.
            </span>
          </label>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Render it**

In `frontend/src/pages/System.tsx`, after the `{status && <PowerCard status={status} />}` line (line 199), add:

```typescript
      <HttpsCard />
```

- [ ] **Step 5: Type-check the frontend**

Run: `cd frontend && npm run lint`
Expected: no TypeScript errors.

- [ ] **Step 6: Manual smoke test (dev server)**

Run: `cd frontend && npm run dev` — log in as admin, open the System page, confirm the HTTPS card renders. Against a dev backend without `https_apply_command` set, it should show the "nicht eingerichtet" hint. (Full end-to-end needs a Pi/VM with Caddy — covered by Task 6/7 manual steps.)

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api.ts frontend/src/pages/System.tsx
git commit -m "feat(https): System page HTTPS card (LAN URL + domain field)"
```

---

## Task 10: Update placeholders + docs

**Files:**
- Modify: `deploy/Caddyfile`
- Modify: `deploy/nginx.conf.example`
- Modify: `docs/BETRIEB.md` (§3, around line 120-129)

- [ ] **Step 1: Update `deploy/Caddyfile` header comment**

Replace the whole file content of `deploy/Caddyfile` with a note that the installer now manages the real config, keeping the manual example for reference:

```
# OffgridCloud reverse proxy (Caddy).
#
# NOTE: The installer now sets this up for you — run deploy/install.sh and
# answer "HTTPS aktivieren? Ja". It writes /etc/caddy/Caddyfile via
# deploy/https/apply.sh (a self-signed <hostname>.local block, plus a real
# Let's-Encrypt block if you give it a public domain). Change the hostname or
# domain later under System → HTTPS in the web UI.
#
# The blocks below are only a manual reference for a standalone setup:
#
#   sudo caddy run --config deploy/Caddyfile
#
offgridcloud.example.com {
    reverse_proxy localhost:8000
    # SSE (/api/events) works out of the box — Caddy does not buffer responses.
}

# --- Offline / field use (no public domain) -------------------------------
# Self-signed certificate Caddy manages locally. Replace with the Pi's mDNS name.
#
# https://offgridcloud.local {
#     tls internal
#     reverse_proxy localhost:8000
# }
```

- [ ] **Step 2: Update `deploy/nginx.conf.example` placeholders**

In `deploy/nginx.conf.example`, replace every `offgrid.local` with `offgridcloud.local` (lines 5, 9, 38) and add a note at the top that Caddy via the installer is the recommended path:

Change line 1 from:
```
# OffgridCloud reverse proxy (nginx). Adjust server_name and certificate paths.
```
to:
```
# OffgridCloud reverse proxy (nginx) — MANUAL ALTERNATIVE.
# The installer sets up Caddy automatically (recommended); use this only if you
# specifically want nginx. Adjust server_name and certificate paths.
```

Then update the three `offgrid.local` occurrences to `offgridcloud.local`.

- [ ] **Step 3: Rewrite `docs/BETRIEB.md` §3**

Replace the §3 block (lines 120-129) with:

```markdown
## 3. HTTPS / Reverse-Proxy

Der Installer richtet HTTPS automatisch ein (Frage „HTTPS aktivieren? Ja",
Standard). Er installiert **Caddy** als Reverse-Proxy und **avahi** für den
mDNS-Namen und erzeugt:

- einen **lokalen Zugang** `https://<hostname>.local` (Standard
  `offgridcloud.local`) mit selbstsigniertem Zertifikat — funktioniert offline
  im Feld, ohne Domain und ohne Internet;
- optional zusätzlich einen **öffentlichen Zugang** über eine echte Domain mit
  automatischem Let's-Encrypt-Zertifikat, sobald eine Domain hinterlegt ist.

**Domain später ändern:** im Portal unter **System → HTTPS** die Domain
eintragen oder entfernen — kein Neu-Installieren nötig. Der lokale
`.local`-Zugang bleibt immer erhalten. Damit ein echtes Zertifikat ausgestellt
werden kann, muss die Domain per DNS auf die Box zeigen und die Ports 80/443
müssen von außen erreichbar sein (Portweiterleitung am Router).

**Manuelle Alternative:** Wer statt Caddy lieber nginx nutzt, findet in
`deploy/nginx.conf.example` eine Vorlage (inkl. Self-signed-Cert-Rezept und der
SSE-freundlichen `/api/events`-Location).
```

- [ ] **Step 4: Commit**

```bash
git add deploy/Caddyfile deploy/nginx.conf.example docs/BETRIEB.md
git commit -m "docs: installer-managed HTTPS; offgridcloud.local placeholder"
```

---

## Final Verification

- [ ] **Backend suite green:** `cd backend && python -m pytest -q` → all pass.
- [ ] **Frontend type-check:** `cd frontend && npm run lint` → no errors.
- [ ] **Bash syntax:** `bash -n deploy/install.sh deploy/https/install.sh deploy/https/apply.sh` → clean.
- [ ] **End-to-end on a Pi/VM (manual, documented in the PR):**
  1. Fresh install with HTTPS on, no domain → `https://offgridcloud.local` reachable (self-signed), login works.
  2. System → HTTPS: set a domain → real cert issued (give it a minute), LAN URL still reachable.
  3. System → HTTPS: clear the domain → only the LAN block remains.
  4. Re-run the installer, press Enter through → existing hostname/domain preserved (not reset).
- [ ] **Open the PR** with the manual E2E results in the description.
```
