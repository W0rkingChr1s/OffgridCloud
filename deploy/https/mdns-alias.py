#!/usr/bin/env python3
"""Publish a fixed mDNS alias (default ``offgridcloud.local``) for this box.

The setup wizard gives every box a unique hostname (``offgridcloud-xxxxx``),
which avahi publishes as ``<hostname>.local``. On top of that we want the easy
name ``offgridcloud.local`` to work too. avahi-daemon itself only publishes the
hostname, so this small daemon adds A records for the alias over avahi's D-Bus
API — one per interface with that interface's own IPv4 address, so a phone on
the fallback AP gets 10.42.0.1 and a laptop on the LAN gets the LAN address.

Addresses change (DHCP, AP fallback, cable in/out) and avahi gets restarted
when the hostname changes, so the loop re-checks every few seconds and
re-publishes when anything differs. If another device already owns the alias
(a second OffgridCloud box on the same LAN), avahi reports a collision; we log
it and keep retrying — the unique ``<hostname>.local`` keeps working either way.

Usage: mdns-alias.py [ALIAS]     (ALIAS without ".local", default offgridcloud)
"""
import socket
import subprocess
import sys
import time

import dbus

ALIAS = (sys.argv[1] if len(sys.argv) > 1 else "offgridcloud").lower().removesuffix(".local")
FQDN = f"{ALIAS}.local"
INTERVAL = 5  # seconds between checks

PROTO_INET = 0               # AVAHI_PROTO_INET
PUBLISH_NO_REVERSE = 16      # AVAHI_PUBLISH_NO_REVERSE: the PTR belongs to the real hostname
STATE_COLLISION, STATE_FAILURE = 3, 4  # AvahiEntryGroupState


def log(msg: str) -> None:
    print(f"[mdns-alias] {msg}", flush=True)


def current_addresses() -> dict[int, str]:
    """{ifindex: first IPv4} for every up, non-loopback interface."""
    out = subprocess.run(["ip", "-o", "-4", "addr", "show", "up"],
                         capture_output=True, text=True, check=False).stdout
    addrs: dict[int, str] = {}
    for line in out.splitlines():
        # "2: eth0    inet 192.168.1.20/24 brd ... scope global dynamic eth0"
        parts = line.split()
        if len(parts) < 4 or parts[2] != "inet" or "scope host" in line:
            continue
        idx = int(parts[0].rstrip(":"))
        addrs.setdefault(idx, parts[3].split("/")[0])
    return addrs


def alias_is_hostname() -> bool:
    """avahi already publishes the alias when it *is* the hostname."""
    return socket.gethostname().split(".")[0].lower() == ALIAS


class Publisher:
    def __init__(self) -> None:
        self.group = None
        self.published: dict[int, str] = {}

    def reset(self) -> None:
        if self.group is not None:
            try:
                self.group.Free()
            except dbus.DBusException:
                pass  # avahi restarted — the group is already gone
        self.group = None
        self.published = {}

    def publish(self, addrs: dict[int, str]) -> None:
        self.reset()
        bus = dbus.SystemBus()
        server = dbus.Interface(bus.get_object("org.freedesktop.Avahi", "/"),
                                "org.freedesktop.Avahi.Server")
        group = dbus.Interface(bus.get_object("org.freedesktop.Avahi", server.EntryGroupNew()),
                               "org.freedesktop.Avahi.EntryGroup")
        added: dict[int, str] = {}
        for idx, addr in addrs.items():
            try:
                group.AddAddress(dbus.Int32(idx), dbus.Int32(PROTO_INET),
                                 dbus.UInt32(PUBLISH_NO_REVERSE), FQDN, addr)
                added[idx] = addr
            except dbus.DBusException as exc:  # e.g. interface avahi doesn't serve (wg0)
                log(f"skip {addr} on if{idx}: {exc.get_dbus_message()}")
        if added:
            group.Commit()
        self.group, self.published = group, addrs
        log(f"{FQDN} -> {', '.join(added.values()) or 'nothing'}")

    def healthy(self) -> bool:
        if self.group is None:
            return False
        try:
            state = self.group.GetState()
        except dbus.DBusException:
            return False  # avahi-daemon restarted
        if state in (STATE_COLLISION, STATE_FAILURE):
            log(f"{FQDN} already taken on the network (state {state}) — retrying later")
            return False
        return True


def main() -> None:
    pub = Publisher()
    collisions = 0
    while True:
        try:
            if alias_is_hostname():
                pub.reset()
            else:
                addrs = current_addresses()
                if addrs != pub.published or not pub.healthy():
                    if pub.group is not None and pub.published == addrs:
                        collisions += 1  # unchanged addresses: back off on repeated collisions
                    else:
                        collisions = 0
                    pub.publish(addrs)
        except dbus.DBusException as exc:  # avahi not (yet) up
            log(f"avahi unavailable: {exc.get_dbus_message()}")
            pub.reset()
        time.sleep(INTERVAL * min(1 + collisions, 12))


if __name__ == "__main__":
    main()
