#!/usr/bin/env python3
"""Render hostapd's config for the upload Wi-Fi from the backend's handover.json.

Invoked as root by ``handover.sh up``. Validates everything that ends up in a
config file or on a command line, writes the hostapd config (0600, it holds the
phone's key) and prints the shell values handover.sh needs, one per line:

    <interface> <address/prefix> <dhcp-start> <dhcp-end> <netmask> <deadline> <country>

Kept in Python so SSIDs and passphrases never pass through shell quoting. Only
stdlib.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import sys

_IFACE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")


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
    channel = int(cfg.get("channel", 6))
    if not 1 <= channel <= 13:
        return fail("channel must be 1-13 (2.4 GHz)")
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
        "hw_mode=g",
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
    print(iface, address.with_prefixlen, start, end, net.netmask, deadline, country or "-")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
