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
  # We have a usable uplink if any device other than our AP is connected AND
  # carries an IPv4 address. This is the signal that matters for the fallback:
  # "the router is reachable and gave us a lease". We deliberately do NOT gate on
  # `nmcli networking connectivity`, which probes *internet* reachability and
  # returns "none"/"unknown" on many setups (checking disabled, or
  # netplan/networkd-rendered profiles) even while a real LAN link with an IP is
  # up — that false negative would drop the box into AP mode while still online.
  local ap_name="$1" dev type state conn
  while IFS=: read -r dev type state conn; do
    [[ -z "$dev" || "$type" == "loopback" || "$type" == "" ]] && continue
    [[ "$conn" == "$ap_name" ]] && continue
    [[ "$state" == connected* ]] || continue
    if nmcli -t -f IP4.ADDRESS device show "$dev" 2>/dev/null \
         | grep -qE '[0-9]{1,3}(\.[0-9]{1,3}){3}'; then
      return 0
    fi
  done < <(nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status 2>/dev/null)
  return 1
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
