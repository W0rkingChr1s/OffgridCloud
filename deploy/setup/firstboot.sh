#!/usr/bin/env bash
# OffgridCloud image — first boot (root, oneshot, before the app starts).
#
# The flashable image carries no secrets: every box flashed from it would share
# them. So on the very first boot this script makes the box unique and puts it
# into setup mode:
#
#   1. generates OGC_SECRET_KEY into .env,
#   2. reads the optional offgridcloud-setup.txt from the boot partition
#      (SETUP_CODE / SETUP_WIFI_PASSWORD / COUNTRY), stores the code's hash for
#      the wizard and removes the file from the card,
#   3. sets the temporary hostname "offgridcloud-setup" (reachable via cable as
#      http://offgridcloud-setup.local) and suggests a unique final one,
#   4. sets the Wi-Fi country + unblocks the radio, and
#   5. swaps Caddy to a plain-HTTP setup config (no certificate warnings in the
#      captive portal), then arms setup-ap.sh via the setup-pending marker.
#
# Runs once: guarded by /var/lib/offgridcloud/firstboot-pending (created at
# image build time by deploy/setup/install.sh).
set -euo pipefail

PREFIX="${OGC_PREFIX:-/opt/offgridcloud}"
SERVICE_USER="${OGC_SERVICE_USER:-offgrid}"
STATE_DIR=/var/lib/offgridcloud
ENV_FILE="$PREFIX/.env"
SETUP_HOSTNAME="offgridcloud-setup"

log() { printf '[firstboot] %s\n' "$*"; }

[[ -f "$STATE_DIR/firstboot-pending" ]] || { log "nothing to do"; exit 0; }

# --- 1. Per-box secret --------------------------------------------------------
if ! grep -q '^OGC_SECRET_KEY=' "$ENV_FILE" 2>/dev/null; then
  echo "OGC_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')" >> "$ENV_FILE"
  log "generated OGC_SECRET_KEY"
fi
chown "$SERVICE_USER:$SERVICE_USER" "$ENV_FILE"; chmod 600 "$ENV_FILE"

# --- 2. Optional operator file on the boot partition ------------------------
SETUP_CODE=""; SETUP_WIFI_PASSWORD=""; COUNTRY="DE"
for f in /boot/firmware/offgridcloud-setup.txt /boot/offgridcloud-setup.txt; do
  [[ -f "$f" ]] || continue
  log "reading $f"
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    key="${line%%=*}"; val="${line#*=}"
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    val="$(printf '%s' "$val" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    case "$key" in
      SETUP_CODE) SETUP_CODE="$val" ;;
      SETUP_WIFI_PASSWORD) SETUP_WIFI_PASSWORD="$val" ;;
      COUNTRY) COUNTRY="$(printf '%s' "$val" | tr '[:lower:]' '[:upper:]')" ;;
    esac
  done < "$f"
  # The card may travel; don't leave the code lying on it in the clear.
  rm -f "$f"
done
[[ "$COUNTRY" =~ ^[A-Z]{2}$ ]] || COUNTRY="DE"
if [[ -n "$SETUP_WIFI_PASSWORD" && ( ${#SETUP_WIFI_PASSWORD} -lt 8 || ${#SETUP_WIFI_PASSWORD} -gt 63 ) ]]; then
  log "SETUP_WIFI_PASSWORD must be 8-63 characters — ignoring it, setup Wi-Fi stays open"
  SETUP_WIFI_PASSWORD=""
fi

SUFFIX="$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 5 || true)"

mkdir -p "$PREFIX/data" "$STATE_DIR"
chmod 700 "$STATE_DIR"
# setup.json — read by the app (app/setup_wizard.py): code hash, suggestions.
SETUP_CODE="$SETUP_CODE" COUNTRY="$COUNTRY" SUFFIX="$SUFFIX" python3 - "$PREFIX/data/setup.json" <<'PY'
import hashlib, json, os, secrets, sys
code = os.environ["SETUP_CODE"].strip()
state = {"suggested_hostname": "offgridcloud-" + os.environ["SUFFIX"], "country": os.environ["COUNTRY"]}
if code:
    salt = secrets.token_hex(16)
    state["salt"] = salt
    state["code_sha256"] = hashlib.sha256((salt + code).encode()).hexdigest()
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(state, fh)
PY
chown "$SERVICE_USER:$SERVICE_USER" "$PREFIX/data" "$PREFIX/data/setup.json"
chmod 600 "$PREFIX/data/setup.json"
[[ -n "$SETUP_CODE" ]] && log "setup code configured — the wizard will ask for it"

# setup-ap.conf — root only: what setup-ap.sh hosts.
umask 077
printf 'SSID=%q\nPSK=%q\nCOUNTRY=%q\n' "OffgridCloud-Setup-${SUFFIX:0:4}" "$SETUP_WIFI_PASSWORD" "$COUNTRY" \
  > "$STATE_DIR/setup-ap.conf"
umask 022

# --- 3. Temporary hostname ----------------------------------------------------
# Written directly (no hostnamectl): we run before D-Bus/avahi are up.
echo "$SETUP_HOSTNAME" > /etc/hostname
hostname "$SETUP_HOSTNAME" 2>/dev/null || true
if grep -qE '^\s*127\.0\.1\.1' /etc/hosts; then
  sed -i -E "s/^\s*127\.0\.1\.1.*/127.0.1.1\t${SETUP_HOSTNAME}/" /etc/hosts
else
  printf '127.0.1.1\t%s\n' "$SETUP_HOSTNAME" >> /etc/hosts
fi

# --- 4. Wi-Fi country (Raspberry Pi OS keeps Wi-Fi blocked until it's set) ---
# Persist it the way raspi-config does (kernel cmdline), without calling
# raspi-config itself — it talks to services that aren't up this early.
for cmdline in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
  [[ -f "$cmdline" ]] || continue
  sed -i -E 's/ ?cfg80211\.ieee80211_regdom=[A-Z]{2}//' "$cmdline"
  sed -i -E "1s/\$/ cfg80211.ieee80211_regdom=${COUNTRY}/" "$cmdline"
  break
done
iw reg set "$COUNTRY" 2>/dev/null || true
rfkill unblock wifi 2>/dev/null || true

# --- 5. Caddy: plain HTTP while in setup mode -------------------------------
PORT="$(sed -n 's/.*--port \([0-9]*\).*/\1/p' /etc/systemd/system/offgridcloud.service 2>/dev/null | head -1)"
PORT="${PORT:-8000}"
if [[ -d /etc/caddy ]]; then
  cat > /etc/caddy/Caddyfile <<CADDY
# Setup mode (deploy/setup/firstboot.sh) — replaced by deploy/https/apply.sh
# once the setup wizard has run. Plain HTTP on purpose: a captive portal must
# not start with a certificate warning.
:80 {
    reverse_proxy localhost:${PORT}
}
CADDY
fi

touch "$STATE_DIR/setup-pending"
rm -f "$STATE_DIR/firstboot-pending"
log "done — box is in setup mode"
