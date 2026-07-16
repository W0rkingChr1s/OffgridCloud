#!/usr/bin/env bash
# OffgridCloud one-line installer.
#
# Fetches the repository and runs the native installer — turns a fresh
# Debian/Raspberry Pi OS (or Fedora/Arch) box into a running OffgridCloud with
# a single command:
#
#   sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/W0rkingChr1s/OffgridCloud/main/deploy/bootstrap.sh)"
#
# Use THIS form (command substitution), not `... | sudo bash`: the native
# installer is interactive, and piping the script into sudo makes the script the
# process's stdin instead of your keyboard. Modern sudo runs the command in its
# own pseudo-terminal and then does not forward keystrokes, so the menu never
# reacts. `bash -c "$(curl ...)"` keeps your terminal as stdin. (Download-then-
# run works too:  curl -fsSL .../bootstrap.sh -o ogc.sh && sudo bash ogc.sh)
#
# To run it UNATTENDED no keyboard is needed, so the pipe form is fine — pass
# OGC_* variables through sudo:
#
#   curl -fsSL .../bootstrap.sh | sudo OGC_NONINTERACTIVE=1 OGC_WITH_KIOSK=1 bash
#
# Overridable via environment:
#   OGC_REPO    git URL   (default: https://github.com/W0rkingChr1s/OffgridCloud.git)
#   OGC_BRANCH  branch    (default: main)
#   OGC_SRC     checkout  (default: /opt/offgridcloud/src)
#   OGC_*       any installer variable (see deploy/install.sh) — forwarded as-is
set -euo pipefail

OGC_REPO="${OGC_REPO:-https://github.com/W0rkingChr1s/OffgridCloud.git}"
OGC_BRANCH="${OGC_BRANCH:-main}"
OGC_SRC="${OGC_SRC:-/opt/offgridcloud/src}"

step() { printf '\n\033[1;36m>> %s\033[0m\n' "$1"; }

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root, e.g.  sudo bash -c \"\$(curl -fsSL <url>)\"" >&2
  exit 1
fi

# --- Base tools (git, curl) -------------------------------------------------
step "Installing base tools (git, curl)..."
if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  apt-get install -y --no-install-recommends git curl ca-certificates
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y git curl
elif command -v pacman >/dev/null 2>&1; then
  pacman -Sy --noconfirm git curl
else
  echo "No supported package manager (apt/dnf/pacman) found." >&2
  exit 1
fi

# --- Node.js >= 18 (needed to build the frontend) ---------------------------
need_node=1
if command -v node >/dev/null 2>&1; then
  major="$(node -v | sed 's/^v\([0-9]*\).*/\1/')"
  [[ "${major:-0}" -ge 18 ]] && need_node=0
fi
if [[ $need_node -eq 1 ]]; then
  step "Installing Node.js 22..."
  if command -v apt-get >/dev/null 2>&1; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    apt-get install -y nodejs
  elif command -v dnf >/dev/null 2>&1; then
    dnf module install -y nodejs:22 || dnf install -y nodejs
  elif command -v pacman >/dev/null 2>&1; then
    pacman -Sy --noconfirm nodejs npm
  fi
else
  echo "   Node.js $(node -v) already present."
fi

# --- Fetch the source -------------------------------------------------------
if [[ -d "$OGC_SRC/.git" ]]; then
  step "Updating existing checkout in $OGC_SRC..."
  # The tree may be owned by the service user; allow root's git to use it.
  git config --global --add safe.directory "$OGC_SRC" 2>/dev/null || true
  # Fetch full history + tags so the installer can derive the version from the
  # git tag (git describe needs tags and real history, not a --depth 1 clone).
  git -C "$OGC_SRC" fetch --tags --prune --unshallow origin 2>/dev/null \
    || git -C "$OGC_SRC" fetch --tags --prune origin
  git -C "$OGC_SRC" checkout -B "$OGC_BRANCH" "origin/$OGC_BRANCH"
else
  step "Cloning $OGC_REPO ($OGC_BRANCH) into $OGC_SRC..."
  mkdir -p "$(dirname "$OGC_SRC")"
  git clone --branch "$OGC_BRANCH" "$OGC_REPO" "$OGC_SRC"
fi

# --- Run the installer ------------------------------------------------------
# It asks its questions on the controlling terminal. Reattach stdin to /dev/tty
# explicitly so the keyboard is fd 0 for the installer even though this bootstrap
# arrived over a pipe (harmless when there is no tty — the installer then detects
# a non-interactive run and uses the OGC_* / default answers). Env vars set on the
# invoking line above are inherited here and become the answers.
step "Running the native installer..."
chmod +x "$OGC_SRC/deploy/install.sh"
if [[ -e /dev/tty ]]; then
  exec bash "$OGC_SRC/deploy/install.sh" </dev/tty
else
  exec bash "$OGC_SRC/deploy/install.sh"
fi
