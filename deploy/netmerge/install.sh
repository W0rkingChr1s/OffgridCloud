#!/usr/bin/env bash
# Set up link bundling ("Leitungsbündelung") with net-merge.
#
# net-merge (https://github.com/rohit267/net-merge, MIT) spreads connections over
# every uplink at once — cable, Wi-Fi, a USB-tethered phone. OffgridCloud runs it
# as a SOCKS5 proxy on 127.0.0.1:1080 and sends only its rclone uploads through
# it; nothing else about the box's routing changes. Opt-in, root-only. It:
#
#   1. installs net-merge (Debian/Raspberry Pi OS: from its signed apt
#      repository, key fingerprint checked; elsewhere: the release tarball),
#   2. installs + starts the offgridcloud-netmerge service, and
#   3. wires OGC_NETMERGE_PROXY into .env so the toggle under
#      Bandbreite → Leitungsbündelung becomes available.
#
# Bundling stays OFF until an admin switches it on in the web UI.
# deploy/install.sh runs this by default; --no-start (image build) only
# enables the service instead of starting it.
#
# Usage: sudo ./deploy/netmerge/install.sh [--prefix DIR] [--service-user USER] [--no-start]
set -euo pipefail

PREFIX="/opt/offgridcloud"
SERVICE_USER="offgrid"
DO_START=1
LISTEN="127.0.0.1:1080"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Signing key of net-merge's package repositories (see its README).
KEY_FPR="0168C7AB77721656904811AE904F61E7F4F1C2A1"
REPO="https://rohit267.github.io/net-merge"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="${2:?}"; shift 2 ;;
    --service-user) SERVICE_USER="${2:?}"; shift 2 ;;
    --no-start) DO_START=0; shift ;;
    -h|--help) sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

step() { printf '\n\033[1;36m>> %s\033[0m\n' "$1"; }

# --- 1. net-merge -------------------------------------------------------------
step "Installing net-merge..."
install_apt() {
  command -v gpg >/dev/null 2>&1 || apt-get install -y --no-install-recommends gnupg >/dev/null
  local tmp; tmp="$(mktemp -d)"
  curl -fsSL "$REPO/apt/key.gpg" -o "$tmp/key.gpg" || { rm -rf "$tmp"; return 1; }
  # Only trust the key if it is the one the project publishes.
  if ! gpg --show-keys --with-colons "$tmp/key.gpg" 2>/dev/null | grep -q "^fpr:*$KEY_FPR:"; then
    echo "   Signing key fingerprint does not match $KEY_FPR — not adding the repository." >&2
    rm -rf "$tmp"; return 1
  fi
  install -m 644 "$tmp/key.gpg" /usr/share/keyrings/net-merge.gpg
  rm -rf "$tmp"
  echo "deb [signed-by=/usr/share/keyrings/net-merge.gpg] $REPO/apt stable main" \
    > /etc/apt/sources.list.d/net-merge.list
  apt-get update -o Dir::Etc::sourcelist=sources.list.d/net-merge.list \
    -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0 >/dev/null
  apt-get install -y --no-install-recommends net-merge
}
install_tarball() {
  local tmp; tmp="$(mktemp -d)"
  if curl -fsSL "https://github.com/rohit267/net-merge/releases/latest/download/net-merge-linux-$(uname -m).tar.gz" \
       -o "$tmp/nm.tgz" && tar -xzf "$tmp/nm.tgz" -C "$tmp" net-merge; then
    install -m 755 "$tmp/net-merge" /usr/local/bin/net-merge
    rm -rf "$tmp"
  else
    rm -rf "$tmp"; return 1
  fi
}
if command -v apt-get >/dev/null 2>&1; then
  install_apt || { echo "   apt repository failed — falling back to the release tarball." >&2; install_tarball; }
else
  install_tarball
fi
NM_BIN="$(command -v net-merge || true)"
if [[ -z "$NM_BIN" ]]; then
  echo "   net-merge could not be installed — see docs/NETZWERK-REDUNDANZ.md." >&2
  exit 1
fi
echo "   $("$NM_BIN" version 2>/dev/null | head -1) at $NM_BIN"

# --- 2. Service -----------------------------------------------------------------
step "Installing the offgridcloud-netmerge service..."
sed -e "s#/usr/bin/net-merge#$NM_BIN#" \
    -e "s#^User=.*#User=$SERVICE_USER#" -e "s#^Group=.*#Group=$SERVICE_USER#" \
    -e "s#127.0.0.1:1080#$LISTEN#g" \
    "$HERE/offgridcloud-netmerge.service" > /etc/systemd/system/offgridcloud-netmerge.service
if [[ $DO_START -eq 1 ]]; then
  systemctl daemon-reload
  systemctl enable offgridcloud-netmerge.service >/dev/null 2>&1 || true
  systemctl restart offgridcloud-netmerge.service || \
    echo "   Service did not start — check: journalctl -u offgridcloud-netmerge -e" >&2
else
  systemctl enable offgridcloud-netmerge.service >/dev/null 2>&1 || true
fi

# --- 3. Wire the app -------------------------------------------------------------
step "Wiring the app to net-merge..."
ENV_FILE="$PREFIX/.env"
set_env() {  # set_env KEY VALUE
  if grep -q "^$1=" "$ENV_FILE"; then
    sed -i "s#^$1=.*#$1=$2#" "$ENV_FILE"
  else
    echo "$1=$2" >> "$ENV_FILE"
  fi
}
if [[ -f "$ENV_FILE" ]]; then
  set_env OGC_NETMERGE_PROXY "$LISTEN"
  set_env OGC_NETMERGE_BINARY "$NM_BIN"
  chown "$SERVICE_USER:$SERVICE_USER" "$ENV_FILE" 2>/dev/null || true
  echo "   Set OGC_NETMERGE_PROXY in $ENV_FILE"
  [[ $DO_START -eq 1 ]] && { systemctl try-restart offgridcloud 2>/dev/null || true; }
else
  echo "   $ENV_FILE not found — add manually:"
  echo "     OGC_NETMERGE_PROXY=$LISTEN"
  echo "     OGC_NETMERGE_BINARY=$NM_BIN"
fi

cat <<DONE

$(printf '\033[1;32mDone.\033[0m') net-merge is running as a SOCKS5 proxy on $LISTEN.

  Next: web UI → Bandbreite → Leitungsbündelung → "Uploads über alle Leitungen
  bündeln", then Speichern. Which links it sees:
    $NM_BIN list
    journalctl -u offgridcloud-netmerge -f
DONE
