"""Update checks against GitHub Releases.

The instance knows its own version (``app.__version__``) and asks the GitHub
Releases API for the latest published release. Pure helpers (version parsing /
comparison) are separated from the network call so they're easy to unit-test and
so the whole thing degrades gracefully when the box is offline (the common case
for an off-grid appliance).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

_CACHE_TTL = 900.0  # seconds — don't hammer the API; releases change rarely


def parse_version(value: str) -> tuple[int, ...]:
    """Parse a version/tag like ``v1.2.3`` or ``0.0.1`` into a comparable tuple.

    Non-numeric suffixes (``-rc1``) are ignored for ordering; unknown formats
    return ``(0,)`` so they never look newer than a real release.
    """
    if not value:
        return (0,)
    cleaned = value.strip().lstrip("vV")
    match = re.match(r"(\d+(?:\.\d+)*)", cleaned)
    if not match:
        return (0,)
    return tuple(int(p) for p in match.group(1).split("."))


def is_newer(latest: str, current: str) -> bool:
    """True if ``latest`` is a strictly newer version than ``current``."""
    a, b = parse_version(latest), parse_version(current)
    length = max(len(a), len(b))
    a += (0,) * (length - len(a))
    b += (0,) * (length - len(b))
    return a > b


@dataclass
class UpdateInfo:
    current: str
    latest: str | None = None
    update_available: bool = False
    release_url: str = ""
    release_name: str = ""
    published_at: str = ""
    notes: str = ""
    error: str = ""
    checked_at: float = field(default_factory=lambda: 0.0)


_cache: dict[str, tuple[float, UpdateInfo]] = {}


def _fetch_latest_release(repo: str, timeout: float = 6.0) -> dict:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "OffgridCloud"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed GitHub host)
        return json.loads(resp.read().decode("utf-8"))


def check_for_update(
    current: str,
    repo: str,
    *,
    now: float | None = None,
    fetcher=_fetch_latest_release,
    use_cache: bool = True,
) -> UpdateInfo:
    """Return update status, caching successful lookups for a while.

    Never raises: on any error (offline, rate-limited, no releases yet) it
    returns an ``UpdateInfo`` with ``update_available=False`` and a message.
    """
    now = time.time() if now is None else now
    if use_cache:
        cached = _cache.get(repo)
        if cached and now - cached[0] < _CACHE_TTL:
            return cached[1]

    info = UpdateInfo(current=current, checked_at=now)
    try:
        data = fetcher(repo)
    except urllib.error.HTTPError as exc:
        # 404 = the repo simply has no published release yet — not a failure.
        info.error = (
            "Noch keine Releases veröffentlicht."
            if exc.code == 404
            else f"Update-Check fehlgeschlagen (HTTP {exc.code})."
        )
        return info
    except Exception:  # noqa: BLE001 - offline / rate-limited / DNS
        info.error = "Kein Update-Check möglich (offline?)."
        return info

    tag = str(data.get("tag_name") or data.get("name") or "")
    info.latest = tag or None
    info.release_url = str(data.get("html_url") or "")
    info.release_name = str(data.get("name") or tag)
    info.published_at = str(data.get("published_at") or "")
    info.notes = str(data.get("body") or "")[:4000]
    info.update_available = bool(tag) and is_newer(tag, current)

    _cache[repo] = (now, info)
    return info


def clear_cache() -> None:
    _cache.clear()


# ---------------------------------------------------------------------------
# One-click self-update runner (observable from the portal)
# ---------------------------------------------------------------------------
#
# The bare "fire a detached command and forget" approach gives the operator no
# feedback: they click "update", the service restarts at some point, and if it
# fails they're back in the terminal reading ``journalctl``. To keep everything
# inside the portal we persist the run to disk so it survives the restart the
# update itself triggers:
#
#   * ``update.log``   — the command's combined stdout/stderr, streamed live.
#   * ``update-state.json`` — phase + versions + timing, read by the UI.
#
# Because the update rebuilds and restarts the systemd service, the process that
# launched it (us) is killed mid-run. Two mechanisms cover the outcome:
#
#   1. A monitor thread waits on the child. If the command exits *without*
#      restarting us (e.g. sudoers misconfigured, git/build error) it records
#      success/failure right away — the portal stays up and shows the log.
#   2. If the restart kills us first, the monitor dies too; on the next boot
#      ``resolve_pending()`` inspects the leftover ``running`` state and decides
#      the outcome from the new running version and the log's sentinels.

PHASE_IDLE = "idle"
PHASE_RUNNING = "running"
PHASE_SUCCESS = "success"
PHASE_FAILED = "failed"
PHASE_UNKNOWN = "unknown"

_LOG_TAIL_BYTES = 16384  # what the UI streams — plenty for the update transcript
_STALE_AFTER = 3600.0  # a "running" state older than this on boot is treated as stale

# update.sh prints these near the end; they let us judge the outcome after a
# restart even when the version string didn't change (main channel / same tag).
_SENTINEL_OK = "and healthy"
_SENTINEL_FAIL = "did not answer"
# The last step update.sh reaches before the restart tears everything down. If
# the log got this far and the service is now back up, the update succeeded —
# even when the version string is unchanged and the health line never made it
# to the log because the restart killed the process first.
_SENTINEL_RESTARTING = "restarting the service"

_run_lock = threading.Lock()


def _state_path(data_dir: Path) -> Path:
    return Path(data_dir) / "update-state.json"


def _log_path(data_dir: Path) -> Path:
    return Path(data_dir) / "update.log"


@dataclass
class UpdateState:
    phase: str = PHASE_IDLE
    from_version: str = ""
    to_version: str = ""
    message: str = ""
    returncode: int | None = None
    started_at: float = 0.0
    finished_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "message": self.message,
            "returncode": self.returncode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def read_state(data_dir: Path) -> UpdateState:
    """Current update state, or an idle default when nothing has run."""
    try:
        raw = json.loads(_state_path(data_dir).read_text("utf-8"))
    except (OSError, ValueError):
        return UpdateState()
    return UpdateState(
        phase=str(raw.get("phase") or PHASE_IDLE),
        from_version=str(raw.get("from_version") or ""),
        to_version=str(raw.get("to_version") or ""),
        message=str(raw.get("message") or ""),
        returncode=raw.get("returncode"),
        started_at=float(raw.get("started_at") or 0.0),
        finished_at=float(raw.get("finished_at") or 0.0),
    )


def write_state(data_dir: Path, state: UpdateState) -> None:
    """Persist ``state`` atomically so a crash mid-write can't corrupt it."""
    path = _state_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state.to_dict()), "utf-8")
    os.replace(tmp, path)


def read_log_tail(data_dir: Path, max_bytes: int = _LOG_TAIL_BYTES) -> str:
    """Last ``max_bytes`` of the update log (decoded loosely), or ``""``."""
    try:
        with _log_path(data_dir).open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            data = fh.read()
    except OSError:
        return ""
    text = data.decode("utf-8", "replace")
    if len(data) == max_bytes and "\n" in text:
        # Drop a partial first line so the UI doesn't show a truncated fragment.
        text = text.split("\n", 1)[1]
    return text


def is_running(data_dir: Path) -> bool:
    return read_state(data_dir).phase == PHASE_RUNNING


def clear_state(data_dir: Path) -> None:
    """Reset to idle and drop the log — dismisses a finished result in the UI."""
    write_state(data_dir, UpdateState())
    try:
        _log_path(Path(data_dir)).unlink()
    except OSError:
        pass


def _monitor(proc: subprocess.Popen, data_dir: Path, current_version: str, now) -> None:
    """Record the outcome if the command exits before the restart kills us."""
    code = proc.wait()
    state = read_state(data_dir)
    if state.phase != PHASE_RUNNING:
        return  # already resolved (e.g. on a concurrent startup)
    # A *negative* return code means the child was killed by a signal — which is
    # exactly what a SUCCESSFUL update looks like: update.sh reaches
    # ``systemctl restart``, and the restart sends SIGTERM to the whole service
    # cgroup, taking update.sh (and us) down with it (code -15). That is NOT a
    # failure. Leave the state ``running`` and let ``resolve_pending()`` settle
    # the real outcome once the service comes back up on the new code.
    if code < 0:
        return
    # Re-derive the version — a successful in-place rebuild restamps VERSION.
    to_version = current_version() if callable(current_version) else current_version
    tail = read_log_tail(data_dir).lower()
    state.returncode = code
    state.finished_at = now()
    state.to_version = to_version
    if code == 0:
        state.phase = PHASE_SUCCESS
        state.message = "Update abgeschlossen."
    elif _SENTINEL_FAIL in tail:
        state.phase = PHASE_FAILED
        state.message = "Dienst nach dem Update nicht erreichbar."
    else:
        state.phase = PHASE_FAILED
        state.message = f"Update fehlgeschlagen (Code {code}). Details im Protokoll unten."
    write_state(data_dir, state)


def start_update(
    data_dir: Path,
    command: str,
    from_version: str,
    *,
    current_version=None,
    now=None,
    popen=subprocess.Popen,
) -> UpdateState:
    """Launch the update command detached, streaming its output to the log.

    Returns the new ``running`` state. Raises ``RuntimeError`` if an update is
    already in progress or the command can't be started.
    """
    data_dir = Path(data_dir)
    now = time.time if now is None else now
    with _run_lock:
        if is_running(data_dir):
            raise RuntimeError("Es läuft bereits ein Update.")
        started = now()
        state = UpdateState(
            phase=PHASE_RUNNING,
            from_version=from_version,
            message="Update läuft …",
            started_at=started,
        )
        write_state(data_dir, state)
        log = _log_path(data_dir)
        log.parent.mkdir(parents=True, exist_ok=True)
        # Truncate so each run starts with a clean transcript the UI can stream.
        logf = log.open("wb")
        try:
            proc = popen(
                shlex.split(command),
                stdout=logf,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # survive our own restart
            )
        except Exception as exc:  # noqa: BLE001
            logf.close()
            state.phase = PHASE_FAILED
            state.message = f"Start fehlgeschlagen: {exc}"
            state.finished_at = now()
            write_state(data_dir, state)
            raise RuntimeError(str(exc)) from exc
        logf.close()  # the child holds its own dup'd fd; we don't need ours

    resolver = current_version if current_version is not None else from_version
    threading.Thread(
        target=_monitor, args=(proc, data_dir, resolver, now), daemon=True
    ).start()
    return state


def resolve_pending(data_dir: Path, current_version: str, *, now=None) -> UpdateState:
    """On boot, settle a ``running`` state left behind by the restart.

    Called from the app lifespan. If the previous process launched an update and
    was killed by the ensuing ``systemctl restart``, we land here running the new
    code — so the update effectively completed. Decide the concrete outcome from
    the version bump and the log's sentinels; otherwise leave it unknown but
    finished, so the UI never shows a perpetual spinner.
    """
    data_dir = Path(data_dir)
    now = time.time if now is None else now
    state = read_state(data_dir)
    if state.phase != PHASE_RUNNING:
        return state

    state.to_version = current_version
    state.finished_at = now()
    tail = read_log_tail(data_dir).lower()
    stale = state.started_at and (now() - state.started_at) > _STALE_AFTER

    if current_version and state.from_version and current_version != state.from_version:
        state.phase = PHASE_SUCCESS
        state.message = f"Aktualisiert auf {current_version}."
    elif _SENTINEL_FAIL in tail:
        # Health check ran and the service didn't answer — a genuine failure.
        state.phase = PHASE_FAILED
        state.message = "Dienst nach dem Update nicht erreichbar."
    elif _SENTINEL_OK in tail:
        state.phase = PHASE_SUCCESS
        state.message = "Update abgeschlossen."
    elif _SENTINEL_RESTARTING in tail:
        # The build finished and update.sh reached the restart step; since we're
        # now back up, the restart succeeded — a same-version rebuild counts.
        state.phase = PHASE_SUCCESS
        state.message = "Update abgeschlossen."
    elif stale:
        state.phase = PHASE_UNKNOWN
        state.message = "Update-Status unklar (unterbrochen?). Protokoll unten prüfen."
    else:
        # We restarted while an update was running but the build never reached
        # the restart step — most likely interrupted (power cut mid-rebuild).
        state.phase = PHASE_UNKNOWN
        state.message = "Neustart erkannt — Ergebnis unklar, Protokoll unten prüfen."
    write_state(data_dir, state)
    return state
