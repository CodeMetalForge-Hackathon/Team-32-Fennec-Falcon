#!/usr/bin/env bash
# eagle-vision.service: Pi camera -> Hailo person boxes (+ identity) -> ~/eagle/vision.jsonl.
# The camera is picked by sensor name: camera.sensor in field.yaml, or EAGLE_CAMERA=imx708_wide_noir to override.
# A 1/s snapshot (boxes drawn) goes to ~/eagle/cam_<name>.jpg for the field page: name "noir" for a
# *_noir sensor, else "standard" (EAGLE_CAMERA_NAME overrides).
# Sets up what ~/hailo-apps/setup_env.sh would (it needs an interactive shell in its own folder).
set -euo pipefail
HAILO_APPS="${HAILO_APPS:-$HOME/hailo-apps}"
EAGLE_KIT="${EAGLE_KIT:-$(cd "$(dirname "$0")/../../eagle-kit" && pwd)}"
PROFILE="${EAGLE_PROFILE:-$HOME/eagle/target_profile.npz}"
FIELD_YAML="${EAGLE_FIELD_YAML:-$(cd "$(dirname "$0")" && pwd)/field.yaml}"

export PYTHONPATH="$HAILO_APPS${PYTHONPATH:+:$PYTHONPATH}"
export GST_PLUGIN_PATH="/usr/local/hailo/resources/so${GST_PLUGIN_PATH:+:$GST_PLUGIN_PATH}"
if [ -f /usr/local/hailo/resources/.env ]; then
  set -a; . /usr/local/hailo/resources/.env; set +a
fi
mkdir -p "$HOME/eagle"

sensor="${EAGLE_CAMERA:-$(sed -n 's/^  sensor: *\([A-Za-z0-9_]*\).*/\1/p' "$FIELD_YAML" | head -1)}"
case "$sensor" in *noir*) default_name=noir ;; *) default_name=standard ;; esac
CAM_NAME="${EAGLE_CAMERA_NAME:-$default_name}"

args=(--jsonl "$HOME/eagle/vision.jsonl" --headless yes --config "$FIELD_YAML"
      --snapshot "$HOME/eagle/cam_${CAM_NAME}.jpg" --snapshot-hz 1)
if [ -n "${EAGLE_CAMERA:-}" ]; then
  args+=(--camera "$EAGLE_CAMERA")
fi
if [ -f "$PROFILE" ]; then
  args+=(--profile "$PROFILE")          # identity matching only when a target has been enrolled
else
  echo "no target profile at $PROFILE: person boxes only"
fi
cd "$EAGLE_KIT"
exec "$HAILO_APPS/venv_hailo_apps/bin/python" apps/hailo_vision.py "${args[@]}" "$@"
