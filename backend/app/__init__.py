"""OffgridCloud backend package."""

from pathlib import Path


def _read_version() -> str:
    """Deployed version, stamped from the git tag by the installer.

    install.sh / update.sh write the checked-out release tag into a ``VERSION``
    file next to this package, so the UI reports the real deployed release
    instead of a hand-maintained constant. Falls back to the constant below for
    plain dev checkouts (no VERSION file).
    """
    try:
        stamped = (Path(__file__).with_name("VERSION")).read_text().strip()
    except OSError:
        stamped = ""
    return stamped or "0.1.0"


__version__ = _read_version()
