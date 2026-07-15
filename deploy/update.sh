#!/usr/bin/env bash
# Update a native OffgridCloud install to the latest GitHub release.
#
# Fetches the newest release tag (falls back to the main branch), checks it out
# in the source tree, rebuilds via the installer, and restarts the service —
# preserving the existing data and .env (and the configured port).
#
# Usage:
#   sudo ./deploy/update.sh [options]
#
# Options:
#   --check          Only report current vs. latest version, then exit.
#   --channel C      "release" (default, newest tag) or "main" (bleeding edge).
#   --prefix DIR     Install location (default: /opt/offgridcloud).
#   --repo OWNER/REPO  GitHub repo to check (default: W0rkingChr1s/OffgridCloud).
#   -h, --help       Show this help.
set -euo pipefail

PREFIX="/opt/offgridcloud"
REPO="W0rkingChr1s/OffgridCloud"
CHANNEL="release"
CHECK=0

usage() { sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK=1; shift ;;
    --channel) CHANNEL="${2:?}"; shift 2 ;;
    --prefix) PREFIX="${2:?}"; shift 2 ;;
    --repo) REPO="${2:?}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

SRC="$PREFIX/src"
step() { printf '\n\033[1;36m>> %s\033[0m\n' "$1"; }

if [[ $EUID -ne 0 ]]; then echo "Please run as root (sudo)." >&2; exit 1; fi
if [[ ! -d "$SRC/.git" ]]; then
  echo "No git checkout at $SRC. Re-run the one-line installer to update:" >&2
  echo "  curl -fsSL https://raw.githubusercontent.com/$REPO/main/deploy/bootstrap.sh | sudo bash" >&2
  exit 1
fi

current_version() {
  # The deployed version is the stamp the app actually reads.
  local v=""
  [[ -f "$PREFIX/backend/app/VERSION" ]] && v="$(tr -d '[:space:]' < "$PREFIX/backend/app/VERSION")"
  echo "${v:-unknown}"
}

latest_tag() {
  curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
    | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1
}

# The source tree is owned by the service user, but we run git as root here —
# tell git that's fine (avoids "detected dubious ownership"). Idempotent.
git config --global --add safe.directory "$SRC" 2>/dev/null || true

step "Fetching updates from origin..."
# Unshallow if the source was originally cloned with --depth 1, so `git describe`
# can find tags for the version stamp; fall back to a plain fetch otherwise.
git -C "$SRC" fetch --tags --prune --unshallow origin 2>/dev/null \
  || git -C "$SRC" fetch --tags --prune origin

CURRENT="$(current_version)"
if [[ "$CHANNEL" == "main" ]]; then
  TARGET="origin/main"
  TARGET_LABEL="main (latest)"
else
  TAG="$(latest_tag || true)"
  if [[ -z "${TAG:-}" ]]; then
    echo "   No GitHub release found — falling back to the main branch."
    TARGET="origin/main"; TARGET_LABEL="main (no release yet)"
  else
    TARGET="$TAG"; TARGET_LABEL="$TAG"
  fi
fi

echo "   Installed: $CURRENT"
echo "   Target:    $TARGET_LABEL"

if [[ $CHECK -eq 1 ]]; then
  exit 0
fi

# Preserve the port from the current systemd unit (install.sh defaults to 8000).
PORT=8000
if [[ -f /etc/systemd/system/offgridcloud.service ]]; then
  PORT="$(sed -n 's/.*--port \([0-9]*\).*/\1/p' /etc/systemd/system/offgridcloud.service | head -1)"
  PORT="${PORT:-8000}"
fi

step "Checking out $TARGET_LABEL..."
git -C "$SRC" checkout -f "$TARGET"

# Stamp the exact target release, so the version is right even when several tags
# point at the same commit (git describe would otherwise pick one arbitrarily).
# For the main channel, leave it unset and let install.sh derive it via describe.
STAMP=""
if [[ "$CHANNEL" != "main" && -n "${TAG:-}" ]]; then
  STAMP="$(printf '%s' "$TAG" | sed -E 's/^v\.?//')"
fi

step "Rebuilding and reinstalling (keeps data & .env)..."
chmod +x "$SRC/deploy/install.sh"
# Headless: no questions, keep the existing prefix/port, refresh the unit but
# leave starting to us (below). Optional features stay as they were — their
# separate units/sudoers are untouched by a core reinstall.
OGC_STAMP_VERSION="$STAMP" OGC_NONINTERACTIVE=1 \
  OGC_PREFIX="$PREFIX" OGC_PORT="$PORT" OGC_START=0 OGC_INSTALL_SERVICE=1 \
  bash "$SRC/deploy/install.sh"

step "Restarting the service..."
systemctl restart offgridcloud
printf "   Waiting for http://127.0.0.1:%s/api/health " "$PORT"
for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    printf "\n   \033[1;32mUpdated to %s and healthy.\033[0m\n" "$(current_version)"
    exit 0
  fi
  printf "."; sleep 1
done
printf "\n   \033[1;31mService did not answer — check: journalctl -u offgridcloud -e\033[0m\n"
exit 1
