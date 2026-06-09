"""Thin wrapper around the rclone binary.

rclone is OffgridCloud's universal transfer engine — it covers every required
provider (S3, Azure Blob, OneDrive/SharePoint, WebDAV/Nextcloud/ownCloud, SFTP,
FTP/FTPS, ...) and brings resumable transfers, retries and bandwidth limiting.

Phase 0 only exposes a version/availability check. Actual transfer
orchestration arrives in Phase 4.
"""

from __future__ import annotations

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
