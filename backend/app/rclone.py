"""Thin wrapper around the rclone binary.

rclone is OffgridCloud's universal transfer engine — it covers every required
provider (S3, Azure Blob, OneDrive/SharePoint, WebDAV/Nextcloud/ownCloud, SFTP,
FTP/FTPS, ...) and brings resumable transfers, retries and bandwidth limiting.

Phase 0 only exposes a version/availability check. Actual transfer
orchestration arrives in Phase 4.
"""

from __future__ import annotations

import os
import shutil
import subprocess
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
