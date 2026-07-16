#!/usr/bin/env bash
# Render the Caddy reverse-proxy config for OffgridCloud and (re)apply it.
#
# Always emits a LAN block for <hostname>.local with a self-signed cert
# (Caddy 'tls internal'). If --domain is given, ALSO emits a public block that
# gets a real Let's Encrypt certificate automatically. Both coexist — LAN access
# never depends on the domain (design decision B).
#
# Idempotent: run it again with the same values and nothing changes. Called by
# deploy/https/install.sh at install time and by the backend (PUT
# /api/system/https) when the operator sets/clears the domain from the UI.
#
# Usage:
#   sudo ./deploy/https/apply.sh --hostname NAME [--domain DOMAIN] [--prefix DIR]
set -euo pipefail

HOSTNAME_SHORT=""
DOMAIN=""
PREFIX="${OGC_PREFIX:-/opt/offgridcloud}"
CADDYFILE="/etc/caddy/Caddyfile"
BACKEND_PORT="${OGC_PORT:-8000}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hostname) HOSTNAME_SHORT="${2:?}"; shift 2 ;;
    --domain)   DOMAIN="${2:-}"; shift 2 ;;
    --prefix)   PREFIX="${2:?}"; shift 2 ;;
    --port)     BACKEND_PORT="${2:?}"; shift 2 ;;
    -h|--help)  sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then echo "Please run as root (sudo)." >&2; exit 1; fi

# Normalise: strip a trailing .local, lowercase. (The backend also validates,
# but apply.sh is called directly by the installer too.)
HOSTNAME_SHORT="$(printf '%s' "$HOSTNAME_SHORT" | tr '[:upper:]' '[:lower:]')"
HOSTNAME_SHORT="${HOSTNAME_SHORT%.local}"
if [[ ! "$HOSTNAME_SHORT" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]]; then
  echo "Invalid hostname: '$HOSTNAME_SHORT'" >&2; exit 2
fi
if [[ -n "$DOMAIN" && ! "$DOMAIN" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$ ]]; then
  echo "Invalid domain: '$DOMAIN'" >&2; exit 2
fi

step() { printf '\n\033[1;36m>> %s\033[0m\n' "$1"; }

# --- 1. Render the Caddyfile to a temp file --------------------------------
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

# Reusable proxy body. SSE-friendly out of the box (Caddy does not buffer, so
# /api/events just works) and no request-body size cap needed — unlike nginx,
# Caddy imposes no upload limit by default, so we don't set request_body at all
# (which avoids betting on the semantics of `max_size 0`).
render_site() {  # render_site <address> <extra-tls-line>
  cat <<SITE
$1 {
    $2
    reverse_proxy localhost:${BACKEND_PORT}
}
SITE
}

{
  echo "# Managed by deploy/https/apply.sh — edits are overwritten. Change the"
  echo "# hostname/domain via the installer or System → HTTPS in the web UI."
  echo
  render_site "${HOSTNAME_SHORT}.local" "tls internal"
  if [[ -n "$DOMAIN" ]]; then
    echo
    render_site "$DOMAIN" ""
  fi
} > "$TMP"

# --- 2. Validate BEFORE touching the live config ---------------------------
step "Validating Caddy config..."
if ! caddy validate --config "$TMP" --adapter caddyfile; then
  echo "   Caddy config invalid — keeping the existing $CADDYFILE unchanged." >&2
  exit 1
fi

# --- 3. Atomic swap + reload -----------------------------------------------
mkdir -p "$(dirname "$CADDYFILE")"
install -m 644 "$TMP" "$CADDYFILE"
step "Reloading Caddy..."
systemctl reload caddy 2>/dev/null || systemctl restart caddy

# --- 4. Set the system hostname (only if it changed) -----------------------
CURRENT_HOST="$(hostnamectl --static 2>/dev/null || cat /etc/hostname 2>/dev/null || echo "")"
if [[ "$CURRENT_HOST" != "$HOSTNAME_SHORT" ]]; then
  step "Setting hostname to '$HOSTNAME_SHORT'..."
  # Record the ORIGINAL pre-OffgridCloud hostname once, so uninstall.sh can put
  # it back. Later re-applies must not overwrite the first capture (quoted —
  # the file is sourced by uninstall.sh).
  HOST_STATE="$PREFIX/data/https-hostname.state"
  if [[ ! -f "$HOST_STATE" && -n "$CURRENT_HOST" ]]; then
    mkdir -p "$PREFIX/data"
    printf 'PREV_HOSTNAME="%s"\n' "$CURRENT_HOST" > "$HOST_STATE" 2>/dev/null || true
  fi
  hostnamectl set-hostname "$HOSTNAME_SHORT"
  # Keep /etc/hosts in sync so sudo doesn't warn about an unresolvable host.
  if grep -qE '^\s*127\.0\.1\.1' /etc/hosts; then
    sed -i -E "s/^\s*127\.0\.1\.1.*/127.0.1.1\t${HOSTNAME_SHORT}/" /etc/hosts
  else
    printf '127.0.1.1\t%s\n' "$HOSTNAME_SHORT" >> /etc/hosts
  fi
  systemctl restart avahi-daemon 2>/dev/null || true
fi

# --- 5. Persist state for the backend to read ------------------------------
STATE_DIR="$PREFIX/data"
mkdir -p "$STATE_DIR"
printf '{"hostname": "%s", "domain": "%s"}\n' "$HOSTNAME_SHORT" "$DOMAIN" \
  > "$STATE_DIR/https_state.json"

step "Done. LAN: https://${HOSTNAME_SHORT}.local${DOMAIN:+  Public: https://$DOMAIN}"
