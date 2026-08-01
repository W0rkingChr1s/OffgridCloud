#!/usr/bin/env bash
# Remove a native OffgridCloud install created by deploy/install.sh.
#
# INTERACTIVE, like the installer: it first reports what it found on this box,
# then asks what to remove (whiptail menu when available, plain text prompts
# otherwise) and shows a summary before touching anything.
#
# Undoes everything the installer and its opt-in features set up: the systemd
# service + VPN drop-in, a running VPN tunnel, all sudoers rules, the on-box
# console (kiosk, incl. restoring the previous boot target), the HTTPS reverse
# proxy (Caddy config + original hostname) and the network-redundancy layer
# (watchdog + fallback AP). Data and .env are KEPT unless you ask for a purge.
#
# Usage:
#   sudo offgridcloud-uninstall                      # installed on the box
#   sudo /opt/offgridcloud/src/deploy/uninstall.sh   # from the checkout
#   sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/W0rkingChr1s/OffgridCloud/main/deploy/uninstall.sh)"
#
# Options (optional — the questions cover the same ground):
#   --prefix DIR    Install location (default: /opt/offgridcloud).
#   --purge         Also delete data/ (database + media buffer) and .env.
#   --remove-user   Also delete the 'offgrid' service user (incl. home dir).
#   --wifi          Also delete the Wi-Fi profiles (ogc-wifi-*) the app created.
#   --packages      Also remove packages pulled in for OffgridCloud (caddy,
#                   rclone, ffmpeg, wireguard-tools, openvpn, speedtest).
#   --no-backup     Skip the safety backup (DB + .env) taken before a purge.
#   --dry-run       Show what would happen, change nothing.
#   -y, --yes       Don't ask; use the options given.
#   -h, --help      Show this help and exit.
#
# Unattended (scripts, CI) — same OGC_* style as the installer:
#   OGC_PREFIX=/opt/offgridcloud        OGC_NONINTERACTIVE=1
#   OGC_UNINSTALL_PURGE=0               OGC_UNINSTALL_WIFI=0
#   OGC_UNINSTALL_USER=0                OGC_UNINSTALL_PACKAGES=0
#   OGC_UNINSTALL_BACKUP=1              OGC_UNINSTALL_DRY_RUN=0
set -euo pipefail

# --- Defaults ---------------------------------------------------------------
PREFIX="${OGC_PREFIX:-/opt/offgridcloud}"
SERVICE_USER="${OGC_SERVICE_USER:-offgrid}"
PURGE="${OGC_UNINSTALL_PURGE:-0}"
REMOVE_WIFI="${OGC_UNINSTALL_WIFI:-0}"
REMOVE_USER="${OGC_UNINSTALL_USER:-0}"
REMOVE_PACKAGES="${OGC_UNINSTALL_PACKAGES:-0}"
DO_BACKUP="${OGC_UNINSTALL_BACKUP:-1}"
DRY_RUN="${OGC_UNINSTALL_DRY_RUN:-0}"
NONINTERACTIVE="${OGC_NONINTERACTIVE:-0}"
BACKUP_DIR="${OGC_UNINSTALL_BACKUP_DIR:-/root}"
UNINSTALL_URL="https://raw.githubusercontent.com/W0rkingChr1s/OffgridCloud/main/deploy/uninstall.sh"

usage() {
  if [[ -f "${BASH_SOURCE[0]}" ]]; then
    sed -n '2,35p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  else
    echo "sudo uninstall.sh [--prefix DIR] [--purge] [--remove-user] [--wifi]"
    echo "                  [--packages] [--no-backup] [--dry-run] [-y]"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="${2:?--prefix needs a value}"; shift 2 ;;
    --purge) PURGE=1; shift ;;
    --remove-user) REMOVE_USER=1; shift ;;
    --wifi) REMOVE_WIFI=1; shift ;;
    --packages) REMOVE_PACKAGES=1; shift ;;
    --no-backup) DO_BACKUP=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -y|--yes|--non-interactive|--defaults) NONINTERACTIVE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then echo "Please run as root (sudo)." >&2; exit 1; fi

step() { printf '\n\033[1;36m>> %s\033[0m\n' "$1"; }

# Every destructive action goes through run(), so --dry-run is a real preview
# and not a second, drifting code path.
run() {
  if [[ $DRY_RUN -eq 1 ]]; then printf '   [dry-run] %s\n' "$*"; return 0; fi
  "$@"
}

# In a dry-run every action "succeeds" without doing anything — guard the
# "…done" messages with this so a preview never claims a change happened.
ran() { [[ $DRY_RUN -eq 0 ]]; }

# --- What is actually on this box? ------------------------------------------
HAS_SERVICE=0; [[ -f /etc/systemd/system/offgridcloud.service ]] && HAS_SERVICE=1
HAS_KIOSK=0;   [[ -f /etc/systemd/system/offgrid-kiosk.service ]] && HAS_KIOSK=1
HAS_NETFB=0;   [[ -f /etc/systemd/system/offgridcloud-netwatch.service ]] && HAS_NETFB=1
HAS_VPN=0;     [[ -f /etc/systemd/system/offgridcloud.service.d/10-vpn-caps.conf ]] && HAS_VPN=1
HAS_HTTPS=0
if [[ -f /etc/caddy/Caddyfile ]] && grep -q 'Managed by deploy/https/apply.sh' /etc/caddy/Caddyfile 2>/dev/null; then
  HAS_HTTPS=1
fi
HAS_PREFIX=0;  [[ -d "$PREFIX" ]] && HAS_PREFIX=1
HAS_USER=0;    id -u "$SERVICE_USER" >/dev/null 2>&1 && HAS_USER=1
HAS_DB=0;      [[ -f "$PREFIX/data/offgridcloud.db" ]] && HAS_DB=1
WIFI_COUNT=0
if command -v nmcli >/dev/null 2>&1; then
  WIFI_COUNT="$(nmcli -t -f NAME connection show 2>/dev/null | grep -c '^ogc-wifi-' || true)"
fi
DATA_SIZE="—"
[[ -d "$PREFIX/data" ]] && DATA_SIZE="$(du -sh "$PREFIX/data" 2>/dev/null | cut -f1)"

if [[ $HAS_SERVICE -eq 0 && $HAS_PREFIX -eq 0 && $HAS_KIOSK -eq 0 && $HAS_NETFB -eq 0 && $HAS_HTTPS -eq 0 ]]; then
  echo "No OffgridCloud installation found (looked in $PREFIX and systemd)."
  echo "Wrong location? Pass it explicitly:  sudo $0 --prefix /pfad/zur/installation"
  exit 0
fi

yesno() { [[ "$1" -eq 1 ]] && echo "ja" || echo "nein"; }
found() { [[ "$1" -eq 1 ]] && echo "gefunden" || echo "nicht vorhanden"; }

found_report() {
  cat <<EOF
Gefunden in $PREFIX:

  Dienst (offgridcloud) ....... $(found $HAS_SERVICE)
  HTTPS (Caddy + Hostname) .... $(found $HAS_HTTPS)
  Netzwerk-Redundanz .......... $(found $HAS_NETFB)
  VPN-Client (Drop-in) ........ $(found $HAS_VPN)
  Kiosk-Konsole ............... $(found $HAS_KIOSK)
  Dienst-Benutzer ($SERVICE_USER) .... $(found $HAS_USER)
  Datenbank + Puffer .......... $(found $HAS_DB) ($DATA_SIZE)
  WLAN-Profile (ogc-wifi-*) ... $WIFI_COUNT
EOF
}

# --- Interactive questionnaire ----------------------------------------------
# Prompts read from the controlling terminal, exactly like the installer.
if [[ $NONINTERACTIVE -eq 0 ]] && { [[ ! -r /dev/tty ]] || [[ ! -w /dev/tty ]]; }; then
  NONINTERACTIVE=1
fi

# The `curl ... | sudo bash` trap: sudo runs us in its own pseudo-terminal but
# never forwards keystrokes, so a menu would show and ignore every key. Verify
# the keyboard answers before committing to prompts (see deploy/install.sh).
if [[ $NONINTERACTIVE -eq 0 && ! -t 0 ]]; then
  printf '\n  Tastatur-Check: bitte \033[1mENTER\033[0m drücken, um fortzufahren … ' > /dev/tty
  if IFS= read -r -t 30 _ < /dev/tty; then
    printf '\n' > /dev/tty
  else
    {
      printf '\n  \033[1;31mKeine Tastatureingabe möglich.\033[0m\n'
      printf '  Das passiert bei »curl … | sudo bash«: sudo führt das Skript in einem\n'
      printf '  eigenen Terminal aus und reicht deine Tastendrücke nicht durch.\n\n'
      printf '  Bitte so neu starten:\n\n'
      printf '    sudo bash -c "$(curl -fsSL %s)"\n\n' "$UNINSTALL_URL"
    } > /dev/tty
    exit 1
  fi
fi

ask_yn() {  # ask_yn VAR "Question" <0|1 default>
  local __var="$1" __q="$2" __def="$3" __ans="" __hint
  [[ "$__def" -eq 1 ]] && __hint="J/n" || __hint="j/N"
  if [[ $NONINTERACTIVE -eq 1 ]]; then printf -v "$__var" '%s' "$__def"; return; fi
  while true; do
    printf '\033[1m%s\033[0m [%s]: ' "$__q" "$__hint" > /dev/tty
    IFS= read -r __ans < /dev/tty || __ans=""
    case "${__ans:-}" in
      "") printf -v "$__var" '%s' "$__def"; return ;;
      [JjYy]|[Jj]a|[Yy]es) printf -v "$__var" '%s' 1; return ;;
      [Nn]|[Nn]ein|[Nn]o) printf -v "$__var" '%s' 0; return ;;
      *) printf '  Bitte j oder n eingeben.\n' > /dev/tty ;;
    esac
  done
}

USE_WHIPTAIL=0
WT_BACKTITLE="OffgridCloud — Deinstallation"
if [[ $NONINTERACTIVE -eq 0 ]] && command -v whiptail >/dev/null 2>&1; then
  USE_WHIPTAIL=1
  export TERM="${TERM:-linux}"
fi

wt_onoff() { [[ "$1" -eq 1 ]] && echo ON || echo OFF; }

wt_msg() {  # wt_msg "text" [height] [width]
  whiptail --backtitle "$WT_BACKTITLE" --title "OffgridCloud" \
    --msgbox "$1" "${2:-16}" "${3:-72}" >/dev/tty 2>&1 </dev/tty || true
}

if [[ $NONINTERACTIVE -eq 0 && $USE_WHIPTAIL -eq 1 ]]; then
  # ---- Graphical questionnaire (whiptail) ----------------------------------
  wt_msg "OffgridCloud wird entfernt.

$(found_report)

Im nächsten Schritt wählst du, was zusätzlich verschwinden soll." 22 74

  _sel=$(whiptail --backtitle "$WT_BACKTITLE" --title "Was soll mit entfernt werden?" \
    --separate-output --checklist \
    "App, Dienst und alle Zusatzfunktionen werden immer entfernt.\nLeertaste schaltet um, Enter bestätigt:" 18 78 5 \
    DATA     "Daten löschen: Datenbank, Medien-Puffer, .env ($DATA_SIZE)"  "$(wt_onoff "$PURGE")" \
    WIFI     "WLAN-Profile der Box löschen (ogc-wifi-*: $WIFI_COUNT)"      "$(wt_onoff "$REMOVE_WIFI")" \
    USER     "Dienst-Benutzer '$SERVICE_USER' löschen (inkl. Home)"        "$(wt_onoff "$REMOVE_USER")" \
    PACKAGES "Pakete entfernen (caddy, rclone, ffmpeg, VPN, speedtest)"    "$(wt_onoff "$REMOVE_PACKAGES")" \
    DRYRUN   "Nur anzeigen, nichts verändern (Trockenlauf)"                "$(wt_onoff "$DRY_RUN")" \
    2>&1 1>/dev/tty </dev/tty) \
    || { echo "Abgebrochen — nichts wurde verändert." > /dev/tty; exit 0; }

  PURGE=0; REMOVE_WIFI=0; REMOVE_USER=0; REMOVE_PACKAGES=0; DRY_RUN=0
  while IFS= read -r _tag; do
    case "$_tag" in
      DATA)     PURGE=1 ;;
      WIFI)     REMOVE_WIFI=1 ;;
      USER)     REMOVE_USER=1 ;;
      PACKAGES) REMOVE_PACKAGES=1 ;;
      DRYRUN)   DRY_RUN=1 ;;
    esac
  done <<< "$_sel"

  if [[ $PURGE -eq 1 && $HAS_DB -eq 1 ]]; then
    if whiptail --backtitle "$WT_BACKTITLE" --title "Sicherung anlegen?" --yesno \
      "Vor dem Löschen eine Sicherung von Datenbank und .env nach\n$BACKUP_DIR anlegen?\n\n(Der Medien-Puffer ist nicht enthalten — nur DB + Schlüssel.)" \
      12 72 >/dev/tty 2>&1 </dev/tty; then DO_BACKUP=1; else DO_BACKUP=0; fi
  fi
else
  # ---- Plain text questionnaire --------------------------------------------
  if [[ $NONINTERACTIVE -eq 0 ]]; then
    cat > /dev/tty <<'BANNER'

  ┌────────────────────────────────────────────────┐
  │   OffgridCloud — Deinstallation                 │
  │   App, Dienst und Zusatzfunktionen gehen weg.   │
  │   Daten bleiben, wenn du nicht ausdrücklich     │
  │   das Löschen bestätigst.                       │
  └────────────────────────────────────────────────┘
BANNER
    found_report > /dev/tty
    echo > /dev/tty
    ask_yn PURGE           "Daten löschen (Datenbank, Medien-Puffer, .env)?"        "$PURGE"
    if [[ $PURGE -eq 1 && $HAS_DB -eq 1 ]]; then
      ask_yn DO_BACKUP     "  … vorher Sicherung (DB + .env) nach $BACKUP_DIR?"     "$DO_BACKUP"
    fi
    [[ "${WIFI_COUNT:-0}" -gt 0 ]] && \
      ask_yn REMOVE_WIFI   "WLAN-Profile der Box löschen (ogc-wifi-*, $WIFI_COUNT)?" "$REMOVE_WIFI"
    [[ $HAS_USER -eq 1 ]] && \
      ask_yn REMOVE_USER   "Dienst-Benutzer '$SERVICE_USER' löschen (inkl. Home)?"  "$REMOVE_USER"
    ask_yn REMOVE_PACKAGES "Mitinstallierte Pakete entfernen (caddy, rclone, …)?"   "$REMOVE_PACKAGES"
    ask_yn DRY_RUN         "Nur anzeigen, was passieren würde (Trockenlauf)?"       "$DRY_RUN"
  fi
fi

# --- Summary + final confirmation -------------------------------------------
summary() {
  cat <<EOF
Entfernt wird:
  Dienst, systemd-Units, sudoers-Regeln, App-Dateien unter $PREFIX
  Zusatzfunktionen: HTTPS, Netzwerk-Redundanz, VPN-Drop-in, Kiosk-Konsole

  Daten + .env löschen ....... $(yesno "$PURGE")$([[ $PURGE -eq 1 && $DO_BACKUP -eq 1 ]] && echo " (Sicherung nach $BACKUP_DIR)")
  WLAN-Profile löschen ....... $(yesno "$REMOVE_WIFI")
  Benutzer '$SERVICE_USER' löschen .. $(yesno "$REMOVE_USER")
  Pakete entfernen ........... $(yesno "$REMOVE_PACKAGES")
  Trockenlauf ................ $(yesno "$DRY_RUN")
EOF
}

if [[ $NONINTERACTIVE -eq 0 ]]; then
  if [[ $USE_WHIPTAIL -eq 1 ]]; then
    whiptail --backtitle "$WT_BACKTITLE" --title "Jetzt deinstallieren?" --defaultno \
      --yesno "$(summary)

Deinstallation jetzt starten?" 20 74 >/dev/tty 2>&1 </dev/tty \
      || { echo "Abgebrochen — nichts wurde verändert." > /dev/tty; exit 0; }
  else
    echo > /dev/tty
    summary > /dev/tty
    echo > /dev/tty
    ask_yn __CONFIRM "Deinstallation jetzt starten?" 0
    if [[ "$__CONFIRM" -ne 1 ]]; then
      echo "Abgebrochen — nichts wurde verändert." > /dev/tty
      exit 0
    fi
  fi
fi

# --- Get out of the way we are about to delete ------------------------------
# When this script runs from inside $PREFIX (the installed copy, or the checkout
# under $PREFIX/src) the cleanup below would pull the file out from under the
# running bash. Copy ourselves — plus the HTTPS teardown helper we call — to a
# temp dir and re-exec from there, handing the answers over as env vars.
# readlink -f, not $(cd …): invoked as /usr/local/bin/offgridcloud-uninstall we
# must see the symlink TARGET under $PREFIX — that's the file about to vanish.
SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || true)"
[[ -f "$SELF" ]] || SELF=""
HERE="${SELF%/*}"
if [[ -n "$SELF" && "$SELF" == "$PREFIX/"* && "${OGC_UNINSTALL_RELOCATED:-0}" -ne 1 ]]; then
  RELOC="$(mktemp -d)"
  cp "$SELF" "$RELOC/uninstall.sh"
  if [[ -f "$HERE/https/uninstall.sh" ]]; then
    mkdir -p "$RELOC/https"
    cp "$HERE/https/uninstall.sh" "$RELOC/https/uninstall.sh"
  fi
  chmod +x "$RELOC/uninstall.sh"
  export OGC_UNINSTALL_RELOCATED=1 OGC_NONINTERACTIVE=1 \
         OGC_PREFIX="$PREFIX" OGC_SERVICE_USER="$SERVICE_USER" \
         OGC_UNINSTALL_PURGE="$PURGE" OGC_UNINSTALL_WIFI="$REMOVE_WIFI" \
         OGC_UNINSTALL_USER="$REMOVE_USER" OGC_UNINSTALL_PACKAGES="$REMOVE_PACKAGES" \
         OGC_UNINSTALL_BACKUP="$DO_BACKUP" OGC_UNINSTALL_DRY_RUN="$DRY_RUN" \
         OGC_UNINSTALL_BACKUP_DIR="$BACKUP_DIR" OGC_UNINSTALL_RELOC_DIR="$RELOC"
  exec bash "$RELOC/uninstall.sh"
fi

# Running as the relocated copy: tidy up the temp dir when we are done, so a
# clean uninstall doesn't leave a stray script behind.
if [[ -n "${OGC_UNINSTALL_RELOC_DIR:-}" && -d "${OGC_UNINSTALL_RELOC_DIR}" ]]; then
  trap 'rm -rf "$OGC_UNINSTALL_RELOC_DIR"' EXIT
fi

[[ $DRY_RUN -eq 1 ]] && printf '\n\033[1;33mTrockenlauf — es wird nichts verändert.\033[0m\n'

# --- 0. Safety backup (DB + .env) -------------------------------------------
if [[ $PURGE -eq 1 && $DO_BACKUP -eq 1 && $HAS_DB -eq 1 ]]; then
  step "Backing up the database + .env before purging..."
  # Self-contained (deploy/backup.sh may not be around when curl'd): the .env
  # holds OGC_SECRET_KEY, without which the DB's provider credentials are lost.
  ARCHIVE="$BACKUP_DIR/offgridcloud-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "   [dry-run] tar -czf $ARCHIVE offgridcloud.db .env"
  else
    _tmp="$(mktemp -d)"
    cp "$PREFIX/data/offgridcloud.db" "$_tmp/" 2>/dev/null || true
    [[ -f "$PREFIX/.env" ]] && cp "$PREFIX/.env" "$_tmp/.env"
    mkdir -p "$BACKUP_DIR"
    if tar -czf "$ARCHIVE" -C "$_tmp" . 2>/dev/null; then
      chmod 600 "$ARCHIVE"
      echo "   Backup written to $ARCHIVE"
    else
      echo "   Could not write the backup — aborting so nothing is lost." >&2
      rm -rf "$_tmp"; exit 1
    fi
    rm -rf "$_tmp"
  fi
fi

# --- 1. A running VPN tunnel ------------------------------------------------
# The app drives WireGuard via `ip`/`wg` (interface ogc-wg) and OpenVPN via a
# pid file — both outlive the service, so take them down before it goes away.
step "Taking down any active VPN tunnel..."
OVPN_PID_FILE="$PREFIX/data/vpn/ogc.pid"
if [[ -f "$OVPN_PID_FILE" ]]; then
  _pid="$(cat "$OVPN_PID_FILE" 2>/dev/null || true)"
  if [[ "$_pid" =~ ^[0-9]+$ ]] && kill -0 "$_pid" 2>/dev/null; then
    run kill -TERM "$_pid" 2>/dev/null || true
    echo "   Stopped the OpenVPN process (pid $_pid)."
  fi
fi
if command -v ip >/dev/null 2>&1 && ip link show ogc-wg >/dev/null 2>&1; then
  run ip link del dev ogc-wg 2>/dev/null || true
  echo "   Removed the WireGuard interface ogc-wg."
fi

# --- 2. Service, drop-ins, sudoers ------------------------------------------
step "Stopping and disabling the service..."
run systemctl disable --now offgridcloud 2>/dev/null || true
run rm -f /etc/systemd/system/offgridcloud.service
# VPN feature: CAP_NET_ADMIN drop-in dir + tun module autoload.
run rm -rf /etc/systemd/system/offgridcloud.service.d
run rm -f /etc/modules-load.d/offgridcloud-tun.conf
# All NOPASSWD rules the installer (and its features) created.
run rm -f /etc/sudoers.d/offgridcloud \
          /etc/sudoers.d/offgridcloud-power \
          /etc/sudoers.d/offgridcloud-https \
          /etc/sudoers.d/offgridcloud-network
run systemctl daemon-reload 2>/dev/null || true

# --- 3. Network-redundancy layer --------------------------------------------
step "Removing the network-redundancy layer (watchdog + fallback AP), if installed..."
run systemctl disable --now offgridcloud-netwatch.service 2>/dev/null || true
run rm -f /etc/systemd/system/offgridcloud-netwatch.service
run rm -rf /run/offgridcloud
if command -v nmcli >/dev/null 2>&1; then
  # The fallback AP is ours alone — take it down and delete the profile. Its
  # name is configurable; read it from network.json, fall back to the default.
  AP_NAME="offgridcloud-ap"
  if [[ -f "$PREFIX/data/network.json" ]]; then
    _v="$(sed -n 's/.*"ap_connection_name"[: ]*"\([^"]*\)".*/\1/p' "$PREFIX/data/network.json" | head -1)"
    [[ -n "$_v" ]] && AP_NAME="$_v"
  fi
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "   [dry-run] nmcli connection delete $AP_NAME"
  else
    nmcli connection down "$AP_NAME" >/dev/null 2>&1 || true
    nmcli connection delete "$AP_NAME" >/dev/null 2>&1 \
      && echo "   Removed fallback AP profile: $AP_NAME" || true
  fi
  # Client Wi-Fi profiles (ogc-wifi-*) keep the box online — removing them can
  # drop the very connection you are working over, so it is opt-in.
  if [[ $REMOVE_WIFI -eq 1 ]]; then
    while IFS= read -r _con; do
      [[ -n "$_con" ]] || continue
      if [[ $DRY_RUN -eq 1 ]]; then
        echo "   [dry-run] nmcli connection delete $_con"
      else
        nmcli connection delete "$_con" >/dev/null 2>&1 \
          && echo "   Removed Wi-Fi profile: $_con" || true
      fi
    done < <(nmcli -t -f NAME connection show 2>/dev/null | grep '^ogc-wifi-' || true)
  elif [[ "${WIFI_COUNT:-0}" -gt 0 ]]; then
    echo "   Kept $WIFI_COUNT Wi-Fi profile(s) (ogc-wifi-*) so the box stays online."
  fi
fi

# --- 4. HTTPS reverse proxy --------------------------------------------------
step "Removing the HTTPS reverse proxy (Caddy config + hostname), if installed..."
HTTPS_UNINSTALL="$HERE/https/uninstall.sh"
if [[ -f "$HTTPS_UNINSTALL" && $DRY_RUN -eq 0 ]]; then
  # The dedicated teardown script ships next to this one (also used by
  # install.sh when HTTPS is deselected on a re-run).
  bash "$HTTPS_UNINSTALL" --prefix "$PREFIX" || true
else
  # Same steps inline, so a standalone run (curl'd, no repo layout) and the
  # dry-run preview tear HTTPS down just as completely.
  if [[ $HAS_HTTPS -eq 1 ]]; then
    run rm -f /etc/caddy/Caddyfile
    run systemctl disable --now caddy 2>/dev/null || true
    echo "   Removed the managed Caddyfile and disabled caddy."
  fi
  run rm -f /etc/sudoers.d/offgridcloud-https
  if [[ -f "$PREFIX/.env" && $DRY_RUN -eq 0 ]]; then
    sed -i '/^OGC_HTTPS_APPLY_COMMAND=/d' "$PREFIX/.env" 2>/dev/null || true
  fi
  HOST_STATE="$PREFIX/data/https-hostname.state"
  if [[ -f "$HOST_STATE" ]]; then
    PREV_HOSTNAME=""
    # shellcheck disable=SC1090
    . "$HOST_STATE" 2>/dev/null || true
    if [[ -n "$PREV_HOSTNAME" ]]; then
      run hostnamectl set-hostname "$PREV_HOSTNAME" 2>/dev/null && ran \
        && echo "   Restored hostname: $PREV_HOSTNAME"
      if [[ $DRY_RUN -eq 0 ]] && grep -qE '^\s*127\.0\.1\.1' /etc/hosts; then
        sed -i -E "s/^\s*127\.0\.1\.1.*/127.0.1.1\t${PREV_HOSTNAME}/" /etc/hosts
      fi
      run systemctl restart avahi-daemon 2>/dev/null || true
    fi
    run rm -f "$HOST_STATE"
  fi
  run rm -f "$PREFIX/data/https_state.json"
fi

# --- 5. On-box console (kiosk) ----------------------------------------------
step "Removing the on-box console (kiosk), if installed..."
run systemctl disable --now offgrid-kiosk.service 2>/dev/null || true
run rm -f /etc/systemd/system/offgrid-kiosk.service
run rm -f /usr/local/bin/offgrid-console
run rm -f /etc/profile.d/offgrid-console.sh
# Hand tty1 back to a normal login prompt.
run systemctl unmask getty@tty1.service 2>/dev/null || true
# Restore the boot behaviour the kiosk installer changed (desktop vs. console).
STATE_FILE="$PREFIX/data/kiosk-boot.state"
if [[ -f "$STATE_FILE" ]]; then
  # shellcheck disable=SC1090
  PREV_TARGET=""; DISABLED_DMS=""
  . "$STATE_FILE" 2>/dev/null || true
  if [[ -n "$PREV_TARGET" && "$PREV_TARGET" != "unknown" ]]; then
    run systemctl set-default "$PREV_TARGET" 2>/dev/null && ran \
      && echo "   Restored default boot target: $PREV_TARGET"
  fi
  for dm in $DISABLED_DMS; do
    run systemctl enable "$dm.service" 2>/dev/null && ran \
      && echo "   Re-enabled display manager: $dm"
  done
  run rm -f "$STATE_FILE"
fi
run systemctl daemon-reload 2>/dev/null || true
run systemctl start getty@tty1.service 2>/dev/null || true

# --- 6. Application files ----------------------------------------------------
# The convenience command installed by install.sh — only ours if it points into
# the prefix we are removing.
if [[ -L /usr/local/bin/offgridcloud-uninstall ]]; then
  _target="$(readlink -f /usr/local/bin/offgridcloud-uninstall 2>/dev/null || true)"
  [[ "$_target" == "$PREFIX/"* ]] && run rm -f /usr/local/bin/offgridcloud-uninstall
fi

if [[ $PURGE -eq 1 ]]; then
  step "Purging everything under $PREFIX (including data and .env)..."
  run rm -rf "$PREFIX"
else
  step "Removing app files but KEEPING $PREFIX/data and $PREFIX/.env..."
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "   [dry-run] rm -rf $PREFIX/* (except data/ and .env)"
  else
    find "$PREFIX" -mindepth 1 -maxdepth 1 \
      ! -name data ! -name .env -exec rm -rf {} + 2>/dev/null || true
  fi
  echo "   Kept: $PREFIX/data and $PREFIX/.env"
fi

# --- 7. Service user ---------------------------------------------------------
if [[ $REMOVE_USER -eq 1 && $HAS_USER -eq 1 ]]; then
  step "Removing service user '$SERVICE_USER' (incl. home + rclone config)..."
  run userdel -r "$SERVICE_USER" 2>/dev/null || run userdel "$SERVICE_USER" 2>/dev/null || true
fi

# --- 8. Packages (opt-in) ----------------------------------------------------
# Deliberately narrow: NetworkManager, avahi, dnsmasq, X/Chromium, Node and
# Python stay — the box (or its desktop image) may well depend on them, and a
# broken network on a remote box is a far worse outcome than a leftover package.
KEPT_NOTE="caddy, avahi-daemon, NetworkManager, rclone, ffmpeg, /usr/local/bin/speedtest"
if [[ $REMOVE_PACKAGES -eq 1 ]]; then
  step "Removing packages that were installed for OffgridCloud..."
  PKGS=(caddy rclone ffmpeg wireguard-tools openvpn)
  if command -v apt-get >/dev/null 2>&1; then
    TO_REMOVE=()
    for p in "${PKGS[@]}"; do
      dpkg -s "$p" >/dev/null 2>&1 && TO_REMOVE+=("$p")
    done
    if [[ ${#TO_REMOVE[@]} -gt 0 ]]; then
      run apt-get purge -y "${TO_REMOVE[@]}" || true
      run apt-get autoremove -y --purge || true
      if ran; then echo "   Removed: ${TO_REMOVE[*]}"; fi
    else
      echo "   None of the optional packages are installed."
    fi
    # The Caddy apt repo deploy/https/install.sh added.
    run rm -f /etc/apt/sources.list.d/caddy-stable.list \
              /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  elif command -v dnf >/dev/null 2>&1; then
    run dnf remove -y "${PKGS[@]}" || true
  elif command -v pacman >/dev/null 2>&1; then
    run pacman -Rns --noconfirm "${PKGS[@]}" || true
  else
    echo "   No supported package manager — remove them by hand: ${PKGS[*]}" >&2
  fi
  # speedtest was dropped in as a plain binary by install.sh, not as a package.
  [[ -f /usr/local/bin/speedtest ]] && run rm -f /usr/local/bin/speedtest
  KEPT_NOTE="avahi-daemon, NetworkManager, Node.js, Python (system tools)"
fi

# --- Summary -----------------------------------------------------------------
if [[ $DRY_RUN -eq 1 ]]; then
  printf '\n\033[1;33mTrockenlauf beendet — es wurde nichts verändert.\033[0m\n'
  echo "Ohne --dry-run (bzw. ohne den Trockenlauf-Haken) läuft genau das oben ab."
  exit 0
fi

printf '\n\033[1;32mFertig.\033[0m OffgridCloud wurde entfernt.\n\n'
if [[ $PURGE -ne 1 ]]; then
  echo "Behalten: $PREFIX/data (Datenbank + Medien-Puffer) und $PREFIX/.env"
  echo "  Endgültig löschen:  sudo rm -rf $PREFIX"
fi
if [[ $REMOVE_USER -ne 1 && $HAS_USER -eq 1 ]]; then
  echo "Behalten: Dienst-Benutzer '$SERVICE_USER' (löschen: sudo userdel -r $SERVICE_USER)"
fi
echo "Behalten: $KEPT_NOTE"
if [[ $HAS_KIOSK -eq 1 ]]; then
  echo
  echo "Die Kiosk-Konsole gibt tty1 erst nach einem Neustart ganz frei: sudo reboot"
fi
