#!/usr/bin/env bash
# Launch Chromium full-screen (kiosk) pointing at the local OffgridCloud portal.
#
# Started on demand from the console menu ("Web-Oberfläche im Browser öffnen"),
# not at boot — so the lightweight text console stays the default face of the
# box and the browser is just one thing you can open from it. Chromium runs on
# a bare X server via xinit on the *current* VT (tty1, the one the console owns);
# when the browser window is closed, xinit exits and control returns to the menu.
#
# Meant for a Pi 4/5 with a desktop stack. On a Pi 3 the text console is the
# recommended experience — Chromium is heavy.
#
# Usage: chromium-kiosk.sh [URL]   (default: http://127.0.0.1:8000)
set -euo pipefail

URL="${1:-http://127.0.0.1:8000}"

CHROME="$(command -v chromium-browser || command -v chromium || true)"
if [[ -z "$CHROME" ]]; then
  echo "Chromium ist nicht installiert. Installer mit --with-chromium erneut ausführen." >&2
  sleep 3
  exit 1
fi
if ! command -v xinit >/dev/null 2>&1; then
  echo "xinit fehlt. Installer mit --with-chromium erneut ausführen." >&2
  sleep 3
  exit 1
fi

# Chromium's own restart prompt after a hard power-off would break the kiosk;
# scrub the "exited cleanly" flags so it never nags on the next launch.
PROFILE="${HOME:-/root}/.config/chromium/Default"
if [[ -f "$PROFILE/Preferences" ]]; then
  sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' \
    "$PROFILE/Preferences" 2>/dev/null || true
fi

# Blank the cursor and stop the console from blanking mid-view.
XINITRC_FLAGS=(
  --kiosk
  --incognito
  --noerrdialogs
  --disable-infobars
  --disable-session-crashed-bubble
  --disable-translate
  --no-first-run
  --fast --fast-start
  --overscroll-history-navigation=0
  --check-for-update-interval=31536000
  --autoplay-policy=no-user-gesture-required
)

# xinit CLIENT [args] -- SERVER [display] [vt] [args]
# vt1 pins X to the console the menu already owns; -keeptty avoids a VT switch.
exec xinit "$CHROME" "${XINITRC_FLAGS[@]}" "$URL" \
  -- /usr/bin/X :0 vt1 -keeptty 2>/dev/null
