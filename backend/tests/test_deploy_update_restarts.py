"""Regression: a one-click update must not be cut short by a service restart.

The portal starts ``update.sh`` inside offgridcloud.service's cgroup. A restart
of that unit in the middle of ``install.sh`` (netfallback/vpn sub-installers ran
``systemctl try-restart offgridcloud``) killed the whole update after the venv
had been rebuilt but before the Bluetooth step reinstalled dbus-fast — and
offgridcloud-ble failed with ``No module named 'dbus_fast'`` after the next
reboot. These checks are static: the scripts need root and systemd to run.
"""

from __future__ import annotations

import re
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"

# Sub-installers install.sh runs while an update is in progress.
SUB_INSTALLERS = ["netfallback/install.sh", "vpn/install.sh", "netmerge/install.sh"]


def _web_restarts(text: str) -> list[int]:
    """Line indexes that restart the web unit (not -ble, -netwatch, …)."""
    pattern = re.compile(
        r"systemctl (try-)?restart \"?(offgridcloud|\$\{SERVICE_NAME\})(\.service)?\b(?!-)"
    )
    return [i for i, line in enumerate(text.splitlines()) if pattern.search(line)]


def test_sub_installers_only_restart_the_web_service_when_allowed():
    for rel in SUB_INSTALLERS:
        lines = (DEPLOY / rel).read_text().splitlines()
        hits = _web_restarts("\n".join(lines))
        assert hits, f"{rel}: expected a (guarded) restart of the web service"
        for i in hits:
            context = "\n".join(lines[max(0, i - 3) : i + 1])
            assert re.search(r"\$\{?(DO_)?START\b", context), (
                f"{rel}:{i + 1} restarts offgridcloud without a --no-start guard"
            )
        assert "--no-start)" in "\n".join(lines), f"{rel}: no --no-start option"


def test_install_passes_no_start_to_sub_installers():
    text = (DEPLOY / "install.sh").read_text()
    for name in ("NETFALLBACK_ARGS", "VPN_ARGS", "NETMERGE_ARGS"):
        assert re.search(rf"\[\[ \$DO_START -eq 1 \]\] \|\| {name}\+=\(--no-start\)", text), name


def test_install_puts_dbus_fast_into_the_fresh_venv_right_away():
    text = (DEPLOY / "install.sh").read_text()
    venv = text.index('python3 -m venv "$PREFIX/backend/.venv"')
    ble_req = text.index("requirements-ble.txt", venv)
    next_section = text.index("# --- Configuration", venv)
    assert ble_req < next_section


def test_update_leaves_the_service_cgroup_before_doing_anything():
    text = (DEPLOY / "update.sh").read_text()
    detach = text.index("exec systemd-run")
    assert detach < text.index('git -C "$SRC" fetch')
    block = text[text.index("OGC_UPDATE_DETACHED") : detach + 400]
    # A transient service, not a scope: sudo relays SIGTERM to its child.
    assert "--scope" not in block
    assert "--setenv=OGC_UPDATE_DETACHED=1" in block
    # BLE restarts before the web service, whose restart may still end us.
    assert text.index("systemctl restart offgridcloud-ble") < text.index(
        "systemctl restart offgridcloud\n"
    )
