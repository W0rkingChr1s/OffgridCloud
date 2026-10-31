"""Cut a video into playable parts that each fit under an upload limit.

Some targets can't take big files in one request and offer no chunked upload
either: a Nextcloud share link on a server older than Nextcloud 32, behind a
proxy that rejects request bodies over ~100 MB (Cloudflare). For videos there
is a way out that keeps every piece usable: ffmpeg's segment muxer with
``-c copy``. No re-encoding, so it is fast even on a Pi and lossless. Every
part is a standalone, playable file. Cuts land on keyframes, so the parts are
only roughly equal; a part that still comes out too big triggers a second
pass with shorter segments.

Only the video and audio streams are kept: vendor data tracks (DJI telemetry
``djmd``/``dbgi``) can't be muxed into segments and are dropped.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("offgridcloud.video_split")

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv"}

# Aim below the limit: segment sizes vary with the content between keyframes.
_HEADROOM = 0.8
_MAX_PASSES = 4
_MIN_SEGMENT_SECONDS = 1.0


@dataclass(frozen=True)
class SplitResult:
    ok: bool
    parts: tuple[Path, ...] = ()
    message: str = ""


def is_splittable(filename: str) -> bool:
    return Path(filename).suffix.lower() in VIDEO_EXTS


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def part_name(filename: str, index: int, total: int) -> str:
    """``clip.MP4`` -> ``clip_teil01.MP4`` (two digits at least, more if needed)."""
    path = Path(filename)
    width = max(2, len(str(total)))
    return f"{path.stem}_teil{index:0{width}d}{path.suffix}"


def part_folder(filename: str) -> str:
    """The sub folder the parts go to: the file name without extension."""
    return Path(filename).stem


def _duration(src: Path) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(src)],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
        return float(json.loads(out)["format"]["duration"])
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError):
        return None


def _segment(src: Path, out_dir: Path, seconds: float) -> tuple[list[Path], str]:
    for old in out_dir.glob("seg*"):
        old.unlink()
    pattern = out_dir / f"seg%04d{src.suffix.lower()}"
    cmd = [
        "ffmpeg", "-v", "error", "-y", "-i", str(src),
        "-map", "0:v", "-map", "0:a?", "-c", "copy",
        "-f", "segment", "-segment_time", f"{seconds:.3f}",
        "-reset_timestamps", "1", str(pattern),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except subprocess.TimeoutExpired:
        return [], "ffmpeg: Zeitüberschreitung beim Teilen"
    except OSError as exc:
        return [], f"ffmpeg: {exc}"
    if proc.returncode != 0:
        lines = [ln for ln in proc.stderr.splitlines() if ln.strip()]
        return [], "ffmpeg: " + (lines[-1] if lines else f"Exit-Code {proc.returncode}")
    return sorted(out_dir.glob("seg*")), ""


def split_video(src: Path, out_dir: Path, max_bytes: int) -> SplitResult:
    """Cut ``src`` into parts of at most ``max_bytes`` each inside ``out_dir``.

    The parts are named ``seg0000.ext`` … (callers rename them on upload).
    ``out_dir`` is created; the caller removes it when done.
    """
    if not ffmpeg_available():
        return SplitResult(False, message="ffmpeg ist nicht installiert")
    size = src.stat().st_size
    duration = _duration(src)
    if not duration or duration <= 0:
        return SplitResult(False, message="Videolänge nicht lesbar (ffprobe)")
    out_dir.mkdir(parents=True, exist_ok=True)

    seconds = max(_MIN_SEGMENT_SECONDS, duration * max_bytes * _HEADROOM / size)
    for _ in range(_MAX_PASSES):
        parts, error = _segment(src, out_dir, seconds)
        if error:
            return SplitResult(False, message=error)
        if not parts:
            return SplitResult(False, message="ffmpeg hat keine Teile erzeugt")
        biggest = max(p.stat().st_size for p in parts)
        if biggest <= max_bytes:
            return SplitResult(True, tuple(parts))
        if seconds <= _MIN_SEGMENT_SECONDS:
            break
        # Keyframes further apart than planned: shrink in proportion and retry.
        seconds = max(_MIN_SEGMENT_SECONDS, seconds * max_bytes * _HEADROOM / biggest)
        logger.info("Teil %d B > %d B, neuer Versuch mit %.1f s", biggest, max_bytes, seconds)
    return SplitResult(
        False,
        message=f"Teile bleiben über {max_bytes // 1_000_000} MB "
                "(Keyframe-Abstand zu groß)",
    )
