#!/usr/bin/env bash
# Set up link bundling ("Leitungsbündelung") with net-merge.
#
# net-merge (https://github.com/rohit267/net-merge, MIT) spreads connections over
# every uplink at once — cable, Wi-Fi, a USB-tethered phone. OffgridCloud runs it
# as a SOCKS5 proxy on 127.0.0.1:1080 and sends only its rclone uploads through
# it; nothing else about the box's routing changes. Opt-in, root-only. It:
#
#   1. installs the pinned net-merge version NM_VERSION from the release
#      tarball (SHA-256 checked) to /usr/local/bin. net-merge's apt repository
#      (rohit267.github.io/net-merge) is gone; its leftovers are removed so
#      they don't break apt-get update,
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
# Pinned release. Bumped by scripts/netmerge-bump.sh (weekly via the
# "net-merge update" workflow, which opens a PR): version + tarball SHA-256s.
NM_VERSION="0.3.3"
declare -A NM_SHA256=(
  [x86_64]="f9092de729478c0e51f9972397ffbc80d3470017fb24315ed4fa045a84b8cff5"
  [aarch64]="3a839cc538b23a6e4575c158d05d9fd1fb90724d3f69e86da6d5dd2c884f41ca"
  [armv7l]="666a70ca54cabe14c5f1de2fb429d142edf88c13113cf46131dc2f65680dcd00"
)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="${2:?}"; shift 2 ;;
    --service-user) SERVICE_USER="${2:?}"; shift 2 ;;
    --no-start) DO_START=0; shift ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

step() { printf '\n\033[1;36m>> %s\033[0m\n' "$1"; }

# --- 1. net-merge -------------------------------------------------------------
step "Installing net-merge $NM_VERSION..."
install_tarball() {
  local arch tmp; arch="$(uname -m)"
  if [[ -z "${NM_SHA256[$arch]:-}" ]]; then
    echo "   No pinned net-merge build for $arch." >&2; return 1
  fi
  tmp="$(mktemp -d)"
  if curl -fsSL "https://github.com/rohit267/net-merge/releases/download/v$NM_VERSION/net-merge-linux-$arch.tar.gz" \
       -o "$tmp/nm.tgz" \
     && { echo "${NM_SHA256[$arch]}  $tmp/nm.tgz" | sha256sum -c --status \
          || { echo "   Checksum mismatch for net-merge-linux-$arch.tar.gz — not installing." >&2; false; }; } \
     && tar -xzf "$tmp/nm.tgz" -C "$tmp" net-merge; then
    install -m 755 "$tmp/net-merge" /usr/local/bin/net-merge
    rm -rf "$tmp"
  else
    rm -rf "$tmp"; return 1
  fi
}
# net-merge's apt repository no longer exists: older installs added it, and
# apt-get update now fails on it (exit 100). Drop it and the package from it.
rm -f /etc/apt/sources.list.d/net-merge.list /usr/share/keyrings/net-merge.gpg
install_tarball || true
if [[ -x /usr/local/bin/net-merge ]] && dpkg -s net-merge >/dev/null 2>&1; then
  apt-mark unhold net-merge >/dev/null 2>&1 || true
  apt-get remove -y net-merge >/dev/null 2>&1 \
    || echo "   Old net-merge package still installed — /usr/local/bin/net-merge is used." >&2
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
