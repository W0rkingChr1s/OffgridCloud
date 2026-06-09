"""Lazy thumbnail generation for media items.

Images use Pillow; videos use ffmpeg if it's installed (optional on a Pi).
Thumbnails are cached under ``<buffer>/.thumbs/<media_id>.jpg``.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from .config import get_settings

logger = logging.getLogger("offgridcloud.thumbnails")

THUMB_MAX = 480
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mpg", ".mpeg"}


def _thumbs_dir() -> Path:
    path = get_settings().buffer_dir / ".thumbs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def thumb_path(media_id: int) -> Path:
    return _thumbs_dir() / f"{media_id}.jpg"


def _make_image_thumb(src: Path, dst: Path) -> bool:
    try:
        from PIL import Image

        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((THUMB_MAX, THUMB_MAX))
            im.save(dst, "JPEG", quality=80)
        return True
    except Exception as exc:  # noqa: BLE001 - unsupported/corrupt file
        logger.debug("Image thumbnail failed for %s: %s", src, exc)
        return False


def _make_video_thumb(src: Path, dst: Path) -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", "1", "-i", str(src),
                "-frames:v", "1", "-vf", f"scale={THUMB_MAX}:-1",
                str(dst),
            ],
            capture_output=True,
            timeout=30,
            check=True,
        )
        return dst.exists()
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("Video thumbnail failed for %s: %s", src, exc)
        return False


def get_or_create_thumb(media_id: int, source_path: str, filename: str) -> Path | None:
    """Return a cached/created thumbnail path, or None if not possible."""
    dst = thumb_path(media_id)
    if dst.exists():
        return dst
    src = Path(source_path)
    if not src.is_file():
        return None

    ext = Path(filename).suffix.lower()
    ok = _make_video_thumb(src, dst) if ext in VIDEO_EXTS else _make_image_thumb(src, dst)
    return dst if ok else None
