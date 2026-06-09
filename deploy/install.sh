#!/usr/bin/env bash
# Native install for OffgridCloud — recommended for Raspberry Pi 3 (no Docker).
#
# Builds the frontend, sets up a Python venv, installs rclone, copies everything
# to /opt/offgridcloud and registers a systemd service.
#
# Usage:  sudo ./deploy/install.sh
set -euo pipefail

PREFIX="/opt/offgridcloud"
SERVICE_USER="offgrid"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

echo ">> Installing system dependencies (python3-venv, rclone)..."
if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  apt-get install -y --no-install-recommends python3-venv rclone
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y python3-virtualenv rclone || dnf install -y python3 rclone
elif command -v pacman >/dev/null 2>&1; then
  pacman -Sy --noconfirm python rclone
else
  echo "No supported package manager found. Ensure python3-venv and rclone are installed." >&2
fi

echo ">> Building frontend (requires Node.js)..."
if ! command -v npm >/dev/null 2>&1; then
  echo "npm not found. Install Node.js, or build the frontend on another machine." >&2
  exit 1
fi
( cd "$REPO_ROOT/frontend" && npm install && npm run build )

echo ">> Creating service user '$SERVICE_USER'..."
id -u "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"

echo ">> Copying files to $PREFIX..."
mkdir -p "$PREFIX"
cp -r "$REPO_ROOT/backend" "$PREFIX/"
rm -rf "$PREFIX/backend/.venv" "$PREFIX/backend/app/static"
cp -r "$REPO_ROOT/frontend/dist" "$PREFIX/backend/app/static"

echo ">> Setting up Python virtualenv..."
python3 -m venv "$PREFIX/backend/.venv"
"$PREFIX/backend/.venv/bin/pip" install --upgrade pip
"$PREFIX/backend/.venv/bin/pip" install -r "$PREFIX/backend/requirements.txt"

if [[ ! -f "$PREFIX/.env" ]]; then
  echo ">> Creating $PREFIX/.env with a generated secret key..."
  SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  cat > "$PREFIX/.env" <<ENV
OGC_SECRET_KEY=$SECRET
OGC_INITIAL_ADMIN_EMAIL=admin@offgrid.local
OGC_INITIAL_ADMIN_PASSWORD=changeme
OGC_ENVIRONMENT=production
OGC_DATA_DIR=$PREFIX/data
OGC_BUFFER_DIR=$PREFIX/data/buffer
OGC_RCLONE_BINARY=rclone
ENV
fi

mkdir -p "$PREFIX/data/buffer"
chown -R "$SERVICE_USER:$SERVICE_USER" "$PREFIX"

echo ">> Installing systemd service..."
cp "$REPO_ROOT/deploy/offgridcloud.service" /etc/systemd/system/offgridcloud.service
systemctl daemon-reload

cat <<EOF

Done. Next steps:
  1. Edit secrets:        sudo nano $PREFIX/.env
  2. (Pi) Point OGC_BUFFER_DIR at your external USB SSD.
  3. Start the service:   sudo systemctl enable --now offgridcloud
  4. Open:                http://<pi-ip>:8000
EOF
