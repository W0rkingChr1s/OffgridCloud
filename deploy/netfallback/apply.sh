#!/usr/bin/env bash
# Apply OffgridCloud's exported network config to NetworkManager (needs root).
#
# Thin wrapper around _apply.py so the app can invoke a single, sudoers-friendly
# command (see deploy/netfallback/install.sh, which wires OGC_NET_APPLY_COMMAND).
# After rendering the connections it nudges the watchdog to converge right away.
#
# Usage: sudo apply.sh [/path/to/network.json]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${1:-/opt/offgridcloud/data/network.json}"

if [[ $EUID -ne 0 ]]; then
  echo "apply.sh must run as root (configuring NetworkManager)." >&2
  exit 1
fi
if ! command -v nmcli >/dev/null 2>&1; then
  echo "nmcli not found. This host does not use NetworkManager — see docs/NETZWERK-REDUNDANZ.md." >&2
  exit 2
fi

python3 "$HERE/_apply.py" "$CONFIG"

# Converge immediately (bring AP up/down to match current connectivity).
if systemctl list-unit-files offgridcloud-netwatch.service >/dev/null 2>&1; then
  systemctl try-restart offgridcloud-netwatch.service || true
else
  "$HERE/watchdog.sh" --oneshot "$CONFIG" || true
fi
