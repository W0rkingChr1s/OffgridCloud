#!/usr/bin/env bash
# Set up Bluetooth management for the companion app (offgridcloud-ble).
#
# Root-only. It:
#   1. installs BlueZ (if missing) and unblocks the radio,
#   2. adds the service user to the "bluetooth" group,
#   3. installs dbus-fast into the app's venv (requirements-ble.txt), and
#   4. installs + enables the offgridcloud-ble systemd service.
#
# Pairing still needs a code from the web portal (Bluetooth → Gerät koppeln);
# admins can switch Bluetooth off there. Protocol: docs/BLE-PROTOKOLL.md.
#
# Usage: sudo ./deploy/ble/install.sh [--prefix DIR] [--service-user USER] [--no-start]
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
    -h|--help) sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

step() { printf '\n\033[1;36m>> %s\033[0m\n' "$1"; }

VENV="$PREFIX/backend/.venv"
UNIT=/etc/systemd/system/offgridcloud-ble.service

# --- 1. BlueZ -----------------------------------------------------------------
step "Ensuring BlueZ is installed..."
if ! command -v bluetoothctl >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y --no-install-recommends bluez
  else
    echo "   bluetoothctl missing and no apt-get here — install BlueZ manually." >&2
  fi
fi
command -v rfkill >/dev/null 2>&1 && rfkill unblock bluetooth 2>/dev/null || true
command -v systemctl >/dev/null 2>&1 && systemctl enable bluetooth 2>/dev/null || true

# --- 2. Group -------------------------------------------------------------------
step "Adding $SERVICE_USER to the bluetooth group..."
getent group bluetooth >/dev/null || groupadd --system bluetooth
usermod -aG bluetooth "$SERVICE_USER"

# --- 3. Python dependency ------------------------------------------------------
step "Installing dbus-fast into the app venv..."
if [[ -x "$VENV/bin/pip" ]]; then
  "$VENV/bin/pip" install --quiet -r "$PREFIX/backend/requirements-ble.txt"
else
  echo "   $VENV not found — run deploy/install.sh first." >&2
  exit 1
fi

# --- 4. Service -------------------------------------------------------------------
step "Installing the offgridcloud-ble service..."
sed -e "s#/opt/offgridcloud#$PREFIX#g" \
    -e "s#^User=offgrid#User=$SERVICE_USER#" \
    -e "s#^Group=offgrid#Group=$SERVICE_USER#" \
    "$HERE/offgridcloud-ble.service" > "$UNIT"
systemctl daemon-reload
systemctl enable offgridcloud-ble.service
if [[ $START -eq 1 ]]; then
  systemctl restart bluetooth.service 2>/dev/null || true
  systemctl restart offgridcloud-ble.service
  echo "   Running. Logs: journalctl -u offgridcloud-ble -f"
else
  echo "   Enabled; starts on next boot."
fi
