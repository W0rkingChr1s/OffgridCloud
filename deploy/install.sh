#!/usr/bin/env bash
# Native install for OffgridCloud — recommended for Raspberry Pi 3 (no Docker).
#
# One command sets everything up: builds the frontend, creates a Python venv,
# installs rclone (and optionally ffmpeg for video thumbnails), copies the app
# to /opt/offgridcloud, writes a .env with a random secret AND a random admin
# password, and registers a systemd service.
#
# The installer is INTERACTIVE: run it and answer a short list of questions
# (press Enter to accept the sensible default in brackets). No flags to memorise.
#
# Usage:
#   sudo ./deploy/install.sh
#
# Automation / headless installs: there are no config flags — set OGC_* env vars
# instead and it runs unattended (also happens automatically when there is no
# terminal, e.g. during a self-update). Recognised variables and their defaults:
#
#   OGC_PREFIX=/opt/offgridcloud     OGC_PORT=8000
#   OGC_ADMIN_EMAIL=admin@offgrid.local
#   OGC_WITH_FFMPEG=0                OGC_WITH_SPEEDTEST=1
#   OGC_WITH_AP_FALLBACK=0           OGC_WITH_VPN=0
#   OGC_WITH_KIOSK=0                 OGC_WITH_CHROMIUM_KIOSK=0   OGC_KIOSK_PIN=
#   OGC_INSTALL_SERVICE=1            OGC_START=1
#   OGC_NONINTERACTIVE=1             # force unattended even with a terminal
#
# Only -h/--help is a flag; any other argument is ignored (older flag-style
# commands still run, they just fall through to the questions / env defaults).
set -euo pipefail

# --- Defaults ---------------------------------------------------------------
PREFIX="${OGC_PREFIX:-/opt/offgridcloud}"
SERVICE_USER="${OGC_SERVICE_USER:-offgrid}"
SPEEDTEST_VER="1.2.0"
NONINTERACTIVE="${OGC_NONINTERACTIVE:-0}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Detect what's already installed so a plain re-run pre-selects it: re-running
# the installer IS the update path — Enter through the questions to update every
# feature you already have (no flags, no env vars). Explicit OGC_* still wins.
_svc_unit="/etc/systemd/system/offgridcloud.service"
_has_service=0;  [[ -f "$_svc_unit" ]] && _has_service=1
_has_kiosk=0;    [[ -f /etc/systemd/system/offgrid-kiosk.service ]] && _has_kiosk=1
_has_ap=0;       [[ -f /etc/systemd/system/offgridcloud-netwatch.service ]] && _has_ap=1
_has_vpn=0;      [[ -f /etc/systemd/system/offgridcloud.service.d/10-vpn-caps.conf ]] && _has_vpn=1
_has_ffmpeg=0;   command -v ffmpeg >/dev/null 2>&1 && _has_ffmpeg=1
_has_speed=1;    # installed by default; keep on unless the operator opts out
_has_chrome=0;   { command -v chromium-browser || command -v chromium; } >/dev/null 2>&1 \
                 && [[ $_has_kiosk -eq 1 ]] && _has_chrome=1
# Carry the existing admin e-mail + port so a re-run doesn't silently change them.
_det_email="admin@offgrid.local"
[[ -f "$PREFIX/.env" ]] && { _v="$(sed -n 's/^OGC_INITIAL_ADMIN_EMAIL=//p' "$PREFIX/.env" | head -1)"; [[ -n "$_v" ]] && _det_email="$_v"; }
_det_port="8000"
[[ -f "$_svc_unit" ]] && { _v="$(sed -n 's/.*--port \([0-9]*\).*/\1/p' "$_svc_unit" | head -1)"; [[ -n "$_v" ]] && _det_port="$_v"; }
EXISTING_INSTALL=$_has_service

# Values (env-overridable; the detected state is the interactive default) ------
ADMIN_EMAIL="${OGC_ADMIN_EMAIL:-$_det_email}"
PORT="${OGC_PORT:-$_det_port}"
DO_START="${OGC_START:-1}"
INSTALL_SERVICE="${OGC_INSTALL_SERVICE:-1}"
WITH_FFMPEG="${OGC_WITH_FFMPEG:-$_has_ffmpeg}"
WITH_AP_FALLBACK="${OGC_WITH_AP_FALLBACK:-$_has_ap}"
WITH_VPN="${OGC_WITH_VPN:-$_has_vpn}"
WITH_KIOSK="${OGC_WITH_KIOSK:-$_has_kiosk}"
WITH_CHROMIUM_KIOSK="${OGC_WITH_CHROMIUM_KIOSK:-$_has_chrome}"
KIOSK_PIN="${OGC_KIOSK_PIN:-}"
WITH_SPEEDTEST="${OGC_WITH_SPEEDTEST:-$_has_speed}"

usage() { sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

# Only --help remains a flag. Everything else is asked below (or comes from the
# OGC_* env vars). Legacy flags are accepted-and-ignored so old commands and the
# piped one-liner (`bash -s -- --start`) don't break.
for arg in "$@"; do
  case "$arg" in
    -h|--help) usage; exit 0 ;;
    --non-interactive|--defaults|--yes|-y) NONINTERACTIVE=1 ;;
    *) : ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

# --- Interactive questionnaire ----------------------------------------------
# The one-line installer runs as `curl ... | sudo bash`, so stdin is the piped
# script, not the keyboard — prompts therefore talk to the controlling terminal
# (/dev/tty). With no terminal (headless self-update, CI) we skip the questions
# and use the defaults / OGC_* values from above.
if [[ $NONINTERACTIVE -eq 0 ]] && { [[ ! -r /dev/tty ]] || [[ ! -w /dev/tty ]]; }; then
  NONINTERACTIVE=1
fi

ask() {  # ask VAR "Question" "default"
  local __var="$1" __q="$2" __def="$3" __ans=""
  if [[ $NONINTERACTIVE -eq 1 ]]; then printf -v "$__var" '%s' "$__def"; return; fi
  printf '\033[1m%s\033[0m [\033[36m%s\033[0m]: ' "$__q" "$__def" > /dev/tty
  IFS= read -r __ans < /dev/tty || __ans=""
  printf -v "$__var" '%s' "${__ans:-$__def}"
}

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

ask_secret() {  # ask_secret VAR "Question"
  local __var="$1" __q="$2" __ans=""
  if [[ $NONINTERACTIVE -eq 1 ]]; then printf -v "$__var" '%s' ""; return; fi
  printf '\033[1m%s\033[0m: ' "$__q" > /dev/tty
  IFS= read -rs __ans < /dev/tty || __ans=""
  printf '\n' > /dev/tty
  printf -v "$__var" '%s' "$__ans"
}

if [[ $NONINTERACTIVE -eq 0 ]]; then
  cat > /dev/tty <<'BANNER'

  ┌────────────────────────────────────────────────┐
  │   OffgridCloud — Installation                   │
  │   Ein paar Fragen, dann läuft der Rest allein.  │
  │   [Wert] = Vorgabe, einfach Enter drücken.      │
  └────────────────────────────────────────────────┘
BANNER
  if [[ $EXISTING_INSTALL -eq 1 ]]; then
    printf '  \033[1;32mBestehende Installation erkannt — dies ist ein Update.\033[0m\n' > /dev/tty
    printf '  Die Vorgaben spiegeln, was schon da ist. Einfach mit Enter durch\n' > /dev/tty
    printf '  bestätigen, dann werden App und alle aktiven Funktionen aktualisiert.\n' > /dev/tty
  fi
fi

ask        PREFIX              "Installationsverzeichnis"                                   "$PREFIX"
ask        ADMIN_EMAIL         "Admin-E-Mail (Login)"                                       "$ADMIN_EMAIL"
ask        PORT                "Port für die Weboberfläche"                                 "$PORT"
ask_yn     WITH_FFMPEG         "Video-Thumbnails aktivieren? (installiert ffmpeg)"          "$WITH_FFMPEG"
ask_yn     WITH_SPEEDTEST      "Ookla-Speedtest-CLI für genauere Bandbreitenmessung?"       "$WITH_SPEEDTEST"
ask_yn     WITH_AP_FALLBACK    "Netzwerk-Redundanz: eigenes WLAN, wenn der Uplink ausfällt?" "$WITH_AP_FALLBACK"
ask_yn     WITH_VPN            "VPN-Client (WireGuard/OpenVPN) einrichten?"                 "$WITH_VPN"
ask_yn     WITH_KIOSK          "OffgridCloud-OS-Menü am Bildschirm der Box (Kiosk)?"        "$WITH_KIOSK"
if [[ "$WITH_KIOSK" -eq 1 ]]; then
  ask_yn   WITH_CHROMIUM_KIOSK "  … zusätzlich Vollbild-Browser (Chromium, eher Pi 4/5)?"   "$WITH_CHROMIUM_KIOSK"
  if [[ -z "$KIOSK_PIN" ]]; then
    ask_secret KIOSK_PIN       "  … Admin-PIN für den Shell-Zugang (leer = zufällig)"
  fi
else
  WITH_CHROMIUM_KIOSK=0
fi
ask_yn     INSTALL_SERVICE     "systemd-Dienst installieren?"                               "$INSTALL_SERVICE"
if [[ "$INSTALL_SERVICE" -eq 1 ]]; then
  ask_yn   DO_START            "Dienst am Ende aktivieren und starten?"                     "$DO_START"
else
  DO_START=0
fi

if [[ $NONINTERACTIVE -eq 0 ]]; then
  yesno() { [[ "$1" -eq 1 ]] && echo "ja" || echo "nein"; }
  cat > /dev/tty <<EOF

  Zusammenfassung:
    Verzeichnis .......... $PREFIX
    Admin-E-Mail ......... $ADMIN_EMAIL
    Port ................. $PORT
    Video-Thumbnails ..... $(yesno "$WITH_FFMPEG")
    Speedtest-CLI ........ $(yesno "$WITH_SPEEDTEST")
    Netzwerk-Redundanz ... $(yesno "$WITH_AP_FALLBACK")
    VPN-Client ........... $(yesno "$WITH_VPN")
    Kiosk-Menü ........... $(yesno "$WITH_KIOSK")$([[ "$WITH_KIOSK" -eq 1 && "$WITH_CHROMIUM_KIOSK" -eq 1 ]] && echo " (+ Chromium)")
    Dienst installieren .. $(yesno "$INSTALL_SERVICE")
    Jetzt starten ........ $(yesno "$DO_START")
EOF
  ask_yn __CONFIRM "Installation jetzt starten?" 1
  if [[ "$__CONFIRM" -ne 1 ]]; then
    echo "Abgebrochen — nichts wurde verändert." > /dev/tty
    exit 0
  fi
fi

step() { printf '\n\033[1;36m>> %s\033[0m\n' "$1"; }

# --- System dependencies ----------------------------------------------------
step "Installing system dependencies..."
if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  apt-get install -y --no-install-recommends python3-venv curl ca-certificates
  [[ $WITH_FFMPEG -eq 1 ]] && apt-get install -y --no-install-recommends ffmpeg
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y python3-virtualenv curl || dnf install -y python3 curl
  [[ $WITH_FFMPEG -eq 1 ]] && dnf install -y ffmpeg
elif command -v pacman >/dev/null 2>&1; then
  pacman -Sy --noconfirm python curl
  [[ $WITH_FFMPEG -eq 1 ]] && pacman -Sy --noconfirm ffmpeg
else
  echo "No supported package manager found. Ensure python3-venv (and curl) are installed." >&2
fi

# rclone — the distro package is often years out of date, so prefer the
# official installer and fall back to the package manager if offline.
if command -v rclone >/dev/null 2>&1; then
  echo "   rclone already present: $(rclone version 2>/dev/null | head -1)"
else
  step "Installing rclone (official installer)..."
  if ! curl -fsSL https://rclone.org/install.sh | bash; then
    echo "   Official installer failed; trying the distro package..."
    if command -v apt-get >/dev/null 2>&1; then apt-get install -y rclone
    elif command -v dnf >/dev/null 2>&1; then dnf install -y rclone
    elif command -v pacman >/dev/null 2>&1; then pacman -Sy --noconfirm rclone
    else echo "   Could not install rclone — install it manually before first use." >&2
    fi
  fi
fi

# Ookla Speedtest CLI — powers the bandwidth "measure now" against a nearby
# server, avoiding the CDN bot-blocking that can 403 the HTTP probe. Best-effort:
# a failure here is non-fatal (the app falls back to the HTTP probe).
if [[ $WITH_SPEEDTEST -eq 1 ]]; then
  if command -v speedtest >/dev/null 2>&1; then
    echo "   speedtest already present: $(speedtest --version 2>/dev/null | head -1)"
  else
    step "Installing Ookla Speedtest CLI..."
    case "$(uname -m)" in
      x86_64|amd64)          ST_ARCH="x86_64" ;;
      aarch64|arm64)         ST_ARCH="aarch64" ;;
      armv7l|armv7)          ST_ARCH="armhf" ;;
      armv6l)                ST_ARCH="armel" ;;
      i386|i686)             ST_ARCH="i386" ;;
      *)                     ST_ARCH="" ;;
    esac
    if [[ -z "$ST_ARCH" ]]; then
      echo "   Unknown CPU architecture ($(uname -m)) — skipping speedtest; the HTTP probe still works." >&2
    else
      ST_URL="https://install.speedtest.net/app/cli/ookla-speedtest-${SPEEDTEST_VER}-linux-${ST_ARCH}.tgz"
      ST_TMP="$(mktemp -d)"
      if curl -fsSL "$ST_URL" -o "$ST_TMP/speedtest.tgz" \
         && tar -xzf "$ST_TMP/speedtest.tgz" -C "$ST_TMP" speedtest; then
        install -m 755 "$ST_TMP/speedtest" /usr/local/bin/speedtest
        echo "   speedtest installed: $(/usr/local/bin/speedtest --version 2>/dev/null | head -1)"
      else
        echo "   Could not download speedtest — skipping; the HTTP probe still works." >&2
      fi
      rm -rf "$ST_TMP"
    fi
  fi
fi

# --- Frontend build ---------------------------------------------------------
step "Building frontend (requires Node.js)..."
if ! command -v npm >/dev/null 2>&1; then
  echo "npm not found. Install Node.js, or build the frontend on another machine" >&2
  echo "and copy frontend/dist to backend/app/static before re-running." >&2
  exit 1
fi
( cd "$REPO_ROOT/frontend" && npm install && npm run build )

# --- Service user -----------------------------------------------------------
step "Creating service user '$SERVICE_USER'..."
id -u "$SERVICE_USER" >/dev/null 2>&1 || \
  useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"

# --- Copy application -------------------------------------------------------
step "Copying files to $PREFIX..."
mkdir -p "$PREFIX"
cp -r "$REPO_ROOT/backend" "$PREFIX/"
rm -rf "$PREFIX/backend/.venv" "$PREFIX/backend/app/static" \
       "$PREFIX/backend/tests" "$PREFIX/backend/.pytest_cache"
cp -r "$REPO_ROOT/frontend/dist" "$PREFIX/backend/app/static"

# Stamp the deployed version so the UI reports the real release. An explicit
# OGC_STAMP_VERSION (set by update.sh to the exact target tag) wins — this is
# unambiguous even when several tags point at the same commit. Otherwise derive
# it from the git tag of the source checkout, then a VERSION file shipped in a
# release tarball. Without any, the app uses its built-in fallback constant.
VERSION_STR="${OGC_STAMP_VERSION:-}"
if [[ -z "$VERSION_STR" ]] && git config --global --add safe.directory "$REPO_ROOT" 2>/dev/null &&
   git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  # No --always: if no tag is reachable, prefer the app's fallback constant over
  # a bare commit hash (which reads oddly as a "version" in the UI).
  VERSION_STR="$(git -C "$REPO_ROOT" describe --tags 2>/dev/null | sed -E 's/^v\.?//')"
elif [[ -z "$VERSION_STR" && -f "$REPO_ROOT/VERSION" ]]; then
  VERSION_STR="$(tr -d '[:space:]' < "$REPO_ROOT/VERSION")"
fi
if [[ -n "$VERSION_STR" ]]; then
  printf '%s\n' "$VERSION_STR" > "$PREFIX/backend/app/VERSION"
  echo "   Deployed version: $VERSION_STR"
fi

# --- Python virtualenv ------------------------------------------------------
step "Setting up Python virtualenv..."
python3 -m venv "$PREFIX/backend/.venv"
"$PREFIX/backend/.venv/bin/pip" install --upgrade pip
"$PREFIX/backend/.venv/bin/pip" install -r "$PREFIX/backend/requirements.txt"

# --- Configuration ----------------------------------------------------------
GENERATED_PASSWORD=""
if [[ ! -f "$PREFIX/.env" ]]; then
  step "Creating $PREFIX/.env with generated secret + admin password..."
  SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  GENERATED_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
  cat > "$PREFIX/.env" <<ENV
OGC_SECRET_KEY=$SECRET
OGC_INITIAL_ADMIN_EMAIL=$ADMIN_EMAIL
OGC_INITIAL_ADMIN_PASSWORD=$GENERATED_PASSWORD
OGC_ENVIRONMENT=production
OGC_DATA_DIR=$PREFIX/data
OGC_BUFFER_DIR=$PREFIX/data/buffer
OGC_RCLONE_BINARY=rclone
ENV
  chmod 600 "$PREFIX/.env"
else
  echo "   Keeping existing $PREFIX/.env"
fi

mkdir -p "$PREFIX/data/buffer"
chown -R "$SERVICE_USER:$SERVICE_USER" "$PREFIX"

# --- systemd service --------------------------------------------------------
if [[ $INSTALL_SERVICE -eq 1 ]]; then
  step "Installing systemd service (port $PORT)..."
  sed "s/--port 8000/--port $PORT/" "$REPO_ROOT/deploy/offgridcloud.service" \
    > /etc/systemd/system/offgridcloud.service
  systemctl daemon-reload
fi

# --- One-click self-update (sudoers) ----------------------------------------
# Enabled by default (see OGC_SELF_UPDATE/OGC_UPDATE_COMMAND defaults in the app).
# We just install the NOPASSWD rule so the service user may run update.sh headless.
step "Wiring up one-click updates from the web UI..."
UPDATE_SCRIPT="$PREFIX/src/deploy/update.sh"
if [[ ! -f "$UPDATE_SCRIPT" ]]; then
  echo "   Note: $UPDATE_SCRIPT not found. One-click update needs the one-line"
  echo "   installer layout ($PREFIX/src). Updates via 'sudo update.sh' still work."
fi
SUDOERS=/etc/sudoers.d/offgridcloud
echo "$SERVICE_USER ALL=(root) NOPASSWD: $UPDATE_SCRIPT" > "$SUDOERS.tmp"
if visudo -cf "$SUDOERS.tmp" >/dev/null 2>&1; then
  install -m 440 "$SUDOERS.tmp" "$SUDOERS"; rm -f "$SUDOERS.tmp"
  # Pin the exact command to this prefix (overrides the app default; matters when
  # --prefix is non-default). Append once, without touching secrets.
  grep -q '^OGC_UPDATE_COMMAND=' "$PREFIX/.env" || \
    echo "OGC_UPDATE_COMMAND=sudo $UPDATE_SCRIPT" >> "$PREFIX/.env"
  chown "$SERVICE_USER:$SERVICE_USER" "$PREFIX/.env"
  echo "   One-click update ready (button appears under System when a release is newer)."
else
  rm -f "$SUDOERS.tmp"
  echo "   Could not validate sudoers rule — skipped. Use 'sudo update.sh' instead." >&2
fi

# --- System power control (sudoers) -----------------------------------------
# Enabled by default (see OGC_*_COMMAND defaults in the app). We install the
# NOPASSWD rules so the service user may restart / reboot / power off headless.
step "Wiring up system control buttons (restart service / reboot / shutdown)..."
RESTART_CMD="/usr/bin/systemctl restart offgridcloud"
REBOOT_CMD="/usr/bin/systemctl reboot"
SHUTDOWN_CMD="/usr/bin/systemctl poweroff"
SUDOERS_PWR=/etc/sudoers.d/offgridcloud-power
{
  echo "$SERVICE_USER ALL=(root) NOPASSWD: $RESTART_CMD"
  echo "$SERVICE_USER ALL=(root) NOPASSWD: $REBOOT_CMD"
  echo "$SERVICE_USER ALL=(root) NOPASSWD: $SHUTDOWN_CMD"
} > "$SUDOERS_PWR.tmp"
if visudo -cf "$SUDOERS_PWR.tmp" >/dev/null 2>&1; then
  install -m 440 "$SUDOERS_PWR.tmp" "$SUDOERS_PWR"; rm -f "$SUDOERS_PWR.tmp"
  echo "   System control ready (buttons appear under System → System steuern)."
else
  rm -f "$SUDOERS_PWR.tmp"
  echo "   Could not validate sudoers rule — skipped system control." >&2
fi

# --- Optional: network-redundancy layer (AP fallback) ----------------------
if [[ $WITH_AP_FALLBACK -eq 1 ]]; then
  step "Installing the network-redundancy layer (AP fallback)..."
  # Copy the scripts somewhere persistent so the watchdog/sudoers paths survive
  # after the source checkout is gone, then run the installer from there.
  mkdir -p "$PREFIX/deploy"
  rm -rf "$PREFIX/deploy/netfallback"   # replace, don't nest, on a re-run/update
  cp -r "$REPO_ROOT/deploy/netfallback" "$PREFIX/deploy/netfallback"
  chmod +x "$PREFIX/deploy/netfallback/"*.sh "$PREFIX/deploy/netfallback/_apply.py" 2>/dev/null || true
  bash "$PREFIX/deploy/netfallback/install.sh" --prefix "$PREFIX" --service-user "$SERVICE_USER" \
    || echo "   AP-fallback setup reported an issue — see docs/NETZWERK-REDUNDANZ.md." >&2
fi

# --- Optional: VPN client prerequisites (native) ---------------------------
if [[ $WITH_VPN -eq 1 ]]; then
  step "Enabling the VPN client (native prerequisites)..."
  # Copy the helper somewhere persistent so it can be re-run later, then apply.
  mkdir -p "$PREFIX/deploy"
  rm -rf "$PREFIX/deploy/vpn"   # replace, don't nest, on a re-run/update
  cp -r "$REPO_ROOT/deploy/vpn" "$PREFIX/deploy/vpn"
  chmod +x "$PREFIX/deploy/vpn/install.sh" 2>/dev/null || true
  if [[ $INSTALL_SERVICE -eq 1 ]]; then
    bash "$PREFIX/deploy/vpn/install.sh" --prefix "$PREFIX" \
      || echo "   VPN setup reported an issue — see docs/VPN.md." >&2
  else
    echo "   --no-service given: VPN needs the systemd unit for the CAP_NET_ADMIN"
    echo "   drop-in. Skipped; run $PREFIX/deploy/vpn/install.sh after installing the service."
  fi
fi

# --- Optional: on-box "OffgridCloud OS" console (kiosk) --------------------
if [[ $WITH_KIOSK -eq 1 ]]; then
  step "Installing the on-box OffgridCloud OS console (kiosk on tty1)..."
  # Copy the kiosk scripts somewhere persistent so the systemd unit's path
  # survives after the source checkout is gone, then run the installer there.
  mkdir -p "$PREFIX/deploy"
  rm -rf "$PREFIX/deploy/kiosk"   # replace, don't nest, on a re-run/update
  cp -r "$REPO_ROOT/deploy/kiosk" "$PREFIX/deploy/kiosk"
  chmod +x "$PREFIX/deploy/kiosk/"*.sh "$PREFIX/deploy/kiosk/offgrid-console.py" 2>/dev/null || true
  KIOSK_ARGS=(--prefix "$PREFIX")
  [[ $WITH_CHROMIUM_KIOSK -eq 1 ]] && KIOSK_ARGS+=(--with-chromium)
  [[ -n "$KIOSK_PIN" ]] && KIOSK_ARGS+=(--pin "$KIOSK_PIN")
  bash "$PREFIX/deploy/kiosk/install.sh" "${KIOSK_ARGS[@]}" \
    || echo "   Kiosk setup reported an issue — see docs/KIOSK.md." >&2
fi

# --- Optional start + health check -----------------------------------------
if [[ $DO_START -eq 1 && $INSTALL_SERVICE -eq 1 ]]; then
  step "Starting the service..."
  systemctl enable --now offgridcloud
  printf "   Waiting for http://127.0.0.1:%s/api/health " "$PORT"
  HEALTHY=0
  for _ in $(seq 1 20); do
    if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
      HEALTHY=1; break
    fi
    printf "."; sleep 1
  done
  if [[ $HEALTHY -eq 1 ]]; then
    printf "\n   \033[1;32mService is up and healthy.\033[0m\n"
  else
    printf "\n   \033[1;31mService did not answer in time — check: journalctl -u offgridcloud -e\033[0m\n"
  fi
fi

# --- Summary ----------------------------------------------------------------
cat <<EOF

$(printf '\033[1;32mDone.\033[0m') OffgridCloud is installed in $PREFIX.

EOF

if [[ -n "$GENERATED_PASSWORD" ]]; then
  cat <<EOF
$(printf '\033[1;33m  Initial admin login (shown only once — save it now):\033[0m')
    URL:      http://<host>:$PORT
    Email:    $ADMIN_EMAIL
    Password: $GENERATED_PASSWORD

  Change the password after your first login.
EOF
else
  echo "  Login uses the credentials already in $PREFIX/.env"
fi

if [[ $DO_START -ne 1 ]]; then
  cat <<EOF

  Next steps:
    1. (Pi) Point OGC_BUFFER_DIR at your external USB SSD: sudo nano $PREFIX/.env
    2. Start the service:   sudo systemctl enable --now offgridcloud
    3. Open:                http://<host>:$PORT
EOF
fi
