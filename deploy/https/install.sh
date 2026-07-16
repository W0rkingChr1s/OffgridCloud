#!/usr/bin/env bash
# Set up HTTPS for OffgridCloud (Caddy reverse proxy + mDNS hostname).
#
# Opt-in, root-only. It:
#   1. installs Caddy (official apt repo) and avahi-daemon (mDNS),
#   2. renders the Caddyfile + sets the hostname via apply.sh,
#   3. grants the service user a NOPASSWD sudoers rule for apply.sh only, and
#   4. wires OGC_HTTPS_APPLY_COMMAND into .env so "System → HTTPS" in the UI works.
#
# Usage:
#   sudo ./deploy/https/install.sh --prefix DIR --hostname NAME [--domain DOMAIN]
#                                  [--service-user USER] [--port PORT]
set -euo pipefail

PREFIX="/opt/offgridcloud"
SERVICE_USER="offgrid"
HOSTNAME_SHORT="offgridcloud"
DOMAIN=""
PORT="8000"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="${2:?}"; shift 2 ;;
    --hostname) HOSTNAME_SHORT="${2:?}"; shift 2 ;;
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --service-user) SERVICE_USER="${2:?}"; shift 2 ;;
    --port) PORT="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then echo "Please run as root (sudo)." >&2; exit 1; fi

step() { printf '\n\033[1;36m>> %s\033[0m\n' "$1"; }
APPLY="$HERE/apply.sh"
chmod +x "$APPLY" 2>/dev/null || true

# --- 1. Caddy + avahi -------------------------------------------------------
step "Installing Caddy + avahi (mDNS)..."
if command -v apt-get >/dev/null 2>&1; then
  apt-get install -y --no-install-recommends avahi-daemon avahi-utils || \
    echo "   Could not install avahi — <hostname>.local may not resolve." >&2
  if ! command -v caddy >/dev/null 2>&1; then
    # Official Caddy apt repo (stable). Best-effort: on failure, point the
    # operator at the manual install and bail out of the HTTPS setup only.
    apt-get install -y --no-install-recommends debian-keyring debian-archive-keyring apt-transport-https curl gnupg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
      | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
      | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    apt-get update
    apt-get install -y caddy || {
      echo "   Caddy install failed. Install it manually (https://caddyserver.com/docs/install)" >&2
      echo "   then re-run this script." >&2
      exit 1
    }
  else
    echo "   caddy already present: $(caddy version 2>/dev/null | head -1)"
  fi
else
  echo "   No apt-get here — install caddy + avahi-daemon manually, then re-run." >&2
  exit 1
fi
systemctl enable --now caddy 2>/dev/null || true

# --- 2. Render config + set hostname ---------------------------------------
step "Applying HTTPS config..."
APPLY_ARGS=(--hostname "$HOSTNAME_SHORT" --prefix "$PREFIX" --port "$PORT")
[[ -n "$DOMAIN" ]] && APPLY_ARGS+=(--domain "$DOMAIN")
OGC_PREFIX="$PREFIX" OGC_PORT="$PORT" bash "$APPLY" "${APPLY_ARGS[@]}"

# --- 3. NOPASSWD sudoers rule for apply.sh only ----------------------------
step "Granting the service user permission to re-apply HTTPS config..."
SUDOERS=/etc/sudoers.d/offgridcloud-https
echo "$SERVICE_USER ALL=(root) NOPASSWD: $APPLY" > "$SUDOERS.tmp"
if visudo -cf "$SUDOERS.tmp" >/dev/null 2>&1; then
  install -m 440 "$SUDOERS.tmp" "$SUDOERS"; rm -f "$SUDOERS.tmp"
  echo "   HTTPS config is now changeable from System → HTTPS in the web UI."
else
  rm -f "$SUDOERS.tmp"
  echo "   Could not validate sudoers rule — skipped. Domain changes then need" >&2
  echo "   'sudo $APPLY --hostname … --domain …' on the box." >&2
fi

# --- 4. Wire OGC_HTTPS_APPLY_COMMAND into .env -----------------------------
ENV_FILE="$PREFIX/.env"
if [[ -f "$ENV_FILE" ]]; then
  grep -q '^OGC_HTTPS_APPLY_COMMAND=' "$ENV_FILE" || \
    echo "OGC_HTTPS_APPLY_COMMAND=sudo $APPLY" >> "$ENV_FILE"
  chown "$SERVICE_USER:$SERVICE_USER" "$ENV_FILE" 2>/dev/null || true
fi

step "HTTPS ready. LAN: https://${HOSTNAME_SHORT}.local${DOMAIN:+  Public: https://$DOMAIN}"
