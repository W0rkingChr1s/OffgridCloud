#!/usr/bin/env bash
# Remove the offgridcloud-ble service. BlueZ itself and paired-device records
# in the database stay (they are harmless without the service).
#
# Usage: sudo ./deploy/ble/uninstall.sh
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi
systemctl disable --now offgridcloud-ble.service 2>/dev/null || true
rm -f /etc/systemd/system/offgridcloud-ble.service
systemctl daemon-reload 2>/dev/null || true
echo "offgridcloud-ble removed."
