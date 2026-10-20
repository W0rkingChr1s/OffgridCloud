"""End-to-end tests for deploy/netfallback/watchdog.sh against a fake ``nmcli``.

The fake keeps a tiny JSON state (profiles, active connection, what a scan sees
in AP vs. client mode) and records every mutating call, so the tests can assert
how the watchdog leaves the fallback AP once a known network is back.
"""

import json
import os
import shutil
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

WATCHDOG = Path(__file__).resolve().parents[2] / "deploy" / "netfallback" / "watchdog.sh"
AP = "offgridcloud-ap"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

FAKE_NMCLI = textwrap.dedent(
    r"""
    #!/usr/bin/env python3
    import json, os, sys

    AP = "offgridcloud-ap"
    path = os.environ["FAKE_NM_STATE"]
    st = json.load(open(path))
    args = sys.argv[1:]
    if args[:1] == ["--wait"]:
        args = args[2:]
    profiles = {p["name"]: p for p in st["profiles"]}
    active = st.get("active")

    def save():
        json.dump(st, open(path, "w"))

    def esc(s):
        return s.replace("\\", "\\\\").replace(":", "\\:")

    if args[:2] == ["connection", "up"] or args[:2] == ["connection", "down"]:
        verb, name = args[1], args[2]
        st["calls"].append(f"{verb} {name}")
        if verb == "down":
            if active == name:
                st["active"] = None
        else:
            if name != AP and name not in st.get("joinable", []):
                save(); sys.exit(4)
            st["active"] = name
        save(); sys.exit(0)

    f = args[args.index("-f") + 1] if "-f" in args else ""
    if f == "DEVICE,TYPE,STATE,CONNECTION":
        print("lo:loopback:connected (externally):lo")
        print(f"wlan0:wifi:{'connected' if active else 'disconnected'}:{esc(active or '')}")
    elif f == "IP4.ADDRESS":
        if active == AP:
            print("IP4.ADDRESS[1]:10.42.0.1/24")
        elif active:
            print("IP4.ADDRESS[1]:192.168.178.50/24")
    elif f == "NAME" and "--active" in args:
        if active:
            print(active)
    elif f == "NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY":
        for p in st["profiles"]:
            print(f"{esc(p['name'])}:802-11-wireless:{p.get('auto', 'yes')}:{p.get('prio', 0)}")
    elif f in ("802-11-wireless.mode", "802-11-wireless.ssid"):
        p = profiles[args[-1]]
        key = f.split(".")[1]
        print(f"{f}:{p.get(key, 'infrastructure' if key == 'mode' else '')}")
    elif f == "SSID":
        seen = st["scan_ap"] if active == AP else st["scan_client"]
        st["calls"].append("scan-ap" if active == AP else "scan-client")
        save()
        for s in seen:
            print(esc(s))
    sys.exit(0)
    """
)


def _setup(tmp_path: Path, **state) -> tuple[dict, Path]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "nmcli"
    fake.write_text(FAKE_NMCLI.lstrip())
    fake.chmod(0o755)

    base = {
        "profiles": [
            {"name": AP, "ssid": "OffgridCloud", "mode": "ap", "auto": "no"},
            {"name": "netplan-wlan0-Home", "ssid": "Home"},
        ],
        "active": AP,
        "scan_ap": [],
        "scan_client": [],
        "joinable": ["netplan-wlan0-Home"],
        "calls": [],
    }
    base.update(state)
    state_file = tmp_path / "nm.json"
    state_file.write_text(json.dumps(base))

    config = tmp_path / "network.json"
    config.write_text(
        json.dumps({"fallback_enabled": True, "ap_connection_name": AP, "fail_threshold": 3})
    )
    rundir = tmp_path / "run"
    rundir.mkdir()
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "FAKE_NM_STATE": str(state_file),
        "OGC_NETWATCH_STATE_DIR": str(rundir),
        "OGC_NETWATCH_SCAN_SETTLE": "0",
        "OGC_NETWATCH_JOIN_WAIT": "1",
    }
    return env, config


def _run(env: dict, config: Path) -> tuple[dict, str]:
    proc = subprocess.run(
        ["bash", str(WATCHDOG), "--oneshot", str(config)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(Path(env["FAKE_NM_STATE"]).read_text()), proc.stdout


def test_leaves_ap_when_known_network_visible_while_hosting(tmp_path):
    env, config = _setup(tmp_path, scan_ap=["Home", "Nachbar"])
    st, out = _run(env, config)
    assert st["active"] == "netplan-wlan0-Home"
    assert st["calls"] == ["scan-ap", f"down {AP}", "up netplan-wlan0-Home"]
    assert "rejoined 'Home'" in out


def test_blind_probe_when_driver_cannot_scan_in_ap_mode(tmp_path):
    env, config = _setup(tmp_path, scan_ap=[], scan_client=["Home"])
    st, _ = _run(env, config)
    assert st["active"] == "netplan-wlan0-Home"
    assert st["calls"] == ["scan-ap", f"down {AP}", "scan-client", "up netplan-wlan0-Home"]


def test_restores_ap_when_nothing_known_in_range(tmp_path):
    env, config = _setup(tmp_path, scan_ap=[], scan_client=["Fremd"])
    st, out = _run(env, config)
    assert st["active"] == AP
    assert st["calls"] == ["scan-ap", f"down {AP}", "scan-client", f"up {AP}"]
    assert "restoring fallback AP" in out


def test_restores_ap_when_join_fails(tmp_path):
    env, config = _setup(tmp_path, scan_ap=["Home"], joinable=[])
    st, _ = _run(env, config)
    assert st["active"] == AP
    assert st["calls"][-1] == f"up {AP}"


def test_stale_ap_scan_does_not_block_blind_probe(tmp_path):
    # AP-mode scan returns (stale) neighbours but not the router: must still
    # fall back to the blind probe instead of staying in AP mode forever.
    env, config = _setup(tmp_path, scan_ap=["Nachbar"], scan_client=["Home"])
    st, _ = _run(env, config)
    assert st["active"] == "netplan-wlan0-Home"


def test_probe_respects_intervals(tmp_path):
    env, config = _setup(tmp_path, scan_ap=[], scan_client=["Fremd"])
    _run(env, config)
    st, _ = _run(env, config)  # immediately again: probe interval not elapsed
    assert st["calls"].count("scan-ap") == 1

    # Probe interval elapsed, blind interval not: scan while hosting only.
    probe = Path(env["OGC_NETWATCH_STATE_DIR"]) / "ap-probe-last"
    probe.write_text(str(int(time.time()) - 1000))
    st, _ = _run(env, config)
    assert st["calls"].count("scan-ap") == 2
    assert st["calls"].count(f"down {AP}") == 1
    assert st["active"] == AP


def test_ignores_profiles_without_autoconnect(tmp_path):
    env, config = _setup(
        tmp_path,
        profiles=[
            {"name": AP, "ssid": "OffgridCloud", "mode": "ap", "auto": "no"},
            {"name": "manual", "ssid": "Home", "auto": "no"},
        ],
        scan_ap=["Home"],
        joinable=["manual"],
    )
    st, out = _run(env, config)
    assert st["active"] == AP
    assert "no known Wi-Fi profiles" in out


def test_prefers_higher_priority_profile(tmp_path):
    env, config = _setup(
        tmp_path,
        profiles=[
            {"name": AP, "ssid": "OffgridCloud", "mode": "ap", "auto": "no"},
            {"name": "netplan-wlan0-Home", "ssid": "Home", "prio": 0},
            {"name": "ogc-wifi-buero", "ssid": "Buero", "prio": 10},
        ],
        scan_ap=["Home", "Buero"],
        joinable=["netplan-wlan0-Home", "ogc-wifi-buero"],
    )
    st, _ = _run(env, config)
    assert st["active"] == "ogc-wifi-buero"
