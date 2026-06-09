#!/usr/bin/env bash
# Back up the OffgridCloud database and configuration (NOT the media buffer).
#
# Usage:  ./deploy/backup.sh [DATA_DIR] [ENV_FILE] [OUT_DIR]
# Defaults assume the native install layout under /opt/offgridcloud.
set -euo pipefail

DATA_DIR="${1:-/opt/offgridcloud/data}"
ENV_FILE="${2:-/opt/offgridcloud/.env}"
OUT_DIR="${3:-.}"

STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="$OUT_DIR/offgridcloud-backup-$STAMP.tar.gz"

if [[ ! -f "$DATA_DIR/offgridcloud.db" ]]; then
  echo "Database not found at $DATA_DIR/offgridcloud.db" >&2
  exit 1
fi

# The .env holds OGC_SECRET_KEY, which is required to decrypt provider
# credentials in the DB — keep this archive somewhere safe.
TMP="$(mktemp -d)"
cp "$DATA_DIR/offgridcloud.db" "$TMP/"
[[ -f "$ENV_FILE" ]] && cp "$ENV_FILE" "$TMP/.env"

tar -czf "$ARCHIVE" -C "$TMP" .
rm -rf "$TMP"

echo "Backup written to $ARCHIVE"
echo "Restore: stop the service, extract offgridcloud.db back into $DATA_DIR (and .env), then start."
