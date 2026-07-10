#!/usr/bin/env bash
# OffgridCloud — zero-config local quickstart.
#
# Tries the whole app on your own machine in ONE command: builds the frontend,
# creates a Python venv, writes a local .env (random secret + admin password on
# first run), and starts the server in the foreground on http://localhost:8000.
#
# No root, no systemd, nothing installed system-wide. Ctrl-C to stop.
# For a real Raspberry Pi / server deployment use deploy/install.sh instead.
#
# Usage:  ./quickstart.sh [--port PORT] [--no-build]
set -euo pipefail

PORT="8000"
DO_BUILD=1
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="${2:?--port needs a value}"; shift 2 ;;
    --no-build) DO_BUILD=0; shift ;;
    -h|--help) sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1;36m>> %s\033[0m\n' "$1"; }

command -v python3 >/dev/null 2>&1 || { echo "python3 is required." >&2; exit 1; }

# --- Frontend ---------------------------------------------------------------
if [[ $DO_BUILD -eq 1 ]]; then
  if command -v npm >/dev/null 2>&1; then
    step "Building frontend..."
    ( cd "$ROOT/frontend" && npm install && npm run build )
    rm -rf "$ROOT/backend/app/static"
    cp -r "$ROOT/frontend/dist" "$ROOT/backend/app/static"
  else
    echo ">> npm not found — skipping the UI build (the API still runs; visit /api/health)."
  fi
fi

# --- Python venv ------------------------------------------------------------
step "Setting up Python environment..."
VENV="$ROOT/backend/.venv"
[[ -d "$VENV" ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$ROOT/backend/requirements.txt"

# --- Local .env -------------------------------------------------------------
ENV_FILE="$ROOT/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  step "Writing local .env (random secret + admin password)..."
  SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  PW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
  cat > "$ENV_FILE" <<ENV
OGC_SECRET_KEY=$SECRET
OGC_INITIAL_ADMIN_EMAIL=admin@offgrid.local
OGC_INITIAL_ADMIN_PASSWORD=$PW
OGC_ENVIRONMENT=development
OGC_DATA_DIR=$ROOT/data
OGC_BUFFER_DIR=$ROOT/data/buffer
OGC_RCLONE_BINARY=rclone
ENV
  printf '\n\033[1;33m  Admin login (saved to .env):  admin@offgrid.local  /  %s\033[0m\n' "$PW"
fi

# --- Run --------------------------------------------------------------------
step "Starting OffgridCloud on http://localhost:$PORT  (Ctrl-C to stop)"
cd "$ROOT/backend"
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
exec "$VENV/bin/uvicorn" app.main:app --host 0.0.0.0 --port "$PORT"
