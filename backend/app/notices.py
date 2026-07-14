"""In-memory ring of recent server notices for the live UI toaster.

The backend announcements (startup, reconnect, bandwidth pause/resume) reach
external channels via :mod:`app.notify`. To also surface them as in-app toasts,
each announcement pushes a *notice* here; the SSE snapshot (see
``routers/events.py``) carries the most recent ones, and the frontend toaster
raises a toast for any it hasn't seen yet (tracked by the monotonic ``id``).

Deliberately tiny and process-local: a handful of ephemeral status blips, not a
durable log (that's the audit trail). Thread-safe because it's written from the
worker/reconcile threads and read from the request threadpool.
"""

from __future__ import annotations

import threading
from collections import deque

# Keep only the last few; a client that reconnects should replay at most this
# many, and the frontend only toasts ids newer than the last it saw.
_MAX_NOTICES = 20

_lock = threading.Lock()
_notices: deque[dict] = deque(maxlen=_MAX_NOTICES)
_seq = 0


def push(level: str, title: str, message: str = "") -> dict:
    """Append a notice and return it (with its assigned ``id``).

    ``level`` mirrors the toast variants: ``success`` | ``error`` | ``info`` |
    ``warning``.
    """
    global _seq
    with _lock:
        _seq += 1
        notice = {"id": _seq, "level": level, "title": title, "message": message}
        _notices.append(notice)
        return dict(notice)


def recent() -> list[dict]:
    """Snapshot of the buffered notices, oldest first."""
    with _lock:
        return [dict(n) for n in _notices]


def reset() -> None:
    """Clear all notices (used by tests)."""
    global _seq
    with _lock:
        _notices.clear()
        _seq = 0
