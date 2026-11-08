#!/usr/bin/env bash
# Shared helpers for the field action scripts (sourced, not run).
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
EAGLE_KIT="$REPO/eagle-kit"
PY="${EAGLE_PY:-$HOME/hailo-apps/venv_hailo_apps/bin/python}"
FIELD_CFG="$HERE/field.yaml"

# Vision units: the per-camera template instances when installed, else the single service.
vision_units() {
  if [ -f /etc/systemd/system/eagle-vision@.service ]; then
    echo "eagle-vision@standard.service eagle-vision@noir.service"
  else
    echo "eagle-vision.service"
  fi
}

svc() {  # svc start|stop|restart unit...   (sudoers allows exactly these for eagle-* units)
  local verb="$1"; shift
  sudo -n /usr/bin/systemctl "$verb" "$@"
}
