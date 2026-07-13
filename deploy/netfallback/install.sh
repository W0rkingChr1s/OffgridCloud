#!/usr/bin/env bash
# Set up the OffgridCloud network-redundancy layer (AP fallback).
#
# Opt-in, root-only. It:
#   1. ensures NetworkManager (+ dnsmasq-base for the AP's DHCP) is installed,
#   2. installs the root watchdog service that hosts the fallback AP,
#   3. grants the service user a NOPASSWD sudoers rule for apply.sh only, and
#   4. wires OGC_NET_APPLY_COMMAND into .env so "Anwenden" in the UI works.
#
# It changes nothing about who the box connects to until an admin configures
# networks + the AP in the web UI and clicks "Anwenden".
#
# Usage: sudo ./deploy/netfallback/install.sh [--prefix DIR] [--service-user USER]
set -euo pipefail

PREFIX="/opt/offgridcloud"
SERVICE_USER="offgrid"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="${2:?}"; shift 2 ;;
    --service-user) SERVICE_USER="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
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
chmod +x "$APPLY" "$WATCHDOG" "$HERE/_apply.py" 2>/dev/null || true

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
if command -v systemctl >/dev/null 2>&1; then
  systemctl enable --now NetworkManager 2>/dev/null || \
    echo "   Could not enable NetworkManager — enable it before relying on the AP fallback." >&2
fi

# --- 2. Watchdog service ----------------------------------------------------
step "Installing the network watchdog service..."
sed -e "s#/opt/offgridcloud/src/deploy/netfallback/watchdog.sh#$WATCHDOG#g" \
    -e "s#/opt/offgridcloud/data/network.json#$CONFIG_FILE#g" \
    "$HERE/offgridcloud-netwatch.service" > /etc/systemd/system/offgridcloud-netwatch.service
systemctl daemon-reload
systemctl enable --now offgridcloud-netwatch.service || \
  echo "   Watchdog did not start — check: journalctl -u offgridcloud-netwatch -e" >&2

# --- 3. sudoers rule for the apply helper ----------------------------------
step "Granting '$SERVICE_USER' a NOPASSWD rule for apply.sh..."
SUDOERS=/etc/sudoers.d/offgridcloud-network
echo "$SERVICE_USER ALL=(root) NOPASSWD: $APPLY, $APPLY \"\"" > "$SUDOERS.tmp"
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
  chown "$SERVICE_USER:$SERVICE_USER" "$ENV_FILE" 2>/dev/null || true
  echo "   Set OGC_NET_APPLY_COMMAND in $ENV_FILE"
  command -v systemctl >/dev/null 2>&1 && systemctl try-restart offgridcloud 2>/dev/null || true
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
