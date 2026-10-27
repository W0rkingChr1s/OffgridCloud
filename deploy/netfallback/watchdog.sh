#!/usr/bin/env bash
# OffgridCloud network watchdog — the actual "Rückfallebene".
#
# Periodically checks whether the box has an upstream network (Ethernet or a
# known Wi-Fi). If it loses upstream for `fail_threshold` consecutive checks and
# the fallback is enabled, it brings up the self-hosted access point so the
# field team can still upload. When a known network returns, it drops the AP and
# rejoins it.
#
# A single Wi-Fi radio can't be AP and client at once, so while the AP is up
# NetworkManager never sees the known network come back on its own. Every
# `ap_probe_interval` seconds the watchdog therefore probes actively (see
# probe_known_networks): scan for a known SSID while still hosting the AP, and
# every `ap_blind_probe_interval` seconds drop the AP briefly to look as a
# client, since many drivers can't scan in AP mode.
#
# Runs as a loop service (root). Reads the exported config each iteration, so
# changes made in the UI take effect after the next `apply.sh` (which restarts
# this service). Use `--oneshot` to evaluate exactly once (used by apply.sh and
# for testing).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
PROBE_FILE="$STATE_DIR/ap-probe-last"
BLIND_FILE="$STATE_DIR/ap-blind-probe-last"
# Written by handover.sh while the companion app's upload Wi-Fi is up.
HANDOVER_STATE="$STATE_DIR/handover.state"
# How long `nmcli connection up` may take when rejoining a known network.
JOIN_WAIT="${OGC_NETWATCH_JOIN_WAIT:-25}"
# Settle time after a rescan before reading the scan results.
SCAN_SETTLE="${OGC_NETWATCH_SCAN_SETTLE:-4}"

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

ap_is_active() {
  nmcli -t -f NAME connection show --active 2>/dev/null | grep -qx "$1"
}

# Visible SSIDs, one per line (nmcli -t escapes ':' and '\' with a backslash).
visible_ssids() {
  nmcli -t -f SSID device wifi list --rescan yes 2>/dev/null \
    | sed -e 's/\\\(.\)/\1/g' | grep -v '^$' | sort -u
}

# Wi-Fi *client* profiles that may autoconnect, highest priority first, as
# "<priority>\t<ssid>\t<name>". Includes profiles OffgridCloud doesn't manage
# (e.g. the network entered in Raspberry Pi Imager → netplan-wlan0-<SSID>).
known_client_profiles() {
  local ap_name="$1" name type auto prio ssid mode
  while IFS=: read -r name type auto prio; do
    [[ "$type" == "802-11-wireless" ]] || continue
    [[ "$name" == "$ap_name" ]] && continue
    [[ "$auto" == "yes" ]] || continue
    mode="$(nmcli -t -f 802-11-wireless.mode connection show "$name" 2>/dev/null)"
    mode="${mode#*:}"
    [[ "$mode" == "ap" || "$mode" == "adhoc" || "$mode" == "mesh" ]] && continue
    ssid="$(nmcli -t -f 802-11-wireless.ssid connection show "$name" 2>/dev/null)"
    ssid="${ssid#*:}"
    [[ -n "$ssid" ]] || continue
    [[ "$prio" =~ ^-?[0-9]+$ ]] || prio=0
    printf '%s\t%s\t%s\n' "$prio" "$ssid" "$name"
  done < <(nmcli -t -f NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY connection show 2>/dev/null) \
    | sort -t $'\t' -k1,1nr
}

# Seconds since the timestamp stored in "$1" (huge if never stamped).
since_stamp() {
  local last=0
  [[ -f "$1" ]] && last="$(cat "$1" 2>/dev/null || echo 0)"
  [[ "$last" =~ ^[0-9]+$ ]] || last=0
  echo $(( $(date +%s) - last ))
}

cfg_seconds() {  # <key> <default> <minimum>
  local v
  v="$(cfg "$1" "$2")"
  [[ "$v" =~ ^[0-9]+$ ]] && (( v >= $3 )) || v="$2"
  echo "$v"
}

# Known profiles (from known_client_profiles) whose SSID is in the visible list,
# as "<ssid>\t<name>", priority order preserved.
in_range() {
  local profiles="$1" visible="$2" prio ssid name
  [[ -n "$visible" ]] || return 0
  while IFS=$'\t' read -r prio ssid name; do
    [[ -n "$ssid" ]] && grep -qxF -- "$ssid" <<<"$visible" && printf '%s\t%s\n' "$ssid" "$name"
  done <<<"$profiles"
  return 0
}

# Try candidates in order; 0 as soon as one gives us an uplink.
join_any() {
  local ap_name="$1" candidates="$2" ssid name
  while IFS=$'\t' read -r ssid name; do
    [[ -n "$name" ]] || continue
    log "known network '$ssid' in range — joining via '$name'"
    if nmcli --wait "$JOIN_WAIT" connection up "$name" >/dev/null 2>&1 \
         && has_upstream "$ap_name"; then
      log "rejoined '$ssid' — left AP mode"
      echo 0 > "$FAIL_FILE"
      return 0
    fi
    log "joining '$ssid' failed"
  done <<<"$candidates"
  return 1
}

# While hosting the AP: is a known network back? If so, leave the AP and join it.
#
# Two stages, because a single radio can't be AP and client at once:
#  1. every `ap_probe_interval` s: scan while still hosting — no interruption.
#  2. every `ap_blind_probe_interval` s, if stage 1 found nothing: drop the AP
#     for a few seconds and scan as a client. Needed because many drivers (the
#     Pi's brcmfmac among them) return nothing or only stale results in AP mode.
probe_known_networks() {
  local ap_name="$1" interval blind_interval profiles candidates
  interval="$(cfg_seconds ap_probe_interval 120 30)"
  blind_interval="$(cfg_seconds ap_blind_probe_interval 600 60)"
  (( $(since_stamp "$PROBE_FILE") >= interval )) || return 1
  date +%s > "$PROBE_FILE"

  profiles="$(known_client_profiles "$ap_name")"
  if [[ -z "$profiles" ]]; then
    log "AP mode: no known Wi-Fi profiles to probe for"
    return 1
  fi

  candidates="$(in_range "$profiles" "$(visible_ssids)")"
  if [[ -n "$candidates" ]]; then
    nmcli connection down "$ap_name" >/dev/null 2>&1 || true
  else
    (( $(since_stamp "$BLIND_FILE") >= blind_interval )) || return 1
    date +%s > "$BLIND_FILE"
    log "AP mode: nothing known seen while hosting — dropping AP briefly to scan"
    nmcli connection down "$ap_name" >/dev/null 2>&1 || true
    sleep "$SCAN_SETTLE"
    candidates="$(in_range "$profiles" "$(visible_ssids)")"
  fi

  join_any "$ap_name" "$candidates" && return 0

  log "no known network reachable — restoring fallback AP '$ap_name'"
  nmcli connection up "$ap_name" >/dev/null 2>&1 \
    || log "failed to restore AP '$ap_name'"
  return 1
}

# While the upload Wi-Fi is up its adapter belongs to hostapd, and on a
# single-radio box the uplink is paused on purpose — hands off, no fallback AP.
# Only enforce its deadline, in case the backend that should end it is gone.
# Returns 0 while a handover is running (skip this round).
handover_running() {
  [[ -f "$HANDOVER_STATE" ]] || return 1
  local iface deadline managed
  read -r iface deadline managed < "$HANDOVER_STATE" || true
  if [[ "$deadline" =~ ^[0-9]+$ ]] && (( $(date +%s) >= deadline )); then
    log "upload Wi-Fi on $iface past its maximum duration — dropping it"
    bash "$HERE/handover.sh" down || true
    return 1
  fi
  echo 0 > "$FAIL_FILE"
  return 0
}

evaluate_once() {
  if ! command -v nmcli >/dev/null 2>&1; then
    log "nmcli not present — nothing to do"; return 0
  fi
  handover_running && return 0
  local enabled ap_name threshold
  enabled="$(cfg fallback_enabled 0)"
  ap_name="$(cfg ap_connection_name offgridcloud-ap)"
  threshold="$(cfg fail_threshold 3)"
  [[ "$threshold" =~ ^[0-9]+$ ]] || threshold=3

  local fails=0
  [[ -f "$FAIL_FILE" ]] && fails="$(cat "$FAIL_FILE" 2>/dev/null || echo 0)"

  if has_upstream "$ap_name"; then
    echo 0 > "$FAIL_FILE"
    if ap_is_active "$ap_name"; then
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
  if ap_is_active "$ap_name"; then
    probe_known_networks "$ap_name" || true
    return 0
  fi
  if (( fails >= threshold )); then
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
