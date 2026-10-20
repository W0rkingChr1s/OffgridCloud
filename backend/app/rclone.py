"""Thin wrapper around the rclone binary.

rclone is OffgridCloud's universal transfer engine — it covers every required
provider (S3, Azure Blob, OneDrive/SharePoint, WebDAV/Nextcloud/ownCloud, SFTP,
FTP/FTPS, ...) and brings resumable transfers, retries and bandwidth limiting.

Phase 0 only exposes a version/availability check. Actual transfer
orchestration arrives in Phase 4.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

from .bonding import apply_proxy
from .config import get_settings


@dataclass(frozen=True)
class RcloneStatus:
    available: bool
    version: str | None = None
    error: str | None = None


def check_rclone() -> RcloneStatus:
    """Return whether the rclone binary is available and its version."""
    binary = get_settings().rclone_binary
    if shutil.which(binary) is None:
        return RcloneStatus(available=False, error=f"'{binary}' not found in PATH")
    try:
        result = subprocess.run(
            [binary, "version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (subprocess.SubprocessError, OSError) as exc:  # pragma: no cover
        return RcloneStatus(available=False, error=str(exc))

    first_line = result.stdout.splitlines()[0] if result.stdout else ""
    return RcloneStatus(available=True, version=first_line.strip() or None)


@dataclass(frozen=True)
class TestResult:
    ok: bool
    message: str


_REMOTE = "ogctest"
# Not an rclone option: carries the net-merge proxy (see app/bonding.py) from
# the transfer worker to this one rclone run, so the upload-function signature
# the worker and its tests share stays unchanged.
PROXY_OPTION = "_ogc_proxy"


def _remote_env(options: dict[str, str]) -> dict[str, str]:
    """Build RCLONE_CONFIG_* env vars so secrets never touch disk."""
    env = dict(os.environ)
    for key, value in options.items():
        if key == PROXY_OPTION:
            continue
        env[f"RCLONE_CONFIG_{_REMOTE.upper()}_{key.upper()}"] = str(value)
    apply_proxy(env, _REMOTE, options, options.get(PROXY_OPTION, ""))
    return env


def test_remote(options: dict[str, str], subpath: str = "") -> TestResult:
    """Try to list the remote's root (or subpath) with a short timeout."""
    binary = get_settings().rclone_binary
    if shutil.which(binary) is None:
        return TestResult(False, f"rclone ('{binary}') ist nicht installiert")

    target = f"{_REMOTE}:{subpath}"
    cmd = [
        binary, "lsd", target,
        "--max-depth", "1",
        "--low-level-retries", "1",
        "--retries", "1",
        "--contimeout", "10s",
        "--timeout", "15s",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env=_remote_env(options),
        )
    except subprocess.TimeoutExpired:
        return TestResult(False, "Zeitüberschreitung beim Verbindungstest")
    except OSError as exc:  # pragma: no cover
        return TestResult(False, str(exc))

    if result.returncode == 0:
        return TestResult(True, "Verbindung erfolgreich")
    # Surface a concise error (last non-empty stderr line).
    err_lines = [ln for ln in (result.stderr or "").splitlines() if ln.strip()]
    return TestResult(False, err_lines[-1] if err_lines else "Verbindung fehlgeschlagen")


@dataclass(frozen=True)
class DeleteResult:
    ok: bool
    message: str = ""


def delete_remote(options: dict[str, str], dest: str) -> DeleteResult:
    """Delete a single file at ``remote:dest`` via ``rclone deletefile``.

    A missing file is treated as success (idempotent): the goal state — "the
    object is not on the remote" — is already met.
    """
    binary = get_settings().rclone_binary
    if shutil.which(binary) is None:
        return DeleteResult(False, f"rclone ('{binary}') ist nicht installiert")

    cmd = [
        binary, "deletefile", f"{_REMOTE}:{dest}",
        "--low-level-retries", "2", "--retries", "1",
        "--contimeout", "15s", "--timeout", "30s",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, env=_remote_env(options)
        )
    except subprocess.TimeoutExpired:
        return DeleteResult(False, "Zeitüberschreitung beim Löschen")
    except OSError as exc:  # pragma: no cover
        return DeleteResult(False, str(exc))

    if result.returncode == 0:
        return DeleteResult(True, "Gelöscht")
    err_lines = [ln for ln in (result.stderr or "").splitlines() if ln.strip()]
    tail = err_lines[-1] if err_lines else ""
    # rclone exits non-zero when the object is already gone; treat as success.
    if "not found" in tail.lower() or "does not exist" in tail.lower():
        return DeleteResult(True, "Bereits entfernt")
    return DeleteResult(False, tail or "Löschen fehlgeschlagen")


logger = logging.getLogger(__name__)


# --- Live bandwidth control (rclone rc) -------------------------------------
# rclone takes --bwlimit when an upload starts. To change it while a long
# upload runs (a pool hub re-splitting a shared line, a schedule window
# starting), every upload also opens rclone's remote-control API on a free
# localhost port with a one-off random password, passed via the environment so
# it never shows up in `ps`. The worker then calls core/bwlimit on it.


@dataclass
class LiveUpload:
    """A running upload whose bandwidth limit can be changed on the fly."""

    url: str
    user: str
    password: str = field(repr=False)
    kbps: int  # limit currently in effect, KiB/s (0 = unlimited)


_live_lock = threading.Lock()
_live: dict[int, LiveUpload] = {}  # id(LiveUpload) -> upload


def live_uploads() -> list[LiveUpload]:
    with _live_lock:
        return list(_live.values())


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _rc_rate(kbps: int) -> str:
    return f"{kbps}k" if kbps > 0 else "off"


# Localhost only: never route the rc call through a configured HTTP proxy.
_rc_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def set_live_limit(upload: LiveUpload, kbps: int, timeout: float = 2.0) -> bool:
    """Change a running upload's limit via core/bwlimit. Never raises."""
    token = base64.b64encode(f"{upload.user}:{upload.password}".encode()).decode()
    req = urllib.request.Request(
        f"{upload.url}/core/bwlimit",
        data=json.dumps({"rate": _rc_rate(kbps)}).encode(),
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _rc_opener.open(req, timeout=timeout) as resp:
            resp.read()
    except (urllib.error.URLError, OSError, ValueError):
        return False  # upload just ended, or rclone too old: the start limit stays
    upload.kbps = kbps
    return True


@dataclass
class UploadResult:
    ok: bool
    bytes_transferred: int = 0
    message: str = ""
    kbps: float = 0.0  # observed throughput in KiB/s


def run_upload(
    local_path: str,
    options: dict[str, str],
    dest: str,
    bwlimit_kbps: int = 0,
    on_progress: Callable[[int, int, float], None] | None = None,
) -> UploadResult:
    """Upload a single local file to ``remote:dest`` via ``rclone copyto``.

    rclone verifies size/hash after transfer for backends that support it, so a
    zero exit code implies an integrity-checked upload. Progress is parsed from
    rclone's JSON log on stderr. ``bwlimit_kbps`` (KiB/s, 0 = unlimited) throttles
    the transfer; observed throughput is returned in ``kbps``.
    """
    binary = get_settings().rclone_binary
    if shutil.which(binary) is None:
        return UploadResult(False, 0, f"rclone ('{binary}') ist nicht installiert")

    live = get_settings().rclone_live_limits
    result = _run_copyto(binary, local_path, options, dest, bwlimit_kbps, on_progress, live)
    if live and not result.ok and "address already in use" in result.message.lower():
        # Someone took the rc port between picking and binding it: just go
        # without live limits rather than failing the upload.
        logger.info("rclone rc port taken, retrying upload without live limits")
        result = _run_copyto(binary, local_path, options, dest, bwlimit_kbps, on_progress, False)
    return result


def _run_copyto(
    binary: str,
    local_path: str,
    options: dict[str, str],
    dest: str,
    bwlimit_kbps: int,
    on_progress: Callable[[int, int, float], None] | None,
    live: bool,
) -> UploadResult:
    cmd = [
        binary, "copyto", local_path, f"{_REMOTE}:{dest}",
        "--use-json-log", "--stats", "1s", "--stats-log-level", "NOTICE",
        "--transfers", "1", "--low-level-retries", "3", "--retries", "1",
    ]
    if bwlimit_kbps and bwlimit_kbps > 0:
        cmd += ["--bwlimit", f"{bwlimit_kbps}k"]
    env = _remote_env(options)
    handle: LiveUpload | None = None
    if live:
        port = _free_port()
        handle = LiveUpload(
            url=f"http://127.0.0.1:{port}",
            user="ogc",
            password=secrets.token_urlsafe(24),
            kbps=max(0, bwlimit_kbps or 0),
        )
        cmd += ["--rc", "--rc-addr", f"127.0.0.1:{port}"]
        env["RCLONE_RC_USER"] = handle.user
        env["RCLONE_RC_PASS"] = handle.password
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
    except OSError as exc:  # pragma: no cover
        return UploadResult(False, 0, str(exc))
    if handle is not None:
        with _live_lock:
            _live[id(handle)] = handle
    try:
        return _follow(proc, started, on_progress)
    finally:
        if handle is not None:
            with _live_lock:
                _live.pop(id(handle), None)


def _follow(
    proc: subprocess.Popen,
    started: float,
    on_progress: Callable[[int, int, float], None] | None,
) -> UploadResult:
    last_bytes = 0
    last_speed = 0.0
    err_tail = ""
    assert proc.stderr is not None
    for line in proc.stderr:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            err_tail = line
            continue
        stats = obj.get("stats")
        if isinstance(stats, dict):
            last_bytes = int(stats.get("bytes", last_bytes) or last_bytes)
            total = int(stats.get("totalBytes", 0) or 0)
            last_speed = float(stats.get("speed", last_speed) or last_speed)
            if on_progress:
                on_progress(last_bytes, total, last_speed / 1024.0)
        elif obj.get("level") in ("error", "critical", "fatal") and obj.get("msg"):
            # rclone logs fatal startup problems (e.g. rc port taken) as "critical".
            err_tail = obj["msg"]

    proc.wait()
    elapsed = max(time.monotonic() - started, 0.001)
    # Prefer rclone's reported speed (bytes/s); fall back to bytes/elapsed.
    kbps = (last_speed / 1024.0) if last_speed > 0 else (last_bytes / 1024.0 / elapsed)
    if proc.returncode == 0:
        return UploadResult(True, last_bytes, "", kbps)
    return UploadResult(False, last_bytes, err_tail or "Upload fehlgeschlagen", kbps)
