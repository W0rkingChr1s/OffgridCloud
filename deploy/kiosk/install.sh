#!/usr/bin/env bash
# Set up the OffgridCloud local console — the on-box "OffgridCloud OS" menu.
#
# Opt-in, root-only. It:
#   1. installs the console program + systemd unit that owns tty1,
#   2. masks getty@tty1 so a monitor on the box shows only the OffgridCloud menu,
#   3. sets an admin PIN (given, or a random 6-digit one printed once) that
#      gates the "drop to the Raspberry Pi OS shell" action, and
#   4. optionally (--with-chromium) installs a minimal X + Chromium stack so the
#      menu can open the full web UI full-screen (meant for a Pi 4/5).
#
# The underlying OS stays reachable on tty2-tty6 (Strg+Alt+F2) with the normal
# login — a deliberate safety net against lock-out.
#
# Usage: sudo ./deploy/kiosk/install.sh [options]
#   --prefix DIR      Install location (default: /opt/offgridcloud).
#   --pin PIN         Set this admin PIN (default: generate + print once).
#   --with-chromium   Also install X + Chromium for the optional browser kiosk.
#   -h, --help        Show this help and exit.
set -euo pipefail

PREFIX="/opt/offgridcloud"
KIOSK_PIN=""
WITH_CHROMIUM=0
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="${2:?--prefix needs a value}"; shift 2 ;;
    --pin) KIOSK_PIN="${2:?--pin needs a value}"; shift 2 ;;
    --with-chromium) WITH_CHROMIUM=1; shift ;;
    -h|--help) sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

step() { printf '\n\033[1;36m>> %s\033[0m\n' "$1"; }

CONSOLE="$HERE/offgrid-console.py"
if [[ ! -f "$CONSOLE" ]]; then
  echo "offgrid-console.py not found next to this installer ($HERE)." >&2
  exit 1
fi
chmod +x "$CONSOLE" "$HERE/chromium-kiosk.sh" "$HERE/set-pin.sh" 2>/dev/null || true

command -v python3 >/dev/null 2>&1 || { echo "python3 is required." >&2; exit 1; }

# --- 1. Admin PIN -----------------------------------------------------------
step "Configuring the admin PIN (gates the drop-to-shell action)..."
PIN_FILE="$PREFIX/data/kiosk.pin"
mkdir -p "$PREFIX/data"
GENERATED_PIN=""
if [[ -n "$KIOSK_PIN" || ! -f "$PIN_FILE" ]]; then
  if [[ -z "$KIOSK_PIN" ]]; then
    KIOSK_PIN="$(python3 -c 'import secrets;print(secrets.randbelow(900000)+100000)')"
    GENERATED_PIN="$KIOSK_PIN"
  fi
  python3 "$CONSOLE" --hash-pin "$KIOSK_PIN" > "$PIN_FILE"
  chmod 600 "$PIN_FILE"
  echo "   PIN stored (hashed) in $PIN_FILE"
else
  echo "   Keeping the existing PIN ($PIN_FILE). Change it with set-pin.sh."
fi

# --- 2. Optional: X + Chromium for the browser kiosk -----------------------
if [[ $WITH_CHROMIUM -eq 1 ]]; then
  step "Installing the minimal X + Chromium stack (browser kiosk)..."
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y --no-install-recommends \
      xserver-xorg xinit x11-xserver-utils chromium-browser \
      || apt-get install -y --no-install-recommends \
           xserver-xorg xinit x11-xserver-utils chromium \
      || echo "   Could not install the browser stack — the text console still works." >&2
    # Let a non-root X start on the console VT (xinit from the kiosk service).
    if [[ -f /etc/X11/Xwrapper.config ]]; then
      grep -q '^allowed_users' /etc/X11/Xwrapper.config \
        && sed -i 's/^allowed_users=.*/allowed_users=anybody/' /etc/X11/Xwrapper.config \
        || echo 'allowed_users=anybody' >> /etc/X11/Xwrapper.config
    else
      printf 'allowed_users=anybody\nneeds_root_rights=yes\n' > /etc/X11/Xwrapper.config
    fi
  else
    echo "   No apt-get here — install xserver-xorg/xinit/chromium manually." >&2
  fi
fi

# --- 3. systemd unit on tty1 ------------------------------------------------
step "Installing the kiosk service on tty1..."
sed -e "s#/opt/offgridcloud/deploy/kiosk/offgrid-console.py#$CONSOLE#g" \
    -e "s#Environment=OGC_PREFIX=/opt/offgridcloud#Environment=OGC_PREFIX=$PREFIX#g" \
    "$HERE/offgrid-kiosk.service" > /etc/systemd/system/offgrid-kiosk.service

# --- 4. Hand tty1 to the console (mask the login getty there) ---------------
step "Masking getty@tty1 so the console owns the primary screen..."
systemctl disable --now getty@tty1.service 2>/dev/null || true
systemctl mask getty@tty1.service 2>/dev/null || true

systemctl daemon-reload
if systemctl enable --now offgrid-kiosk.service 2>/dev/null; then
  echo "   Kiosk service is up on tty1."
else
  echo "   Could not start offgrid-kiosk.service — check: journalctl -u offgrid-kiosk -e" >&2
fi

# --- Summary ----------------------------------------------------------------
cat <<EOF

$(printf '\033[1;32mDone.\033[0m') OffgridCloud OS console is installed on tty1.

  A monitor attached to the box now shows only the OffgridCloud menu. The
  Raspberry Pi OS shell stays reachable two ways:
    · from the menu → "Zur Raspberry-Pi-Shell (PIN)"  (needs the admin PIN)
    · Strg+Alt+F2 to a normal login on tty2            (safety net)
EOF

if [[ -n "$GENERATED_PIN" ]]; then
  cat <<EOF

$(printf '\033[1;33m  Admin PIN (shown only once — save it now): %s\033[0m')
  Change it anytime with: sudo $HERE/set-pin.sh
EOF
fi
