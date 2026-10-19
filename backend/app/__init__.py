"""OffgridCloud backend package."""

import re
from pathlib import Path

CHANNEL_RELEASE = "release"
CHANNEL_MAIN = "main"

# ``git describe`` output past a tag: ``0.4.10-44-g97b8eb0``.
_DESCRIBE_RE = re.compile(r"-\d+-g([0-9a-f]{7,40})$")


def _read_stamp(name: str) -> str:
    try:
        return (Path(__file__).with_name(name)).read_text().strip()
    except OSError:
        return ""


def _read_version() -> str:
    """Deployed version, stamped from the git tag by the installer.

    install.sh / update.sh write the checked-out release tag into a ``VERSION``
    file next to this package, so the UI reports the real deployed release
    instead of a hand-maintained constant. Falls back to the constant below for
    plain dev checkouts (no VERSION file).
    """
    return _read_stamp("VERSION") or "0.1.0"


def _read_commit() -> str:
    """Deployed git commit (``COMMIT`` stamp), else the hash in a describe version."""
    stamped = _read_stamp("COMMIT")
    if stamped:
        return stamped
    match = _DESCRIBE_RE.search(_read_version())
    return match.group(1) if match else ""


def _read_channel() -> str:
    """Update channel this install follows: ``release`` (tags) or ``main``.

    install.sh stamps it into ``CHANNEL``. Installs from before that stamp are
    inferred from the version: a describe string past a tag (``0.4.10-44-g…``)
    can only come from a main checkout.
    """
    stamped = _read_stamp("CHANNEL")
    if stamped in (CHANNEL_RELEASE, CHANNEL_MAIN):
        return stamped
    return CHANNEL_MAIN if _DESCRIBE_RE.search(_read_version()) else CHANNEL_RELEASE


__version__ = _read_version()
