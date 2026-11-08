#!/usr/bin/env bash
# Open the demo view full screen on the Pi's own display. No internet needed: the page is served by
# eagle-demo.service on this Pi (http://localhost:8081). Used by the desktop icon and the login autostart.
#   bash pi/field/open_demo.sh            # kiosk (full screen, no browser bars); Alt+F4 closes it
#   bash pi/field/open_demo.sh --window   # normal window instead
URL="${EAGLE_DEMO_URL:-http://localhost:8081}"
for _ in $(seq 60); do                                  # wait up to 60 s for the page after boot
  curl -s -o /dev/null --max-time 1 "$URL/state" && break
  sleep 1
done
B="$(command -v chromium || command -v chromium-browser || command -v firefox)"
[ -n "$B" ] || { echo "no browser found"; exit 1; }
if [ "${1:-}" = "--window" ]; then
  exec "$B" --new-window "$URL"
fi
case "$B" in
  *firefox) exec "$B" --kiosk "$URL" ;;
  *) exec "$B" --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
        --no-first-run --check-for-update-interval=31536000 --password-store=basic \
        --user-data-dir="$HOME/.config/eagle-demo-browser" "$URL" ;;
esac
