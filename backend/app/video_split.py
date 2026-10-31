"""Cut files into parts that each fit under an upload limit.

Some targets can't take big files in one request and offer no chunked upload
either: a Nextcloud share link on a server older than Nextcloud 32, behind a
proxy that rejects request bodies over ~100 MB (Cloudflare).

Videos are cut with ffmpeg's segment muxer and ``-c copy``: no re-encoding,
so it is fast even on a Pi and lossless, and every part is a standalone,
playable file. Cuts land on keyframes, so the parts are only roughly equal; a
part that still comes out too big triggers another pass with shorter
segments. Subtitle tracks (some DJI models write their flight data there)
stay in the parts and are also saved once as an ``.srt`` file. Vendor data
tracks (DJI ``djmd``/``dbgi``) can't be muxed into segments and are dropped.

Everything else is cut into plain byte ranges ``name.001``, ``name.002`` …,
which 7-Zip joins directly and any shell with ``cat``.
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
# Keep this much free on the buffer drive besides the parts themselves.
_SPACE_RESERVE = 200 * 1_000_000
_COPY_BLOCK = 4 * 1024 * 1024

# Messages that won't change by trying again (see transfers.is_permanent_error).
MSG_NO_FFMPEG = "ffmpeg ist nicht installiert"
MSG_KEYFRAMES = "Keyframe-Abstand zu groß"


@dataclass(frozen=True)
class SplitResult:
    ok: bool
    parts: tuple[Path, ...] = ()
    message: str = ""
    # Extra files to upload next to the parts: (local file, remote name).
    extras: tuple[tuple[Path, str], ...] = ()


def is_splittable(filename: str) -> bool:
    """Whether the file is a video that ffmpeg can cut into playable parts."""
    return Path(filename).suffix.lower() in VIDEO_EXTS


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def part_name(filename: str, index: int, total: int) -> str:
    """``clip.MP4`` -> ``clip_teil01.MP4`` (two digits at least, more if needed)."""
    path = Path(filename)
    width = max(2, len(str(total)))
    return f"{path.stem}_teil{index:0{width}d}{path.suffix}"


def binary_part_name(filename: str, index: int, total: int) -> str:
    """``data.zip`` -> ``data.zip.001`` (7-Zip's split-volume naming)."""
    width = max(3, len(str(total)))
    return f"{filename}.{index:0{width}d}"


def part_folder(filename: str) -> str:
    """The sub folder the parts go to: the file name without extension."""
    return Path(filename).stem


def join_instructions(filename: str, total: int) -> str:
    """Text placed next to binary parts so recipients know how to rejoin them."""
    first = binary_part_name(filename, 1, total)
    pattern = first[:-3] + "*"
    return (
        f"{filename} wurde für den Upload in {total} Teile zerlegt.\n\n"
        "Zusammenfügen:\n"
        f"  Windows (7-Zip):   Rechtsklick auf {first} -> 7-Zip -> Dateien zusammenfügen\n"
        f"  Windows (cmd):     copy /b \"{pattern}\" \"{filename}\"\n"
        f"  Linux/macOS:       cat \"{pattern}\" > \"{filename}\"\n"
    )


def has_space(directory: Path, needed: int) -> tuple[bool, str]:
    """Whether ``directory`` has room for ``needed`` more bytes (plus a reserve)."""
    try:
        free = shutil.disk_usage(directory).free
    except OSError:
        return True, ""  # can't tell: let the split itself fail if it must
    if free >= needed + _SPACE_RESERVE:
        return True, ""
    return False, (
        f"zu wenig Platz im Puffer zum Teilen: braucht {_mb(needed + _SPACE_RESERVE)}, "
        f"frei {_mb(free)}"
    )


def _mb(num: int) -> str:
    return f"{num / 1_000_000:.0f} MB"


def _probe(src: Path) -> tuple[float | None, list[str]]:
    """Duration in seconds and the codec types of all streams."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type", "-of", "json", str(src)],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
        data = json.loads(out)
        duration = float(data["format"]["duration"])
        types = [s.get("codec_type", "") for s in data.get("streams", [])]
        return duration, types
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError):
        return None, []


def _run(cmd: list[str], timeout: int = 3600) -> str:
    """Run ffmpeg; the error text, or ``""`` on success."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "ffmpeg: Zeitüberschreitung"
    except OSError as exc:
        return f"ffmpeg: {exc}"
    if proc.returncode != 0:
        lines = [ln for ln in proc.stderr.splitlines() if ln.strip()]
        return "ffmpeg: " + (lines[-1] if lines else f"Exit-Code {proc.returncode}")
    return ""


def _segment(src: Path, out_dir: Path, seconds: float, subs: bool) -> tuple[list[Path], str]:
    for old in out_dir.glob("seg*"):
        old.unlink()
    pattern = out_dir / f"seg%04d{src.suffix.lower()}"
    maps = ["-map", "0:v", "-map", "0:a?"]
    if subs:
        maps += ["-map", "0:s?"]
    error = _run([
        "ffmpeg", "-v", "error", "-y", "-i", str(src), *maps, "-c", "copy",
        "-f", "segment", "-segment_time", f"{seconds:.3f}",
        "-reset_timestamps", "1", str(pattern),
    ])
    if error:
        return [], error
    return sorted(out_dir.glob("seg*")), ""


def _extract_subtitles(src: Path, out_dir: Path) -> Path | None:
    """The first subtitle track as ``.srt`` (DJI flight data), or ``None``."""
    dst = out_dir / "telemetry.srt"
    if _run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0:s:0", str(dst)],
            timeout=600):
        return None
    return dst if dst.exists() and dst.stat().st_size > 0 else None


def split_video(src: Path, out_dir: Path, max_bytes: int) -> SplitResult:
    """Cut ``src`` into playable parts of at most ``max_bytes`` each in ``out_dir``.

    The parts are named ``seg0000.ext`` … (callers rename them on upload).
    ``out_dir`` is created; the caller removes it when done.
    """
    if not ffmpeg_available():
        return SplitResult(False, message=MSG_NO_FFMPEG)
    size = src.stat().st_size
    ok, why = has_space(src.parent, size)
    if not ok:
        return SplitResult(False, message=why)
    duration, types = _probe(src)
    if not duration or duration <= 0:
        return SplitResult(False, message="Videolänge nicht lesbar (ffprobe)")
    out_dir.mkdir(parents=True, exist_ok=True)

    extras: list[tuple[Path, str]] = []
    subs = "subtitle" in types
    if subs:
        srt = _extract_subtitles(src, out_dir)
        if srt is not None:
            extras.append((srt, f"{src.stem}.srt"))

    seconds = max(_MIN_SEGMENT_SECONDS, duration * max_bytes * _HEADROOM / size)
    for _ in range(_MAX_PASSES):
        parts, error = _segment(src, out_dir, seconds, subs)
        if error and subs:
            # A subtitle codec the container can't take: go on without it,
            # the .srt next to the parts still has the data.
            logger.info("Segmentieren mit Untertiteln fehlgeschlagen (%s), ohne", error)
            subs = False
            parts, error = _segment(src, out_dir, seconds, subs)
        if error:
            return SplitResult(False, message=error)
        if not parts:
            return SplitResult(False, message="ffmpeg hat keine Teile erzeugt")
        biggest = max(p.stat().st_size for p in parts)
        if biggest <= max_bytes:
            return SplitResult(True, tuple(parts), extras=tuple(extras))
        if seconds <= _MIN_SEGMENT_SECONDS:
            break
        # Keyframes further apart than planned: shrink in proportion and retry.
        seconds = max(_MIN_SEGMENT_SECONDS, seconds * max_bytes * _HEADROOM / biggest)
        logger.info("Teil %d B > %d B, neuer Versuch mit %.1f s", biggest, max_bytes, seconds)
    return SplitResult(
        False, message=f"Teile bleiben über {_mb(max_bytes)} ({MSG_KEYFRAMES})"
    )


def split_binary(src: Path, out_dir: Path, max_bytes: int) -> SplitResult:
    """Cut any file into byte ranges of ``max_bytes`` (``seg0000`` …) in ``out_dir``."""
    size = src.stat().st_size
    ok, why = has_space(src.parent, size)
    if not ok:
        return SplitResult(False, message=why)
    out_dir.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    try:
        with src.open("rb") as fin:
            index = 0
            while True:
                part = out_dir / f"seg{index:04d}"
                written = 0
                with part.open("wb") as fout:
                    while written < max_bytes:
                        block = fin.read(min(_COPY_BLOCK, max_bytes - written))
                        if not block:
                            break
                        fout.write(block)
                        written += len(block)
                if written == 0:
                    part.unlink()
                    break
                parts.append(part)
                index += 1
    except OSError as exc:
        return SplitResult(False, message=f"Datei teilen fehlgeschlagen: {exc}")
    return SplitResult(True, tuple(parts))
