#!/usr/bin/env bash
# Remove a native OffgridCloud install created by deploy/install.sh.
#
# Usage:
#   sudo ./deploy/uninstall.sh [--prefix DIR] [--purge] [--remove-user]
#
# Options:
#   --prefix DIR    Install location (default: /opt/offgridcloud).
#   --purge         Also delete data/ (database + media buffer). DESTRUCTIVE.
#   --remove-user   Also delete the 'offgrid' service user.
set -euo pipefail

PREFIX="/opt/offgridcloud"
SERVICE_USER="offgrid"
PURGE=0
REMOVE_USER=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="${2:?}"; shift 2 ;;
    --purge) PURGE=1; shift ;;
    --remove-user) REMOVE_USER=1; shift ;;
    -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then echo "Please run as root (sudo)." >&2; exit 1; fi

echo ">> Stopping and disabling the service..."
systemctl disable --now offgridcloud 2>/dev/null || true
rm -f /etc/systemd/system/offgridcloud.service
rm -f /etc/sudoers.d/offgridcloud
systemctl daemon-reload 2>/dev/null || true

echo ">> Removing the on-box console (kiosk), if installed..."
systemctl disable --now offgrid-kiosk.service 2>/dev/null || true
rm -f /etc/systemd/system/offgrid-kiosk.service
# Hand tty1 back to a normal login prompt.
systemctl unmask getty@tty1.service 2>/dev/null || true
systemctl daemon-reload 2>/dev/null || true
systemctl start getty@tty1.service 2>/dev/null || true

if [[ $PURGE -eq 1 ]]; then
  echo ">> Purging everything under $PREFIX (including data)..."
  rm -rf "$PREFIX"
else
  echo ">> Removing app files but KEEPING $PREFIX/data and $PREFIX/.env..."
  find "$PREFIX" -mindepth 1 -maxdepth 1 \
    ! -name data ! -name .env -exec rm -rf {} + 2>/dev/null || true
  echo "   Kept: $PREFIX/data and $PREFIX/.env  (use --purge to remove them too)"
fi

if [[ $REMOVE_USER -eq 1 ]]; then
  echo ">> Removing service user '$SERVICE_USER'..."
  userdel "$SERVICE_USER" 2>/dev/null || true
fi

echo "Done."
