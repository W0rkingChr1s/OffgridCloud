#!/usr/bin/env bash
# OffgridCloud image — setup Wi-Fi + captive portal while in setup mode (root).
#
# As long as the setup wizard hasn't run (/var/lib/offgridcloud/setup-pending),
# this loop makes sure the box can be reached:
#
#   * it has an uplink of its own (cable, or a Wi-Fi set up in Raspberry Pi
#     Imager)  → nothing to host; the wizard is at http://offgridcloud-setup.local
#   * no uplink for 3 checks in a row → it hosts the Wi-Fi from setup-ap.conf
#     (open by default, "OffgridCloud-Setup-XXXX") on 10.42.0.1 and answers
#     every DNS name with that address, so phones pop up the wizard as a
#     captive portal (the app redirects their probe URLs to /setup).
#
# Before raising the AP it scans once and leaves the SSIDs for the wizard
# (a single radio can't scan reliably while it is an AP). When the marker is
# gone (finish.sh), it tears everything down and exits.
set -uo pipefail

STATE_DIR=/var/lib/offgridcloud
CONF="$STATE_DIR/setup-ap.conf"
AP_NAME="offgridcloud-setup"
AP_ADDRESS="10.42.0.1/24"
DNS_HIJACK=/etc/NetworkManager/dnsmasq-shared.d/offgridcloud-setup.conf
SCAN_FILE=/run/offgridcloud/setup-wifi.json
INTERVAL=10
THRESHOLD=3

log() { printf '[setup-ap] %s\n' "$*"; }

SSID="OffgridCloud-Setup"; PSK=""; COUNTRY=""
# shellcheck source=/dev/null
[[ -f "$CONF" ]] && source "$CONF"

wifi_if() {
  nmcli -t -f DEVICE,TYPE device status 2>/dev/null | awk -F: '$2=="wifi"{print $1; exit}'
}

# Same notion of "uplink" as the fallback watchdog (deploy/netfallback/
# watchdog.sh): any connected device but our AP that carries an IPv4 address.
has_upstream() {
  local dev type state conn
  while IFS=: read -r dev type state conn; do
    [[ -z "$dev" || "$type" == "loopback" || -z "$type" ]] && continue
    [[ "$conn" == "$AP_NAME" ]] && continue
    [[ "$state" == connected* ]] || continue
    nmcli -t -f IP4.ADDRESS device show "$dev" 2>/dev/null \
      | grep -qE '[0-9]{1,3}(\.[0-9]{1,3}){3}' && return 0
  done < <(nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status 2>/dev/null)
  return 1
}

ap_active() {
  nmcli -t -f NAME connection show --active 2>/dev/null | grep -qx "$AP_NAME"
}

save_scan() {
  mkdir -p "$(dirname "$SCAN_FILE")"
  nmcli device wifi rescan >/dev/null 2>&1 || true
  sleep 4
  nmcli -t -f SSID device wifi list 2>/dev/null | python3 -c '
import json, sys
seen = []
for line in sys.stdin:
    ssid = line.rstrip("\n").replace("\\:", ":").strip()
    if ssid and ssid not in seen:
        seen.append(ssid)
json.dump(seen, sys.stdout)
' > "$SCAN_FILE.tmp" && mv "$SCAN_FILE.tmp" "$SCAN_FILE"
  chmod 644 "$SCAN_FILE" 2>/dev/null || true
}

ap_up() {
  local ifname; ifname="$(wifi_if)"
  if [[ -z "$ifname" ]]; then
    log "no Wi-Fi device — the wizard is only reachable via cable"; return
  fi
  rfkill unblock wifi 2>/dev/null || true
  [[ -n "$COUNTRY" ]] && iw reg set "$COUNTRY" 2>/dev/null
  save_scan
  mkdir -p "$(dirname "$DNS_HIJACK")"
  printf '# Captive portal for the OffgridCloud setup Wi-Fi (removed by finish.sh).\naddress=/#/%s\n' \
    "${AP_ADDRESS%/*}" > "$DNS_HIJACK"
  nmcli connection delete "$AP_NAME" >/dev/null 2>&1 || true
  nmcli connection add type wifi con-name "$AP_NAME" ifname "$ifname" ssid "$SSID" \
    802-11-wireless.mode ap 802-11-wireless.band bg \
    ipv4.method shared ipv4.addresses "$AP_ADDRESS" ipv6.method disabled \
    connection.autoconnect no >/dev/null || { log "could not create AP connection"; return; }
  if [[ -n "$PSK" ]]; then
    nmcli connection modify "$AP_NAME" 802-11-wireless-security.key-mgmt wpa-psk \
      802-11-wireless-security.psk "$PSK" 802-11-wireless-security.proto rsn \
      802-11-wireless-security.pairwise ccmp 802-11-wireless-security.group ccmp >/dev/null
  fi
  if nmcli connection up "$AP_NAME" >/dev/null 2>&1; then
    log "hosting setup Wi-Fi '$SSID' ($([[ -n "$PSK" ]] && echo WPA2 || echo open)) on ${AP_ADDRESS%/*}"
  else
    log "failed to bring up the setup Wi-Fi"
  fi
}

teardown() {
  nmcli connection down "$AP_NAME" >/dev/null 2>&1 || true
  nmcli connection delete "$AP_NAME" >/dev/null 2>&1 || true
  rm -f "$DNS_HIJACK"
}

if [[ "${1:-}" == "--teardown" ]]; then teardown; exit 0; fi
command -v nmcli >/dev/null 2>&1 || { log "nmcli missing — nothing to host"; exit 0; }

log "setup mode — watching for an uplink"
fails=0
while [[ -f "$STATE_DIR/setup-pending" ]]; do
  if has_upstream; then
    fails=0
    if ap_active; then log "uplink present — setup Wi-Fi off"; teardown; fi
  else
    fails=$((fails + 1))
    if (( fails >= THRESHOLD )) && ! ap_active; then ap_up; fi
  fi
  sleep "$INTERVAL"
done
log "setup finished — stopping"
teardown
