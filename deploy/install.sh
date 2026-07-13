#!/usr/bin/env bash
# Native install for OffgridCloud — recommended for Raspberry Pi 3 (no Docker).
#
# One command sets everything up: builds the frontend, creates a Python venv,
# installs rclone (and optionally ffmpeg for video thumbnails), copies the app
# to /opt/offgridcloud, writes a .env with a random secret AND a random admin
# password, and registers a systemd service.
#
# Usage:
#   sudo ./deploy/install.sh [options]
#
# Options:
#   --start                Enable and start the service right away, then verify
#                          it answers on /api/health.
#   --admin-email EMAIL    Initial admin login (default: admin@offgrid.local).
#   --port PORT            Port to serve on (default: 8000).
#   --prefix DIR           Install location (default: /opt/offgridcloud).
#   --with-ffmpeg          Also install ffmpeg (enables video thumbnails).
#   --self-update          Enable one-click updates from the web UI (sudoers).
#   --no-service           Skip installing the systemd unit.
#   -h, --help             Show this help and exit.
set -euo pipefail

PREFIX="/opt/offgridcloud"
SERVICE_USER="offgrid"
ADMIN_EMAIL="admin@offgrid.local"
PORT="8000"
DO_START=0
INSTALL_SERVICE=1
WITH_FFMPEG=0
SELF_UPDATE=0
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() { sed -n '2,21p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --start) DO_START=1; shift ;;
    --admin-email) ADMIN_EMAIL="${2:?--admin-email needs a value}"; shift 2 ;;
    --port) PORT="${2:?--port needs a value}"; shift 2 ;;
    --prefix) PREFIX="${2:?--prefix needs a value}"; shift 2 ;;
    --with-ffmpeg) WITH_FFMPEG=1; shift ;;
    --self-update) SELF_UPDATE=1; shift ;;
    --no-service) INSTALL_SERVICE=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

step() { printf '\n\033[1;36m>> %s\033[0m\n' "$1"; }

# --- System dependencies ----------------------------------------------------
step "Installing system dependencies..."
if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  apt-get install -y --no-install-recommends python3-venv curl ca-certificates
  [[ $WITH_FFMPEG -eq 1 ]] && apt-get install -y --no-install-recommends ffmpeg
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y python3-virtualenv curl || dnf install -y python3 curl
  [[ $WITH_FFMPEG -eq 1 ]] && dnf install -y ffmpeg
elif command -v pacman >/dev/null 2>&1; then
  pacman -Sy --noconfirm python curl
  [[ $WITH_FFMPEG -eq 1 ]] && pacman -Sy --noconfirm ffmpeg
else
  echo "No supported package manager found. Ensure python3-venv (and curl) are installed." >&2
fi

# rclone — the distro package is often years out of date, so prefer the
# official installer and fall back to the package manager if offline.
if command -v rclone >/dev/null 2>&1; then
  echo "   rclone already present: $(rclone version 2>/dev/null | head -1)"
else
  step "Installing rclone (official installer)..."
  if ! curl -fsSL https://rclone.org/install.sh | bash; then
    echo "   Official installer failed; trying the distro package..."
    if command -v apt-get >/dev/null 2>&1; then apt-get install -y rclone
    elif command -v dnf >/dev/null 2>&1; then dnf install -y rclone
    elif command -v pacman >/dev/null 2>&1; then pacman -Sy --noconfirm rclone
    else echo "   Could not install rclone — install it manually before first use." >&2
    fi
  fi
fi

# --- Frontend build ---------------------------------------------------------
step "Building frontend (requires Node.js)..."
if ! command -v npm >/dev/null 2>&1; then
  echo "npm not found. Install Node.js, or build the frontend on another machine" >&2
  echo "and copy frontend/dist to backend/app/static before re-running." >&2
  exit 1
fi
( cd "$REPO_ROOT/frontend" && npm install && npm run build )

# --- Service user -----------------------------------------------------------
step "Creating service user '$SERVICE_USER'..."
id -u "$SERVICE_USER" >/dev/null 2>&1 || \
  useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"

# --- Copy application -------------------------------------------------------
step "Copying files to $PREFIX..."
mkdir -p "$PREFIX"
cp -r "$REPO_ROOT/backend" "$PREFIX/"
rm -rf "$PREFIX/backend/.venv" "$PREFIX/backend/app/static" \
       "$PREFIX/backend/tests" "$PREFIX/backend/.pytest_cache"
cp -r "$REPO_ROOT/frontend/dist" "$PREFIX/backend/app/static"

# Stamp the deployed version so the UI reports the real release. An explicit
# OGC_STAMP_VERSION (set by update.sh to the exact target tag) wins — this is
# unambiguous even when several tags point at the same commit. Otherwise derive
# it from the git tag of the source checkout, then a VERSION file shipped in a
# release tarball. Without any, the app uses its built-in fallback constant.
VERSION_STR="${OGC_STAMP_VERSION:-}"
if [[ -z "$VERSION_STR" ]] && git config --global --add safe.directory "$REPO_ROOT" 2>/dev/null &&
   git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  # No --always: if no tag is reachable, prefer the app's fallback constant over
  # a bare commit hash (which reads oddly as a "version" in the UI).
  VERSION_STR="$(git -C "$REPO_ROOT" describe --tags 2>/dev/null | sed -E 's/^v\.?//')"
elif [[ -z "$VERSION_STR" && -f "$REPO_ROOT/VERSION" ]]; then
  VERSION_STR="$(tr -d '[:space:]' < "$REPO_ROOT/VERSION")"
fi
if [[ -n "$VERSION_STR" ]]; then
  printf '%s\n' "$VERSION_STR" > "$PREFIX/backend/app/VERSION"
  echo "   Deployed version: $VERSION_STR"
fi

# --- Python virtualenv ------------------------------------------------------
step "Setting up Python virtualenv..."
python3 -m venv "$PREFIX/backend/.venv"
"$PREFIX/backend/.venv/bin/pip" install --upgrade pip
"$PREFIX/backend/.venv/bin/pip" install -r "$PREFIX/backend/requirements.txt"

# --- Configuration ----------------------------------------------------------
GENERATED_PASSWORD=""
if [[ ! -f "$PREFIX/.env" ]]; then
  step "Creating $PREFIX/.env with generated secret + admin password..."
  SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  GENERATED_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
  cat > "$PREFIX/.env" <<ENV
OGC_SECRET_KEY=$SECRET
OGC_INITIAL_ADMIN_EMAIL=$ADMIN_EMAIL
OGC_INITIAL_ADMIN_PASSWORD=$GENERATED_PASSWORD
OGC_ENVIRONMENT=production
OGC_DATA_DIR=$PREFIX/data
OGC_BUFFER_DIR=$PREFIX/data/buffer
OGC_RCLONE_BINARY=rclone
ENV
  chmod 600 "$PREFIX/.env"
else
  echo "   Keeping existing $PREFIX/.env"
fi

mkdir -p "$PREFIX/data/buffer"
chown -R "$SERVICE_USER:$SERVICE_USER" "$PREFIX"

# --- systemd service --------------------------------------------------------
if [[ $INSTALL_SERVICE -eq 1 ]]; then
  step "Installing systemd service (port $PORT)..."
  sed "s/--port 8000/--port $PORT/" "$REPO_ROOT/deploy/offgridcloud.service" \
    > /etc/systemd/system/offgridcloud.service
  systemctl daemon-reload
fi

# --- Optional: one-click self-update (sudoers + .env flags) -----------------
if [[ $SELF_UPDATE -eq 1 ]]; then
  step "Enabling one-click updates from the web UI..."
  UPDATE_SCRIPT="$PREFIX/src/deploy/update.sh"
  if [[ ! -f "$UPDATE_SCRIPT" ]]; then
    echo "   Note: $UPDATE_SCRIPT not found. One-click update needs the one-line"
    echo "   installer layout ($PREFIX/src). Updates via 'sudo update.sh' still work."
  fi
  SUDOERS=/etc/sudoers.d/offgridcloud
  echo "$SERVICE_USER ALL=(root) NOPASSWD: $UPDATE_SCRIPT" > "$SUDOERS.tmp"
  if visudo -cf "$SUDOERS.tmp" >/dev/null 2>&1; then
    install -m 440 "$SUDOERS.tmp" "$SUDOERS"; rm -f "$SUDOERS.tmp"
    # Wire the app to that command (append once, without touching secrets).
    grep -q '^OGC_SELF_UPDATE=' "$PREFIX/.env" || echo "OGC_SELF_UPDATE=true" >> "$PREFIX/.env"
    grep -q '^OGC_UPDATE_COMMAND=' "$PREFIX/.env" || \
      echo "OGC_UPDATE_COMMAND=sudo $UPDATE_SCRIPT" >> "$PREFIX/.env"
    chown "$SERVICE_USER:$SERVICE_USER" "$PREFIX/.env"
    echo "   One-click update enabled (button appears under System when a release is newer)."
  else
    rm -f "$SUDOERS.tmp"
    echo "   Could not validate sudoers rule — skipped. Use 'sudo update.sh' instead." >&2
  fi
fi

# --- Optional start + health check -----------------------------------------
if [[ $DO_START -eq 1 && $INSTALL_SERVICE -eq 1 ]]; then
  step "Starting the service..."
  systemctl enable --now offgridcloud
  printf "   Waiting for http://127.0.0.1:%s/api/health " "$PORT"
  HEALTHY=0
  for _ in $(seq 1 20); do
    if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
      HEALTHY=1; break
    fi
    printf "."; sleep 1
  done
  if [[ $HEALTHY -eq 1 ]]; then
    printf "\n   \033[1;32mService is up and healthy.\033[0m\n"
  else
    printf "\n   \033[1;31mService did not answer in time — check: journalctl -u offgridcloud -e\033[0m\n"
  fi
fi

# --- Summary ----------------------------------------------------------------
cat <<EOF

$(printf '\033[1;32mDone.\033[0m') OffgridCloud is installed in $PREFIX.

EOF

if [[ -n "$GENERATED_PASSWORD" ]]; then
  cat <<EOF
$(printf '\033[1;33m  Initial admin login (shown only once — save it now):\033[0m')
    URL:      http://<host>:$PORT
    Email:    $ADMIN_EMAIL
    Password: $GENERATED_PASSWORD

  Change the password after your first login.
EOF
else
  echo "  Login uses the credentials already in $PREFIX/.env"
fi

if [[ $DO_START -ne 1 ]]; then
  cat <<EOF

  Next steps:
    1. (Pi) Point OGC_BUFFER_DIR at your external USB SSD: sudo nano $PREFIX/.env
    2. Start the service:   sudo systemctl enable --now offgridcloud
    3. Open:                http://<host>:$PORT
EOF
fi
