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
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() { sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --start) DO_START=1; shift ;;
    --admin-email) ADMIN_EMAIL="${2:?--admin-email needs a value}"; shift 2 ;;
    --port) PORT="${2:?--port needs a value}"; shift 2 ;;
    --prefix) PREFIX="${2:?--prefix needs a value}"; shift 2 ;;
    --with-ffmpeg) WITH_FFMPEG=1; shift ;;
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
