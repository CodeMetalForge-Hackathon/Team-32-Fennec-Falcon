#!/usr/bin/env bash
# Desktop icon + open-at-login for the demo view, so it comes up on the screen with no internet and no typing.
#   bash pi/field/kiosk_install.sh            # add the icon and open the demo full screen at every login
#   bash pi/field/kiosk_install.sh --icon     # icon only, no autostart
#   bash pi/field/kiosk_install.sh remove
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$HERE/open_demo.sh"
APPS="$HOME/.local/share/applications"; AUTO="$HOME/.config/autostart"; DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ "${1:-}" = "remove" ]; then
  rm -f "$APPS/eagle-demo.desktop" "$AUTO/eagle-demo.desktop" "$DESK/eagle-demo.desktop"
  echo "removed"; exit 0
fi
mkdir -p "$APPS" "$AUTO" "$DESK"
entry() {
  cat <<D
[Desktop Entry]
Type=Application
Name=Eagle Demo
Comment=Full-screen search view (offline, http://localhost:8081). Alt+F4 to close.
Exec=$HERE/open_demo.sh
Icon=applications-science
Terminal=false
Categories=Utility;
D
}
entry > "$APPS/eagle-demo.desktop"
entry > "$DESK/eagle-demo.desktop"
chmod +x "$DESK/eagle-demo.desktop"
if [ "${1:-}" != "--icon" ]; then
  entry > "$AUTO/eagle-demo.desktop"
  echo "demo view opens full screen at every login (remove: bash $0 remove)"
fi
echo "icon: 'Eagle Demo' on the desktop and in the menu"
