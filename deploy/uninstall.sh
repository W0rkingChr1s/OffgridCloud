#!/usr/bin/env bash
# Remove a native OffgridCloud install created by deploy/install.sh.
#
# Undoes everything the installer (and its opt-in features) set up: the systemd
# service + VPN drop-in, all sudoers rules, the on-box console (kiosk), the
# HTTPS reverse proxy (Caddy config + hostname), and the network-redundancy
# layer (watchdog + fallback AP). Packages installed along the way (caddy,
# avahi, rclone, ffmpeg, NetworkManager, speedtest) stay — they are generic
# tools and removing them could break unrelated setups.
#
# Usage:
#   sudo ./deploy/uninstall.sh [--prefix DIR] [--purge] [--remove-user]
#
# Options:
#   --prefix DIR    Install location (default: /opt/offgridcloud).
#   --purge         Also delete data/ (database + media buffer) and the
#                   Wi-Fi profiles (ogc-wifi-*) the app created. DESTRUCTIVE.
#   --remove-user   Also delete the 'offgrid' service user (incl. home dir).
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
    -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then echo "Please run as root (sudo)." >&2; exit 1; fi

echo ">> Stopping and disabling the service..."
systemctl disable --now offgridcloud 2>/dev/null || true
rm -f /etc/systemd/system/offgridcloud.service
# VPN feature: CAP_NET_ADMIN drop-in dir + tun module autoload.
rm -rf /etc/systemd/system/offgridcloud.service.d
rm -f /etc/modules-load.d/offgridcloud-tun.conf
# All NOPASSWD rules the installer (and its features) created.
rm -f /etc/sudoers.d/offgridcloud \
      /etc/sudoers.d/offgridcloud-power \
      /etc/sudoers.d/offgridcloud-https \
      /etc/sudoers.d/offgridcloud-network
systemctl daemon-reload 2>/dev/null || true

echo ">> Removing the network-redundancy layer (watchdog + fallback AP), if installed..."
systemctl disable --now offgridcloud-netwatch.service 2>/dev/null || true
rm -f /etc/systemd/system/offgridcloud-netwatch.service
if command -v nmcli >/dev/null 2>&1; then
  # The fallback AP is ours alone — take it down and delete the profile. Its
  # name is configurable; read it from network.json, fall back to the default.
  AP_NAME="offgridcloud-ap"
  if [[ -f "$PREFIX/data/network.json" ]]; then
    _v="$(sed -n 's/.*"ap_connection_name"[: ]*"\([^"]*\)".*/\1/p' "$PREFIX/data/network.json" | head -1)"
    [[ -n "$_v" ]] && AP_NAME="$_v"
  fi
  nmcli connection down "$AP_NAME" >/dev/null 2>&1 || true
  nmcli connection delete "$AP_NAME" >/dev/null 2>&1 \
    && echo "   Removed fallback AP profile: $AP_NAME"
  # Client Wi-Fi profiles (ogc-wifi-*) keep the box online — only --purge
  # removes them. Careful: this can drop the connection you are using.
  if [[ $PURGE -eq 1 ]]; then
    while IFS= read -r _con; do
      [[ -n "$_con" ]] || continue
      nmcli connection delete "$_con" >/dev/null 2>&1 \
        && echo "   Removed Wi-Fi profile: $_con"
    done < <(nmcli -t -f NAME connection show 2>/dev/null | grep '^ogc-wifi-' || true)
  else
    _kept="$(nmcli -t -f NAME connection show 2>/dev/null | grep -c '^ogc-wifi-' || true)"
    [[ "${_kept:-0}" -gt 0 ]] && \
      echo "   Kept $_kept Wi-Fi profile(s) (ogc-wifi-*) so the box stays online (use --purge to remove)."
  fi
fi

echo ">> Removing the HTTPS reverse proxy (Caddy config + hostname), if installed..."
# Only touch the Caddyfile if it is the one our apply.sh rendered — a
# hand-written Caddy setup is not ours to delete.
if [[ -f /etc/caddy/Caddyfile ]] && grep -q 'Managed by deploy/https/apply.sh' /etc/caddy/Caddyfile; then
  rm -f /etc/caddy/Caddyfile
  systemctl disable --now caddy 2>/dev/null || true
  echo "   Removed the managed Caddyfile and disabled caddy."
fi
# Restore the hostname the box had before the installer renamed it.
HOST_STATE="$PREFIX/data/https-hostname.state"
if [[ -f "$HOST_STATE" ]]; then
  PREV_HOSTNAME=""
  # shellcheck disable=SC1090
  . "$HOST_STATE" 2>/dev/null || true
  if [[ -n "$PREV_HOSTNAME" ]]; then
    hostnamectl set-hostname "$PREV_HOSTNAME" 2>/dev/null \
      && echo "   Restored hostname: $PREV_HOSTNAME"
    if grep -qE '^\s*127\.0\.1\.1' /etc/hosts; then
      sed -i -E "s/^\s*127\.0\.1\.1.*/127.0.1.1\t${PREV_HOSTNAME}/" /etc/hosts
    fi
    systemctl restart avahi-daemon 2>/dev/null || true
  fi
  rm -f "$HOST_STATE"
fi

echo ">> Removing the on-box console (kiosk), if installed..."
systemctl disable --now offgrid-kiosk.service 2>/dev/null || true
rm -f /etc/systemd/system/offgrid-kiosk.service
rm -f /usr/local/bin/offgrid-console
rm -f /etc/profile.d/offgrid-console.sh
# Hand tty1 back to a normal login prompt.
systemctl unmask getty@tty1.service 2>/dev/null || true
# Restore the boot behaviour the kiosk installer changed (desktop vs. console).
STATE_FILE="$PREFIX/data/kiosk-boot.state"
if [[ -f "$STATE_FILE" ]]; then
  # shellcheck disable=SC1090
  PREV_TARGET=""; DISABLED_DMS=""
  . "$STATE_FILE" 2>/dev/null || true
  if [[ -n "$PREV_TARGET" && "$PREV_TARGET" != "unknown" ]]; then
    systemctl set-default "$PREV_TARGET" 2>/dev/null \
      && echo "   Restored default boot target: $PREV_TARGET"
  fi
  for dm in $DISABLED_DMS; do
    systemctl enable "$dm.service" 2>/dev/null \
      && echo "   Re-enabled display manager: $dm"
  done
  rm -f "$STATE_FILE"
fi
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
  userdel -r "$SERVICE_USER" 2>/dev/null || userdel "$SERVICE_USER" 2>/dev/null || true
fi

cat <<'EOF'
Done.

Left installed (generic tools, remove manually if unwanted):
  caddy, avahi-daemon, NetworkManager, rclone, ffmpeg, /usr/local/bin/speedtest
EOF
