#!/usr/bin/env python3
"""Render NetworkManager connections from OffgridCloud's exported config.

Invoked as root by ``apply.sh``. Reads the JSON the app writes
(``<data_dir>/network.json``) and makes NetworkManager reflect it:

* one client connection per "known network" (auto-join, by priority), and
* the fallback access point the box hosts when nothing else is reachable.

It does NOT decide *when* to host the AP — that is the watchdog's job. It only
ensures the connections exist (and tears the AP down if the fallback is
disabled), then nudges the watchdog to converge immediately.

Kept in Python (not pure bash) so variable-length network lists and Wi-Fi
passphrases are handled without shell-quoting pitfalls. Only stdlib + nmcli.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

AP_DEFAULT = "offgridcloud-ap"
CLIENT_PREFIX_DEFAULT = "ogc-wifi-"


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    print("+ nmcli " + " ".join(args))
    proc = subprocess.run(["nmcli", *args], capture_output=True, text=True, check=False)
    if proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.returncode != 0 and check:
        print(proc.stderr.strip(), file=sys.stderr)
        raise SystemExit(f"nmcli failed: {' '.join(args)}")
    return proc


def wifi_interface() -> str:
    proc = subprocess.run(
        ["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in proc.stdout.splitlines():
        dev, _, dtype = line.partition(":")
        if dtype == "wifi":
            return dev
    return "*"  # let NetworkManager pick


def existing_connections() -> list[str]:
    proc = subprocess.run(
        ["nmcli", "-t", "-f", "NAME", "connection", "show"],
        capture_output=True,
        text=True,
        check=False,
    )
    return [line for line in proc.stdout.splitlines() if line]


def slug(ssid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", ssid).strip("-").lower() or "net"


def delete_if_exists(name: str, existing: list[str]) -> None:
    if name in existing:
        run(["connection", "delete", name], check=False)


def apply_known(net: dict, prefix: str, ifname: str, existing: list[str]) -> str:
    ssid = net["ssid"]
    name = f"{prefix}{slug(ssid)}"
    delete_if_exists(name, existing)
    run(["connection", "add", "type", "wifi", "con-name", name, "ifname", ifname, "ssid", ssid])
    mods = [
        "connection.autoconnect",
        "yes" if net.get("autoconnect", True) else "no",
        "connection.autoconnect-priority",
        str(int(net.get("priority", 0))),
    ]
    psk = net.get("passphrase") or ""
    if psk:
        mods += ["802-11-wireless-security.key-mgmt", "wpa-psk", "802-11-wireless-security.psk", psk]
    run(["connection", "modify", name, *mods])
    return name


def apply_ap(ap: dict, name: str, ifname: str, existing: list[str]) -> None:
    ssid = ap.get("ssid") or "OffgridCloud"
    address = ap.get("address") or "10.42.0.1/24"
    delete_if_exists(name, existing)
    run(["connection", "add", "type", "wifi", "con-name", name, "ifname", ifname, "ssid", ssid])
    mods = [
        "802-11-wireless.mode", "ap",
        "802-11-wireless.band", "bg",
        "802-11-wireless.hidden", "yes" if ap.get("hidden") else "no",
        "ipv4.method", "shared",
        "ipv4.addresses", address,
        # The watchdog controls activation; never auto-start the AP itself.
        "connection.autoconnect", "no",
    ]
    psk = ap.get("passphrase") or ""
    if psk:
        mods += [
            "802-11-wireless-security.key-mgmt", "wpa-psk",
            "802-11-wireless-security.psk", psk,
            "802-11-wireless-security.proto", "rsn",
            "802-11-wireless-security.group", "ccmp",
            "802-11-wireless-security.pairwise", "ccmp",
        ]
    run(["connection", "modify", name, *mods])


def set_regulatory_domain(country: str) -> None:
    if not country:
        return
    try:
        subprocess.run(["iw", "reg", "set", country], check=False, capture_output=True)
        print(f"+ iw reg set {country}")
    except FileNotFoundError:
        print("note: 'iw' not found — skipping regulatory-domain set")


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "/opt/offgridcloud/data/network.json"
    try:
        config = json.loads(open(path, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read config {path}: {exc}", file=sys.stderr)
        return 1

    ap_name = config.get("ap_connection_name") or AP_DEFAULT
    prefix = config.get("client_prefix") or CLIENT_PREFIX_DEFAULT
    ifname = wifi_interface()
    existing = existing_connections()
    set_regulatory_domain(config.get("ap", {}).get("country", ""))

    keep = set()
    for net in config.get("known_networks", []):
        if net.get("ssid"):
            keep.add(apply_known(net, prefix, ifname, existing))

    # Prune client connections we manage but that are no longer configured.
    for name in existing:
        if name.startswith(prefix) and name not in keep:
            run(["connection", "delete", name], check=False)

    if config.get("fallback_enabled"):
        apply_ap(config.get("ap", {}), ap_name, ifname, existing)
    else:
        # Fallback off — make sure the AP is down and gone.
        run(["connection", "down", ap_name], check=False)
        delete_if_exists(ap_name, existing)

    print("Applied network configuration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
