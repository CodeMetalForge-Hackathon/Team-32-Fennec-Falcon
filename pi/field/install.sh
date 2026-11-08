#!/usr/bin/env bash
# Install the field services so the Pi starts everything on power-up with no screen, keyboard or network:
#   eagle-nest       nest_station (decrypts life_node's frames, CoT)          -> ~/field_logs/nest.log
#   eagle-life       life_node: thermal + audio + vision, --record             -> ~/field_logs/<run>/, life.log
#   eagle-vision     hailo_vision on the NoIR camera -> ~/eagle/vision.jsonl   -> ~/field_logs/vision.log
#   eagle-dashboard  read-only status page, http://<pi>:8080 (hotspot: http://10.42.0.1:8080)
# thermal.service and fusion.service open the same camera / microphone, so they are DISABLED (not removed);
# `install.sh uninstall` removes the eagle services and re-enables thermal.service.
#
#   bash pi/field/install.sh            # install + enable (starts now unless --no-start)
#   bash pi/field/install.sh uninstall
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
USER_NAME="${SUDO_USER:-$USER}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
UNITS=(eagle-nest eagle-life eagle-vision eagle-dashboard)

if [ "${1:-}" = "uninstall" ]; then
  for u in "${UNITS[@]}"; do
    sudo systemctl disable --now "$u.service" 2>/dev/null || true
    sudo rm -f "/etc/systemd/system/$u.service"
  done
  sudo systemctl daemon-reload
  sudo systemctl enable --now thermal.service 2>/dev/null || true
  echo "eagle field services removed; thermal.service re-enabled"
  exit 0
fi

START=1
[ "${1:-}" = "--no-start" ] && START=0

for f in "$HOME_DIR/hailo-apps/venv_hailo_apps/bin/python" "$HOME_DIR/eagle-models/models.lock.json"; do
  [ -e "$f" ] || { echo "missing $f: install hailo-apps and run apps/fetch_models.py first (README section 10)"; exit 1; }
done

for u in "${UNITS[@]}"; do
  sed -e "s|@USER@|$USER_NAME|g" -e "s|@REPO@|$REPO|g" -e "s|@HOME@|$HOME_DIR|g" "$HERE/$u.service" \
    | sudo tee "/etc/systemd/system/$u.service" >/dev/null
done
chmod +x "$HERE/run_vision.sh"
sudo systemctl daemon-reload
for old in thermal.service fusion.service; do
  if systemctl is-enabled --quiet "$old" 2>/dev/null || systemctl is-active --quiet "$old" 2>/dev/null; then
    echo "disabling $old (same camera / microphone)"
    sudo systemctl disable --now "$old"
  fi
done
for u in "${UNITS[@]}"; do
  sudo systemctl enable "$u.service"
done
if [ "$START" = 1 ]; then
  sudo systemctl restart "${UNITS[@]/%/.service}"
fi
sudo -u "$USER_NAME" mkdir -p "$HOME_DIR/field_logs" "$HOME_DIR/eagle"
systemctl --no-pager --lines=0 status "${UNITS[@]/%/.service}" | grep -E "●|Active:" || true
echo "logs: $HOME_DIR/field_logs (nest.log, life.log, vision.log, <run>/). Status page: http://<pi>:8080"
