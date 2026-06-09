"""Thin wrapper around the rclone binary.

rclone is OffgridCloud's universal transfer engine — it covers every required
provider (S3, Azure Blob, OneDrive/SharePoint, WebDAV/Nextcloud/ownCloud, SFTP,
FTP/FTPS, ...) and brings resumable transfers, retries and bandwidth limiting.

Phase 0 only exposes a version/availability check. Actual transfer
orchestration arrives in Phase 4.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

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


def _remote_env(options: dict[str, str]) -> dict[str, str]:
    """Build RCLONE_CONFIG_* env vars so secrets never touch disk."""
    env = dict(os.environ)
    for key, value in options.items():
        env[f"RCLONE_CONFIG_{_REMOTE.upper()}_{key.upper()}"] = str(value)
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
    on_progress: Callable[[int, int], None] | None = None,
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

    cmd = [
        binary, "copyto", local_path, f"{_REMOTE}:{dest}",
        "--use-json-log", "--stats", "1s", "--stats-log-level", "NOTICE",
        "--transfers", "1", "--low-level-retries", "3", "--retries", "1",
    ]
    if bwlimit_kbps and bwlimit_kbps > 0:
        cmd += ["--bwlimit", f"{bwlimit_kbps}k"]
    last_bytes = 0
    last_speed = 0.0
    err_tail = ""
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env=_remote_env(options),
        )
    except OSError as exc:  # pragma: no cover
        return UploadResult(False, 0, str(exc))

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
                on_progress(last_bytes, total)
        elif obj.get("level") == "error" and obj.get("msg"):
            err_tail = obj["msg"]

    proc.wait()
    elapsed = max(time.monotonic() - started, 0.001)
    # Prefer rclone's reported speed (bytes/s); fall back to bytes/elapsed.
    kbps = (last_speed / 1024.0) if last_speed > 0 else (last_bytes / 1024.0 / elapsed)
    if proc.returncode == 0:
        return UploadResult(True, last_bytes, "", kbps)
    return UploadResult(False, last_bytes, err_tail or "Upload fehlgeschlagen", kbps)
