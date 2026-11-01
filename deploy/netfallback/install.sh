#!/usr/bin/env bash
# Set up the OffgridCloud network-redundancy layer (AP fallback).
#
# Opt-in, root-only. It:
#   1. ensures NetworkManager (+ dnsmasq-base for the AP's DHCP) is installed,
#   2. installs the root watchdog service that hosts the fallback AP,
#   3. grants the service user NOPASSWD sudoers rules for apply.sh and the
#      upload-handover helper (handover.sh up/down) only, and
#   4. wires OGC_NET_APPLY_COMMAND / OGC_NET_HANDOVER_COMMAND into .env so
#      "Anwenden" in the UI and the companion app's upload Wi-Fi work.
#
# It changes nothing about who the box connects to until an admin configures
# networks + the AP in the web UI and clicks "Anwenden".
#
# Usage: sudo ./deploy/netfallback/install.sh [--prefix DIR] [--service-user USER] [--no-start]
set -euo pipefail

PREFIX="/opt/offgridcloud"
SERVICE_USER="offgrid"
START=1
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="${2:?}"; shift 2 ;;
    --service-user) SERVICE_USER="${2:?}"; shift 2 ;;
    --no-start) START=0; shift ;;
    -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

step() { printf '\n\033[1;36m>> %s\033[0m\n' "$1"; }

CONFIG_FILE="$PREFIX/data/network.json"
APPLY="$HERE/apply.sh"
WATCHDOG="$HERE/watchdog.sh"
HANDOVER="$HERE/handover.sh"
HANDOVER_FILE="$PREFIX/data/handover.json"
chmod +x "$APPLY" "$WATCHDOG" "$HANDOVER" "$HERE/_apply.py" "$HERE/_handover.py" 2>/dev/null || true

# --- 1. NetworkManager + dnsmasq-base --------------------------------------
step "Ensuring NetworkManager is installed and running..."
if ! command -v nmcli >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y --no-install-recommends network-manager dnsmasq-base iw
  else
    echo "   nmcli missing and no apt-get here — install NetworkManager manually." >&2
  fi
else
  echo "   nmcli present: $(nmcli --version 2>/dev/null | head -1)"
  # dnsmasq-base backs NetworkManager's 'shared' IPv4 (the AP's DHCP).
  command -v apt-get >/dev/null 2>&1 && \
    dpkg -s dnsmasq-base >/dev/null 2>&1 || \
    { command -v apt-get >/dev/null 2>&1 && apt-get install -y --no-install-recommends dnsmasq-base iw || true; }
fi
# hostapd runs the companion app's upload Wi-Fi (handover.sh). Only started on
# demand by that helper — its own service must never grab the Wi-Fi adapter.
if command -v apt-get >/dev/null 2>&1 && ! command -v hostapd >/dev/null 2>&1; then
  apt-get install -y --no-install-recommends hostapd \
    || echo "   hostapd not installed — the app's upload Wi-Fi only works via the fallback AP." >&2
fi
if command -v systemctl >/dev/null 2>&1 && [[ ! -f /etc/hostapd/hostapd.conf ]]; then
  systemctl disable --now hostapd.service >/dev/null 2>&1 || true
fi
# iw adds the upload Wi-Fi as a virtual interface beside a Wi-Fi uplink, which
# NetworkManager must leave alone (handover.sh, name = app.handover.VIRTUAL_IFACE).
if command -v apt-get >/dev/null 2>&1 && ! command -v iw >/dev/null 2>&1; then
  apt-get install -y --no-install-recommends iw || true
fi
mkdir -p /etc/NetworkManager/conf.d
printf '[keyfile]\nunmanaged-devices=interface-name:ogcup0\n' \
  > /etc/NetworkManager/conf.d/offgridcloud-handover.conf
if command -v systemctl >/dev/null 2>&1; then
  systemctl enable --now NetworkManager 2>/dev/null || \
    echo "   Could not enable NetworkManager — enable it before relying on the AP fallback." >&2
fi

# --- 1b. USB tethering: phone only as the uplink of last resort ------------
if [[ -d /etc/NetworkManager/conf.d ]]; then
  install -m 644 "$HERE/offgridcloud-tether.conf" /etc/NetworkManager/conf.d/offgridcloud-tether.conf
  # Takes effect for the next activation; a phone already plugged in keeps its
  # current route until it is replugged.
  nmcli general reload conf >/dev/null 2>&1 || true
  echo "   USB tethering ranks behind cable and Wi-Fi (route metric 700)."
fi

# --- 2. Watchdog service ----------------------------------------------------
step "Installing the network watchdog service..."
sed -e "s#/opt/offgridcloud/src/deploy/netfallback/watchdog.sh#$WATCHDOG#g" \
    -e "s#/opt/offgridcloud/data/network.json#$CONFIG_FILE#g" \
    "$HERE/offgridcloud-netwatch.service" > /etc/systemd/system/offgridcloud-netwatch.service
systemctl daemon-reload
systemctl enable offgridcloud-netwatch.service >/dev/null 2>&1 || true
# Restart, not just `enable --now`: on an update (install.sh/update.sh re-run
# this) the running loop keeps executing the old watchdog.sh it already read,
# so fixes would only take effect after the next reboot.
systemctl restart offgridcloud-netwatch.service || \
  echo "   Watchdog did not start — check: journalctl -u offgridcloud-netwatch -e" >&2

# --- 3. sudoers rule for the apply helper ----------------------------------
step "Granting '$SERVICE_USER' NOPASSWD rules for apply.sh and handover.sh..."
SUDOERS=/etc/sudoers.d/offgridcloud-network
{
  echo "$SERVICE_USER ALL=(root) NOPASSWD: $APPLY, $APPLY \"\""
  echo "$SERVICE_USER ALL=(root) NOPASSWD: $HANDOVER up $HANDOVER_FILE, $HANDOVER down"
} > "$SUDOERS.tmp"
if visudo -cf "$SUDOERS.tmp" >/dev/null 2>&1; then
  install -m 440 "$SUDOERS.tmp" "$SUDOERS"; rm -f "$SUDOERS.tmp"
  echo "   Installed $SUDOERS"
else
  rm -f "$SUDOERS.tmp"
  echo "   Could not validate sudoers rule — 'Anwenden' will export-only. Run apply.sh by hand." >&2
fi

# --- 4. Wire OGC_NET_APPLY_COMMAND into .env -------------------------------
step "Wiring the app to the apply helper..."
ENV_FILE="$PREFIX/.env"
if [[ -f "$ENV_FILE" ]]; then
  if grep -q '^OGC_NET_APPLY_COMMAND=' "$ENV_FILE"; then
    sed -i "s#^OGC_NET_APPLY_COMMAND=.*#OGC_NET_APPLY_COMMAND=sudo $APPLY $CONFIG_FILE#" "$ENV_FILE"
  else
    echo "OGC_NET_APPLY_COMMAND=sudo $APPLY $CONFIG_FILE" >> "$ENV_FILE"
  fi
  if grep -q '^OGC_NET_HANDOVER_COMMAND=' "$ENV_FILE"; then
    sed -i "s#^OGC_NET_HANDOVER_COMMAND=.*#OGC_NET_HANDOVER_COMMAND=sudo $HANDOVER#" "$ENV_FILE"
  else
    echo "OGC_NET_HANDOVER_COMMAND=sudo $HANDOVER" >> "$ENV_FILE"
  fi
  chown "$SERVICE_USER:$SERVICE_USER" "$ENV_FILE" 2>/dev/null || true
  echo "   Set OGC_NET_APPLY_COMMAND and OGC_NET_HANDOVER_COMMAND in $ENV_FILE"
  # Not during install.sh/update.sh (--no-start): they restart the web service
  # themselves at the end, and a restart mid-update cut the update short.
  if [[ $START -eq 1 ]] && command -v systemctl >/dev/null 2>&1; then
    systemctl try-restart offgridcloud 2>/dev/null || true
  fi
else
  echo "   $ENV_FILE not found — add manually:"
  echo "     OGC_NET_APPLY_COMMAND=sudo $APPLY $CONFIG_FILE"
fi

cat <<EOF

$(printf '\033[1;32mDone.\033[0m') Network-redundancy layer installed.

  Next: open the web UI → Netzwerk, set the fallback AP name/password, add the
  Wi-Fi networks the box should join, then click "Anwenden".

  The box keeps its current connection until you do. Watch the watchdog with:
    journalctl -u offgridcloud-netwatch -f
EOF
