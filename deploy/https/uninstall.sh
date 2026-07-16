#!/usr/bin/env bash
# Tear down the OffgridCloud HTTPS layer (Caddy reverse proxy + mDNS hostname).
#
# Counterpart to deploy/https/install.sh — removes only what that script set
# up: the managed Caddyfile (identified by its header), the sudoers rule, the
# OGC_HTTPS_APPLY_COMMAND wiring in .env, and the state files. Restores the
# original hostname if apply.sh recorded it. The caddy/avahi packages stay
# installed (generic tools).
#
# Called by deploy/install.sh when HTTPS is deselected on a re-run, and by
# deploy/uninstall.sh during a full uninstall. Safe to run when HTTPS was
# never set up — every step is a no-op then.
#
# Usage: sudo ./deploy/https/uninstall.sh [--prefix DIR]
set -euo pipefail

PREFIX="/opt/offgridcloud"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then echo "Please run as root (sudo)." >&2; exit 1; fi

# --- 1. Managed Caddyfile + caddy service ------------------------------------
# Only touch the Caddyfile if it is the one our apply.sh rendered — a
# hand-written Caddy setup is not ours to delete.
if [[ -f /etc/caddy/Caddyfile ]] && grep -q 'Managed by deploy/https/apply.sh' /etc/caddy/Caddyfile; then
  rm -f /etc/caddy/Caddyfile
  systemctl disable --now caddy 2>/dev/null || true
  echo "   Removed the managed Caddyfile and disabled caddy."
fi

# --- 2. sudoers rule + .env wiring -------------------------------------------
rm -f /etc/sudoers.d/offgridcloud-https
if [[ -f "$PREFIX/.env" ]]; then
  sed -i '/^OGC_HTTPS_APPLY_COMMAND=/d' "$PREFIX/.env" 2>/dev/null || true
fi

# --- 3. Restore the pre-OffgridCloud hostname --------------------------------
HOST_STATE="$PREFIX/data/https-hostname.state"
if [[ -f "$HOST_STATE" ]]; then
  PREV_HOSTNAME=""
  # shellcheck disable=SC1090
  . "$HOST_STATE" 2>/dev/null || true
  if [[ -n "$PREV_HOSTNAME" ]]; then
    hostnamectl set-hostname "$PREV_HOSTNAME" 2>/dev/null \
      && echo "   Restored hostname: $PREV_HOSTNAME"
    # Keep /etc/hosts in sync (mirrors what apply.sh changed).
    if grep -qE '^\s*127\.0\.1\.1' /etc/hosts; then
      sed -i -E "s/^\s*127\.0\.1\.1.*/127.0.1.1\t${PREV_HOSTNAME}/" /etc/hosts
    fi
    systemctl restart avahi-daemon 2>/dev/null || true
  fi
  rm -f "$HOST_STATE"
elif [[ -f "$PREFIX/data/https_state.json" ]]; then
  # HTTPS was set up before apply.sh started recording the original name.
  echo "   Note: original hostname unknown — keeping '$(hostname 2>/dev/null || true)'."
  echo "   Change it with: sudo hostnamectl set-hostname <name>"
fi

# --- 4. State file the backend reads -----------------------------------------
rm -f "$PREFIX/data/https_state.json"

# Let a running app pick up the removed wiring (no-op when the service is gone).
systemctl try-restart offgridcloud 2>/dev/null || true
