#!/usr/bin/env bash
# (Re)set the OffgridCloud console admin PIN — the one that unlocks the
# "drop to the Raspberry Pi OS shell" action in the on-box menu.
#
# Prompts twice (hidden), stores only a salted PBKDF2 hash. Root-only.
#
# Usage: sudo ./deploy/kiosk/set-pin.sh [--prefix DIR]
set -euo pipefail

PREFIX="/opt/offgridcloud"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

# The console program owns the hashing + storage logic; reuse it so the format
# stays in one place. OGC_PREFIX tells it where data/kiosk.pin lives.
OGC_PREFIX="$PREFIX" exec python3 "$HERE/offgrid-console.py" --set-pin
