#!/usr/bin/env bash
# Raise or drop the upload Wi-Fi for the companion app's handover (needs root).
#
# The backend (app/handover.py) writes <data_dir>/handover.json with the
# adapter, SSID and the requesting phone's own key, then calls
#
#   sudo handover.sh up /opt/offgridcloud/data/handover.json
#   sudo handover.sh down
#
# "up" runs hostapd + a DHCP-only dnsmasq on the adapter. The network has no
# router and no DNS, so phones keep their mobile data for the internet and
# reach the box by IP. Two cases:
#
#   * a free adapter: taken away from NetworkManager for the session, handed
#     back on "down";
#   * "parent" set (the adapter is the box's Wi-Fi uplink): a second, virtual
#     interface is added to it and the AP runs there, on the client's channel,
#     so the uplink stays up. If the chip can't, "up" fails and cleans up — the
#     box is never taken offline for a handover.
#
# "down" undoes all of it. Both are idempotent.
#
# Backstop: handover.state carries a deadline; the watchdog runs "down" once
# it has passed, even if the backend is gone.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${OGC_NETWATCH_STATE_DIR:-/run/offgridcloud}"
STATE="$STATE_DIR/handover.state"
HOSTAPD_CONF="$STATE_DIR/handover-hostapd.conf"
HOSTAPD_PID="$STATE_DIR/handover-hostapd.pid"
DNSMASQ_PID="$STATE_DIR/handover-dnsmasq.pid"
LEASES="$STATE_DIR/handover.leases"
# How long hostapd may take to report the AP as enabled.
UP_WAIT="${OGC_HANDOVER_UP_WAIT:-15}"

log() { printf '[handover] %s\n' "$*"; }

stop_pid() {
  local file="$1" pid
  [[ -f "$file" ]] || return 0
  pid="$(cat "$file" 2>/dev/null)"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.3
    done
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$file"
}

down() {
  local iface="" deadline managed="yes" parent="-"
  if [[ -f "$STATE" ]]; then
    read -r iface deadline managed parent < "$STATE" || true
  fi
  stop_pid "$DNSMASQ_PID"
  stop_pid "$HOSTAPD_PID"
  if [[ -n "$iface" && -n "$parent" && "$parent" != "-" ]]; then
    # Virtual AP beside the uplink: just remove it, the client is untouched.
    iw dev "$iface" del 2>/dev/null || true
    log "upload Wi-Fi $iface beside $parent is down"
  elif [[ -n "$iface" ]]; then
    ip addr flush dev "$iface" 2>/dev/null || true
    if [[ "$managed" == "yes" ]] && command -v nmcli >/dev/null 2>&1; then
      # NetworkManager autoconnects the best known network again.
      nmcli device set "$iface" managed yes >/dev/null 2>&1 || true
    fi
    log "upload Wi-Fi on $iface is down"
  fi
  rm -f "$STATE" "$HOSTAPD_CONF" "$LEASES"
}

up() {
  local config="$1" values iface address start end mask deadline country managed parent band
  for bin in hostapd dnsmasq ip; do
    if ! command -v "$bin" >/dev/null 2>&1; then
      echo "$bin not found — run deploy/netfallback/install.sh" >&2
      return 2
    fi
  done
  mkdir -p "$STATE_DIR"
  down  # a leftover session never stacks

  values="$(python3 "$HERE/_handover.py" "$config" "$HOSTAPD_CONF")" || return 1
  read -r iface address start end mask deadline country parent band <<<"$values"

  if [[ "$parent" != "-" ]]; then
    up_beside "$iface" "$parent" "$deadline" || return 1
  else
    take_adapter "$iface" "$deadline" || return 1
  fi
  # Beside a running client the country is already set; changing it now could
  # move the client off its channel.
  [[ "$parent" != "-" ]] && country="-"
  start_ap "$iface" "$address" "$start" "$end" "$mask" "$country" || return 1
  echo "band=$band"
}

# Add the virtual AP interface to the uplink adapter (NetworkManager keeps
# its hands off it: install.sh marks the name unmanaged, and we say so here).
up_beside() {
  local iface="$1" parent="$2" deadline="$3"
  if ! command -v iw >/dev/null 2>&1; then
    echo "iw not found — run deploy/netfallback/install.sh" >&2
    return 2
  fi
  printf '%s %s %s %s\n' "$iface" "$deadline" "no" "$parent" > "$STATE"
  iw dev "$iface" del 2>/dev/null || true
  if ! iw dev "$parent" interface add "$iface" type __ap; then
    log "$parent cannot host an AP beside its client"
    down
    return 1
  fi
  command -v nmcli >/dev/null 2>&1 && nmcli device set "$iface" managed no >/dev/null 2>&1
  return 0
}

# Take a free adapter away from NetworkManager for the session.
take_adapter() {
  local iface="$1" deadline="$2" managed="no"
  if command -v nmcli >/dev/null 2>&1; then
    local state
    state="$(nmcli -t -f DEVICE,STATE device status 2>/dev/null | awk -F: -v d="$iface" '$1==d {print $2}')"
    [[ -n "$state" && "$state" != "unmanaged" ]] && managed="yes"
  fi
  # Written first, so "down" can clean up whatever happens next.
  printf '%s %s %s %s\n' "$iface" "$deadline" "$managed" "-" > "$STATE"

  if [[ "$managed" == "yes" ]]; then
    nmcli device set "$iface" managed no >/dev/null 2>&1 || true
    sleep 1
  fi
}

start_ap() {
  local iface="$1" address="$2" start="$3" end="$4" mask="$5" country="$6"
  command -v rfkill >/dev/null 2>&1 && rfkill unblock wifi 2>/dev/null || true
  if [[ "$country" != "-" ]] && command -v iw >/dev/null 2>&1; then
    iw reg set "$country" 2>/dev/null || true
  fi
  if ! { ip link set "$iface" up && ip addr flush dev "$iface" \
         && ip addr add "$address" dev "$iface"; }; then
    log "could not configure $iface"
    down
    return 1
  fi

  if ! hostapd -B -P "$HOSTAPD_PID" "$HOSTAPD_CONF" >/dev/null; then
    log "hostapd failed to start on $iface"
    down
    return 1
  fi
  local enabled=0
  for _ in $(seq 1 "$UP_WAIT"); do
    if command -v hostapd_cli >/dev/null 2>&1; then
      hostapd_cli -p /run/hostapd -i "$iface" status 2>/dev/null | grep -q '^state=ENABLED' \
        && { enabled=1; break; }
    else
      sleep 2; enabled=1; break
    fi
    sleep 1
  done
  if [[ $enabled -ne 1 ]]; then
    log "hostapd did not enable the AP on $iface"
    down
    return 1
  fi

  # DHCP only (--port=0: no DNS); no router or DNS option, see header.
  if ! dnsmasq --conf-file=/dev/null --port=0 --no-resolv --no-hosts \
      --interface="$iface" --bind-interfaces --except-interface=lo \
      --dhcp-range="$start,$end,$mask,1h" --dhcp-authoritative \
      --dhcp-option=option:router --dhcp-option=option:dns-server \
      --dhcp-leasefile="$LEASES" --pid-file="$DNSMASQ_PID"; then
    log "dnsmasq failed on $iface"
    down
    return 1
  fi
  log "upload Wi-Fi up on $iface ($address)"
}

if [[ $EUID -ne 0 ]]; then
  echo "handover.sh must run as root." >&2
  exit 1
fi

case "${1:-}" in
  up)
    [[ -n "${2:-}" ]] || { echo "usage: handover.sh up <handover.json>" >&2; exit 2; }
    up "$2"
    ;;
  down)
    down
    ;;
  *)
    echo "usage: handover.sh up <handover.json> | down" >&2
    exit 2
    ;;
esac
