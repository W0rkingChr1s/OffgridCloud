#!/usr/bin/env bash
# Prepare an OffgridCloud install for the first-boot setup wizard (root).
#
# Used when building the flashable image (deploy/install.sh with
# OGC_IMAGE_BUILD=1). It
#   1. installs the firstboot + setup-Wi-Fi units and arms them,
#   2. grants the service user a NOPASSWD sudoers rule for finish.sh only, and
#   3. switches the app into wizard mode in .env (OGC_SETUP_WIZARD=true).
#
# Works inside a chroot (pi-gen): only `systemctl enable`, no starting.
#
# Usage: sudo ./deploy/setup/install.sh [--prefix DIR] [--service-user USER]
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
if [[ $EUID -ne 0 ]]; then echo "Please run as root (sudo)." >&2; exit 1; fi

chmod +x "$HERE/"*.sh
for unit in offgridcloud-firstboot.service offgridcloud-setup-ap.service; do
  sed "s#/opt/offgridcloud/deploy/setup#$HERE#g" "$HERE/$unit" > "/etc/systemd/system/$unit"
  systemctl enable "$unit"
done
install -d -m 700 /var/lib/offgridcloud
touch /var/lib/offgridcloud/firstboot-pending

SUDOERS=/etc/sudoers.d/offgridcloud-setup
echo "$SERVICE_USER ALL=(root) NOPASSWD: $HERE/finish.sh" > "$SUDOERS.tmp"
if visudo -cf "$SUDOERS.tmp" >/dev/null 2>&1; then
  install -m 440 "$SUDOERS.tmp" "$SUDOERS"; rm -f "$SUDOERS.tmp"
else
  rm -f "$SUDOERS.tmp"
  echo "Could not validate the sudoers rule for finish.sh." >&2; exit 1
fi

ENV_FILE="$PREFIX/.env"
for kv in "OGC_SETUP_WIZARD=true" "OGC_SETUP_FINISH_COMMAND=sudo $HERE/finish.sh"; do
  grep -q "^${kv%%=*}=" "$ENV_FILE" || echo "$kv" >> "$ENV_FILE"
done
chown "$SERVICE_USER:$SERVICE_USER" "$ENV_FILE"
echo "   Setup wizard armed: the box starts in setup mode on its first boot."
