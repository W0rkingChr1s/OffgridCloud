"""Lazy thumbnail generation for media items.

Images use Pillow; videos use ffmpeg if it's installed (optional on a Pi).
Thumbnails are cached under ``<buffer>/.thumbs/<media_id>.jpg``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import get_settings

logger = logging.getLogger("offgridcloud.thumbnails")

THUMB_MAX = 480
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mpg", ".mpeg"}

# Thumbnails are rendered on these two threads only; the endpoints await them.
# A gallery fires hundreds of requests at once, and they must queue here, not
# in the server's shared thread pool: that pool also serves every other API
# call and the web UI's static files. Blocking it on a thumbnail slot took the
# whole site down while a card copy slowed the thumbnails to a crawl.
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="thumbs")


async def render(fn, *args):
    """Run a thumbnail function on the thumbnail threads and await it."""
    return await asyncio.get_running_loop().run_in_executor(_EXECUTOR, fn, *args)


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


# --- Thumbnails for files on attached media ---------------------------------
# The storage gallery shows hundreds of camera files at once. Generating them
# must stay cheap on a Pi 3: JPEGs decode in draft mode (DCT scaling, ~1/8 the
# work), RAW files use the preview JPEG every camera embeds, and at most two
# thumbnails are computed at a time (see _EXECUTOR) so a gallery scroll can't
# starve uploads.

SOURCE_SIZES = (320, 1600)
_SOURCE_CACHE_MAX = 6000  # files; oldest are pruned beyond this
_RAW_SCAN_BYTES = 24 * 1024 * 1024


def _source_dir() -> Path:
    path = _thumbs_dir() / "src"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _prune_source_cache() -> None:
    try:
        files = sorted(_source_dir().glob("*.jpg"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    for old in files[: max(0, len(files) - _SOURCE_CACHE_MAX)]:
        old.unlink(missing_ok=True)


def _render(im, dst: Path, size: int) -> None:
    from PIL import ImageOps

    im = ImageOps.exif_transpose(im)
    im = im.convert("RGB")
    im.thumbnail((size, size))
    im.save(dst, "JPEG", quality=82)


def _image_thumb(src: Path, dst: Path, size: int) -> bool:
    try:
        from PIL import Image

        with Image.open(src) as im:
            if im.format == "JPEG":
                im.draft("RGB", (size, size))
            _render(im, dst, size)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("Source thumbnail failed for %s: %s", src, exc)
        return False


def _raw_thumb(src: Path, dst: Path, size: int) -> bool:
    """Use the largest decodable JPEG preview embedded in a RAW file."""
    import io

    from PIL import Image

    try:
        with open(src, "rb") as fh:
            data = fh.read(_RAW_SCAN_BYTES)
    except OSError:
        return False
    best = None
    pos = data.find(b"\xff\xd8\xff")
    tried = 0
    while pos != -1 and tried < 12:
        tried += 1
        try:
            im = Image.open(io.BytesIO(data[pos:]))
            im.draft("RGB", (size, size))
            im.load()
            if best is None or im.size[0] > best.size[0]:
                best = im
            if im.size[0] >= size:
                break
        except Exception:  # noqa: BLE001 - not a JPEG start, keep scanning
            pass
        pos = data.find(b"\xff\xd8\xff", pos + 3)
    if best is None or best.size[0] < 160:
        return False
    try:
        _render(best, dst, size)
        return True
    except Exception:  # noqa: BLE001
        return False


def source_thumb_path(size: int, key: str) -> Path:
    """Where the thumbnail of a medium file is cached (see :func:`source_thumb`)."""
    size = min(SOURCE_SIZES, key=lambda s: abs(s - size))
    digest = hashlib.sha1(f"{key}|{size}".encode()).hexdigest()
    return _source_dir() / f"{digest}.jpg"


def source_thumb(src: Path, kind: str, size: int, key: str) -> Path | None:
    """Cached thumbnail for a file on an attached medium, or None.

    ``key`` identifies the exact file version (device UUID, path, size, mtime)
    so a re-shot card with recycled names never shows a stale picture. Call it
    through :func:`render` from a request.
    """
    dst = source_thumb_path(size, key)
    size = min(SOURCE_SIZES, key=lambda s: abs(s - size))
    if dst.exists():
        return dst
    if kind not in {"photo", "raw", "video"}:
        return None
    # Per thread: both thumbnail threads may render the same file at once.
    tmp = dst.with_suffix(f".{threading.get_ident()}.tmp.jpg")
    if kind == "video":
        ok = _make_video_thumb(src, tmp)
        if ok and size != THUMB_MAX:
            ok = _image_thumb(tmp, tmp, size)
    elif kind == "raw":
        ok = _raw_thumb(src, tmp, size) or _image_thumb(src, tmp, size)
    else:
        ok = _image_thumb(src, tmp, size)
    if not ok:
        tmp.unlink(missing_ok=True)
        return None
    tmp.replace(dst)
    if hash(dst.stem) % 50 == 0:
        _prune_source_cache()
    return dst
