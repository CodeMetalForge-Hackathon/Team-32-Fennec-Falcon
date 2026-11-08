#!/usr/bin/env bash
# Install the field services so the Pi runs everything on power-up with no screen, keyboard or network:
#   eagle-nest        nest_station (decrypts life_node's frames, CoT)             -> ~/field_logs/nest.log
#   eagle-life        life_node: thermal + audio + vision (run_life.sh records)   -> ~/field_logs/<run>/, life.log
#   eagle-vision@NAME per-camera Hailo person detection (+ identity), NAME = standard, noir
#                     (or the single eagle-vision.service if the template is not present)
#   eagle-dashboard   phone control page, http://<pi>:8080 (hotspot: http://10.42.0.1:8080)
#   eagle-hotspot     at boot, THERMAL-PI hotspot if no known Wi-Fi within 30 s
# Every pi/field/*.service is installed. thermal.service and fusion.service open the same camera /
# microphone, so they are DISABLED (not removed). Also installs /etc/sudoers.d/eagle-field so the page
# can start/stop eagle services and reboot or power off.
#
#   bash pi/field/install.sh            # install + enable (starts now unless --no-start)
#   bash pi/field/install.sh uninstall
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
USER_NAME="${SUDO_USER:-$USER}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
VISION_NAMES=(standard noir)

unit_files=("$HERE"/*.service)
plain=()          # non-template unit names
template=""       # e.g. eagle-vision@
for f in "${unit_files[@]}"; do
  n="$(basename "$f" .service)"
  if [[ "$n" == *@ ]]; then template="$n"; else plain+=("$n"); fi
done
# The template replaces the single vision service.
if [ -n "$template" ]; then
  tmp=(); for n in "${plain[@]}"; do [ "$n" = "${template%@}" ] || tmp+=("$n"); done; plain=("${tmp[@]}")
fi
# eagle-runtime replaces eagle-life (pi/field/use_runtime.sh): enable one of the two, never both.
node="eagle-life"
systemctl is-enabled --quiet eagle-runtime.service 2>/dev/null && node="eagle-runtime"
tmp=(); for n in "${plain[@]}"; do
  if [ "$n" != "eagle-life" ] && [ "$n" != "eagle-runtime" ] || [ "$n" = "$node" ]; then tmp+=("$n"); fi
done; plain=("${tmp[@]}")
enable_units=("${plain[@]/%/.service}")
if [ -n "$template" ]; then
  for v in "${VISION_NAMES[@]}"; do enable_units+=("${template}${v}.service"); done
fi

if [ "${1:-}" = "uninstall" ]; then
  for u in /etc/systemd/system/eagle-*.service; do
    [ -e "$u" ] || continue
    n="$(basename "$u")"
    if [[ "$n" == *@.service ]]; then
      for v in "${VISION_NAMES[@]}"; do sudo systemctl disable --now "${n%.service}${v}.service" 2>/dev/null || true; done
    else
      sudo systemctl disable --now "$n" 2>/dev/null || true
    fi
    sudo rm -f "$u"
  done
  sudo rm -f /etc/sudoers.d/eagle-field
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

sudo -u "$USER_NAME" mkdir -p "$HOME_DIR/field_logs" "$HOME_DIR/eagle"
chmod +x "$HERE"/*.sh

# Old single vision unit, if the per-camera template now exists.
if [ -n "$template" ] && [ -e "/etc/systemd/system/${template%@}.service" ]; then
  sudo systemctl disable --now "${template%@}.service" 2>/dev/null || true
  sudo rm -f "/etc/systemd/system/${template%@}.service"
fi

for f in "${unit_files[@]}"; do
  sed -e "s|@USER@|$USER_NAME|g" -e "s|@REPO@|$REPO|g" -e "s|@HOME@|$HOME_DIR|g" "$f" \
    | sudo tee "/etc/systemd/system/$(basename "$f")" >/dev/null
done

# sudoers drop-in: checked with visudo before it is put in place.
tmp_sudo="$(mktemp)"
sed -e "s|@USER@|$USER_NAME|g" "$HERE/eagle-field.sudoers" > "$tmp_sudo"
if sudo visudo -cf "$tmp_sudo" >/dev/null; then
  sudo install -m 0440 -o root -g root "$tmp_sudo" /etc/sudoers.d/eagle-field
else
  echo "WARNING: sudoers file failed visudo; page buttons that control services will not work"
fi
rm -f "$tmp_sudo"

sudo systemctl daemon-reload
for old in thermal.service fusion.service; do
  if systemctl is-enabled --quiet "$old" 2>/dev/null || systemctl is-active --quiet "$old" 2>/dev/null; then
    echo "disabling $old (same camera / microphone)"
    sudo systemctl disable --now "$old"
  fi
done
for u in "${enable_units[@]}"; do sudo systemctl enable "$u"; done
if [ "$START" = 1 ]; then
  for u in "${enable_units[@]}"; do sudo systemctl restart "$u" || echo "WARNING: $u did not start"; done
fi
systemctl --no-pager --lines=0 status "${enable_units[@]}" | grep -E "●|Active:" || true
echo "logs: $HOME_DIR/field_logs. Control page: http://<pi>:8080 (hotspot THERMAL-PI: http://10.42.0.1:8080)"
