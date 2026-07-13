#!/usr/bin/env bash
# Enable the OffgridCloud VPN client on a NATIVE (systemd) install.
#
# The app runs unprivileged, so bringing a WireGuard/OpenVPN tunnel up — which
# creates a TUN interface and edits the routing table — needs the NET_ADMIN
# capability. In Docker you pass --cap-add=NET_ADMIN --device=/dev/net/tun; on a
# bare-metal Raspberry Pi this script does the equivalent:
#
#   1. installs wireguard-tools + openvpn,
#   2. loads the tun kernel module (now and on every boot), and
#   3. grants the systemd service CAP_NET_ADMIN via a drop-in, then restarts it.
#
# Idempotent. Re-run after changing --prefix/--service-name if needed.
#
# Usage: sudo ./deploy/vpn/install.sh [--service-name NAME] [--prefix DIR]
set -euo pipefail

SERVICE_NAME="offgridcloud"
PREFIX="/opt/offgridcloud"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service-name) SERVICE_NAME="${2:?}"; shift 2 ;;
    --prefix) PREFIX="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

step() { printf '\n\033[1;36m>> %s\033[0m\n' "$1"; }

# --- 1. VPN tooling ---------------------------------------------------------
step "Installing wireguard-tools + openvpn..."
if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  apt-get install -y --no-install-recommends wireguard-tools openvpn
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y wireguard-tools openvpn
elif command -v pacman >/dev/null 2>&1; then
  pacman -Sy --noconfirm wireguard-tools openvpn
else
  echo "   No supported package manager — install wireguard-tools/openvpn manually." >&2
fi

# --- 2. TUN kernel module ---------------------------------------------------
step "Ensuring the tun module is loaded (now + on boot)..."
modprobe tun 2>/dev/null || echo "   modprobe tun failed (built-in kernel? /dev/net/tun may still exist)." >&2
mkdir -p /etc/modules-load.d
echo "tun" > /etc/modules-load.d/offgridcloud-tun.conf
[[ -e /dev/net/tun ]] && echo "   /dev/net/tun present." || \
  echo "   /dev/net/tun still missing — a reboot may be required." >&2

# --- 3. Grant the service CAP_NET_ADMIN -------------------------------------
step "Granting '$SERVICE_NAME' the NET_ADMIN capability (systemd drop-in)..."
if ! systemctl list-unit-files "${SERVICE_NAME}.service" >/dev/null 2>&1; then
  echo "   Service '${SERVICE_NAME}.service' not found. Install OffgridCloud first," >&2
  echo "   or pass --service-name. Skipping the capability drop-in." >&2
  exit 1
fi
DROPIN_DIR="/etc/systemd/system/${SERVICE_NAME}.service.d"
mkdir -p "$DROPIN_DIR"
cat > "$DROPIN_DIR/10-vpn-caps.conf" <<'UNIT'
# Added by deploy/vpn/install.sh — lets the unprivileged service create the VPN
# TUN interface and edit routes. NET_ADMIN is the only extra capability needed.
[Service]
AmbientCapabilities=CAP_NET_ADMIN
UNIT
systemctl daemon-reload
systemctl try-restart "${SERVICE_NAME}.service" 2>/dev/null || \
  echo "   Could not restart ${SERVICE_NAME} — start it to pick up the change." >&2

cat <<EOF

$(printf '\033[1;32mDone.\033[0m') VPN prerequisites are in place for the native install.

  Open the web UI → VPN. The "erhöhte Rechte" warning should be gone; add a
  WireGuard or OpenVPN profile and connect.

  Note: keep DNS out of the WireGuard config (no "DNS =" line) — rewriting the
  system resolver needs full root and is skipped when only CAP_NET_ADMIN is
  granted. Route by IP, or resolve names another way. See docs/VPN.md.
EOF
