#!/usr/bin/env python3
"""Render hostapd's config for the upload Wi-Fi from the backend's handover.json.

Invoked as root by ``handover.sh up``. Validates everything that ends up in a
config file or on a command line, writes the hostapd config (0600, it holds the
phone's key) and prints the shell values handover.sh needs, on one line:

    <interface> <address/prefix> <dhcp-start> <dhcp-end> <netmask> <deadline> <country>
    <parent> <band>

``parent`` (``-`` if none) is the adapter whose Wi-Fi client the AP runs beside
as a virtual interface. One radio can only be on one channel, so the AP then
takes the channel the client is on right now (``iw dev <parent> info``).

Kept in Python so SSIDs and passphrases never pass through shell quoting. Only
stdlib.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
import sys

_IFACE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
# 5 GHz channels an AP may open without radar detection (DFS), which would
# keep it silent for a minute or longer. Whether the country allows the upper
# ones is up to the kernel's regulatory database: hostapd fails if not.
_CHANNELS_5GHZ = frozenset({36, 40, 44, 48, 149, 153, 157, 161, 165})


def client_channel(parent: str) -> int | None:
    """Channel the Wi-Fi client on ``parent`` is on, None if not connected."""
    try:
        out = subprocess.run(
            ["iw", "dev", parent, "info"], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"^\s*channel (\d+)", out, re.MULTILINE)
    return int(match.group(1)) if match else None


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def main() -> int:
    if len(sys.argv) != 3:
        return fail("usage: _handover.py <handover.json> <hostapd.conf>")
    try:
        with open(sys.argv[1], encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError) as exc:
        return fail(f"cannot read {sys.argv[1]}: {exc}")

    iface = str(cfg.get("interface", ""))
    if not _IFACE.match(iface):
        return fail(f"invalid interface {iface!r}")
    ssid = str(cfg.get("ssid", "")).encode("utf-8")
    if not 1 <= len(ssid) <= 32:
        return fail("SSID must be 1-32 bytes")
    passphrase = str(cfg.get("passphrase", ""))
    if not (8 <= len(passphrase) <= 63 and passphrase.isascii() and passphrase.isprintable()):
        return fail("passphrase must be 8-63 printable ASCII characters")
    try:
        address = ipaddress.ip_interface(str(cfg.get("address", "10.43.0.1/24")))
    except ValueError as exc:
        return fail(f"invalid address: {exc}")
    net = address.network
    if address.version != 4 or net.num_addresses < 16:
        return fail("address must be IPv4 with at least a /28")
    parent = str(cfg.get("parent", "") or "")
    if parent:
        if not _IFACE.match(parent) or parent == iface:
            return fail(f"invalid parent {parent!r}")
        channel = client_channel(parent)
        if channel is None:
            return fail(f"{parent} is not connected to a Wi-Fi network")
    else:
        channel = int(cfg.get("channel", 6))
    if 1 <= channel <= 13:
        hw_mode, band = "g", "2.4GHz"
    elif channel in _CHANNELS_5GHZ:
        hw_mode, band = "a", "5GHz"
    else:
        return fail(f"channel {channel} needs radar detection (DFS), no AP possible there")
    country = str(cfg.get("country", "")).upper()
    if country and not re.fullmatch(r"[A-Z]{2}", country):
        country = ""
    deadline = int(cfg.get("deadline", 0))

    lines = [
        f"interface={iface}",
        "driver=nl80211",
        "ctrl_interface=/run/hostapd",
        # Hex form: no escaping questions whatever the box is called.
        f"ssid2={ssid.hex()}",
        f"hw_mode={hw_mode}",
        f"channel={channel}",
        "ieee80211n=1",
        "wmm_enabled=1",
        "auth_algs=1",
        "wpa=2",
        "wpa_key_mgmt=WPA-PSK",
        "rsn_pairwise=CCMP",
        f"wpa_passphrase={passphrase}",
        # Upload only, no reason for anyone to roam in; the key is per phone.
        "max_num_sta=2",
        "ignore_broadcast_ssid=0",
    ]
    if country:
        lines += [f"country_code={country}", "ieee80211d=1"]

    fd = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    hosts = list(net.hosts())
    start, end = hosts[9], hosts[min(49, len(hosts) - 1)]
    print(
        iface, address.with_prefixlen, start, end, net.netmask, deadline, country or "-",
        parent or "-", band,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
