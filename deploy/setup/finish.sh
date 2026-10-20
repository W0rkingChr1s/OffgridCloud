#!/usr/bin/env bash
# OffgridCloud image — leave setup mode after the web wizard (root).
#
# Started detached by the app (POST /api/setup/complete) via a NOPASSWD sudoers
# rule. Refuses to do anything unless the box is still in setup mode, so the
# rule can't be used to re-run it later. It
#
#   1. ends setup mode (marker gone → setup-ap.sh stops) and removes the setup
#      Wi-Fi + captive-portal DNS,
#   2. applies the network config the wizard exported (wizard Wi-Fi + the
#      fallback AP; deploy/netfallback/apply.sh),
#   3. sets the final hostname and the real HTTPS config (deploy/https/apply.sh),
#   4. joins the chosen Wi-Fi.
#
# Logs to /var/lib/offgridcloud/finish.log.
set -uo pipefail

PREFIX="${OGC_PREFIX:-/opt/offgridcloud}"
STATE_DIR=/var/lib/offgridcloud
RESULT="$PREFIX/data/setup-result.json"
NETWORK_JSON="$PREFIX/data/network.json"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then echo "Please run as root." >&2; exit 1; fi
if [[ ! -f "$STATE_DIR/setup-pending" ]]; then
  echo "Not in setup mode — nothing to do." >&2; exit 1
fi
exec >>"$STATE_DIR/finish.log" 2>&1
log() { printf '[finish %s] %s\n' "$(date -Is)" "$*"; }

# Let the HTTP response reach the browser before its Wi-Fi goes away.
sleep 5

HOST="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("hostname",""))' "$RESULT" 2>/dev/null || true)"
COUNTRY="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("country",""))' "$RESULT" 2>/dev/null || true)"
if [[ ! "$HOST" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]]; then
  log "invalid or missing hostname in $RESULT ('$HOST') — aborting, still in setup mode"; exit 1
fi
[[ "$COUNTRY" =~ ^[A-Z]{2}$ ]] || COUNTRY=""

# --- 1. End setup mode ------------------------------------------------------------
rm -f "$STATE_DIR/setup-pending" "$PREFIX/data/setup.json"
systemctl disable offgridcloud-setup-ap.service >/dev/null 2>&1 || true
systemctl stop offgridcloud-setup-ap.service >/dev/null 2>&1 || true
bash "$HERE/setup-ap.sh" --teardown
log "setup Wi-Fi removed"

if [[ -n "$COUNTRY" ]]; then
  for cmdline in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
    [[ -f "$cmdline" ]] || continue
    sed -i -E 's/ ?cfg80211\.ieee80211_regdom=[A-Z]{2}//' "$cmdline"
    sed -i -E "1s/\$/ cfg80211.ieee80211_regdom=${COUNTRY}/" "$cmdline"
    break
  done
fi

# --- 2. Network (wizard Wi-Fi + fallback AP) --------------------------------
if [[ -x "$PREFIX/deploy/netfallback/apply.sh" && -f "$NETWORK_JSON" ]]; then
  "$PREFIX/deploy/netfallback/apply.sh" "$NETWORK_JSON" && log "network config applied" \
    || log "network apply failed — fix it under Admin → Netzwerk"
else
  log "netfallback helper or $NETWORK_JSON missing — network left as is"
fi

# --- 3. Hostname + HTTPS ----------------------------------------------------
PORT="$(sed -n 's/.*--port \([0-9]*\).*/\1/p' /etc/systemd/system/offgridcloud.service 2>/dev/null | head -1)"
if [[ -x "$PREFIX/deploy/https/apply.sh" ]]; then
  OGC_PREFIX="$PREFIX" "$PREFIX/deploy/https/apply.sh" --hostname "$HOST" --prefix "$PREFIX" \
    --port "${PORT:-8000}" && log "hostname + HTTPS set: https://$HOST.local" \
    || log "HTTPS apply failed — see above"
else
  hostnamectl set-hostname "$HOST" 2>/dev/null || { echo "$HOST" > /etc/hostname; hostname "$HOST"; }
  sed -i -E "s/^\s*127\.0\.1\.1.*/127.0.1.1\t${HOST}/" /etc/hosts
  systemctl restart avahi-daemon 2>/dev/null || true
  log "hostname set: $HOST"
fi

# --- 4. Join the chosen Wi-Fi -----------------------------------------------
WIFI_IF="$(nmcli -t -f DEVICE,TYPE device status 2>/dev/null | awk -F: '$2=="wifi"{print $1; exit}')"
if [[ -n "$WIFI_IF" ]]; then
  nmcli device connect "$WIFI_IF" >/dev/null 2>&1 && log "joined Wi-Fi on $WIFI_IF" \
    || log "no Wi-Fi joined yet — the fallback watchdog takes over if there is no uplink"
fi
log "setup finished"
