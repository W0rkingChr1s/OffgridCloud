#!/usr/bin/env bash
# OffgridCloud network watchdog — the actual "Rückfallebene".
#
# Periodically checks whether the box has an upstream network (Ethernet or a
# known Wi-Fi). If it loses upstream for `fail_threshold` consecutive checks and
# the fallback is enabled, it brings up the self-hosted access point so the
# field team can still upload. When a known network returns, it drops the AP and
# lets NetworkManager rejoin.
#
# Runs as a loop service (root). Reads the exported config each iteration, so
# changes made in the UI take effect after the next `apply.sh` (which restarts
# this service). Use `--oneshot` to evaluate exactly once (used by apply.sh and
# for testing).
set -uo pipefail

CONFIG_DEFAULT="/opt/offgridcloud/data/network.json"
ONESHOT=0
CONFIG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --oneshot) ONESHOT=1; shift ;;
    *) CONFIG="$1"; shift ;;
  esac
done
CONFIG="${CONFIG:-$CONFIG_DEFAULT}"

STATE_DIR="${OGC_NETWATCH_STATE_DIR:-/run/offgridcloud}"
mkdir -p "$STATE_DIR" 2>/dev/null || true
FAIL_FILE="$STATE_DIR/net-fail-count"

log() { printf '[netwatch] %s\n' "$*"; }

# Read a scalar from the JSON config via python3 (guaranteed on the box).
# Usage: cfg <dotted.key> <default>
cfg() {
  python3 - "$CONFIG" "$1" "$2" <<'PY' 2>/dev/null || printf '%s' "$2"
import json, sys
path, dotted, default = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    data = json.load(open(path))
except Exception:
    print(default); sys.exit()
cur = data
for key in dotted.split("."):
    if isinstance(cur, dict) and key in cur:
        cur = cur[key]
    else:
        cur = default; break
print(cur if not isinstance(cur, bool) else ("1" if cur else "0"))
PY
}

# True if the box currently has a usable upstream (not counting our own AP).
has_upstream() {
  local ap_name="$1"
  local conn
  conn="$(nmcli networking connectivity check 2>/dev/null || echo unknown)"
  # An active connection other than the AP means we have a real uplink.
  local active
  active="$(nmcli -t -f NAME,TYPE connection show --active 2>/dev/null \
            | grep -Ev ":(loopback)$" | grep -v "^${ap_name}:" || true)"
  [[ -n "$active" && "$conn" != "none" ]]
}

evaluate_once() {
  if ! command -v nmcli >/dev/null 2>&1; then
    log "nmcli not present — nothing to do"; return 0
  fi
  local enabled ap_name threshold
  enabled="$(cfg fallback_enabled 0)"
  ap_name="$(cfg ap_connection_name offgridcloud-ap)"
  threshold="$(cfg fail_threshold 3)"
  [[ "$threshold" =~ ^[0-9]+$ ]] || threshold=3

  local fails=0
  [[ -f "$FAIL_FILE" ]] && fails="$(cat "$FAIL_FILE" 2>/dev/null || echo 0)"

  if has_upstream "$ap_name"; then
    echo 0 > "$FAIL_FILE"
    if nmcli -t -f NAME connection show --active 2>/dev/null | grep -qx "$ap_name"; then
      log "upstream restored — leaving AP mode"
      nmcli connection down "$ap_name" >/dev/null 2>&1 || true
    fi
    return 0
  fi

  # No upstream.
  fails=$((fails + 1))
  echo "$fails" > "$FAIL_FILE"
  nmcli device wifi rescan >/dev/null 2>&1 || true

  if [[ "$enabled" != "1" ]]; then
    log "no upstream (fail $fails) — fallback disabled, staying put"
    return 0
  fi
  if (( fails >= threshold )); then
    if nmcli -t -f NAME connection show --active 2>/dev/null | grep -qx "$ap_name"; then
      return 0  # already hosting the AP
    fi
    log "no upstream for $fails checks — bringing up fallback AP '$ap_name'"
    nmcli connection up "$ap_name" >/dev/null 2>&1 \
      || log "failed to bring up AP '$ap_name' (does it exist? run apply.sh)"
  else
    log "no upstream (fail $fails/$threshold) — waiting"
  fi
}

if [[ $ONESHOT -eq 1 ]]; then
  evaluate_once
  exit 0
fi

log "watchdog started (config: $CONFIG)"
while true; do
  evaluate_once
  interval="$(cfg check_interval 20)"
  [[ "$interval" =~ ^[0-9]+$ ]] && (( interval >= 5 )) || interval=20
  sleep "$interval"
done
