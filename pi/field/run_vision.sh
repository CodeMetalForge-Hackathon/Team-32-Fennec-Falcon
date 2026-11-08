#!/usr/bin/env bash
# eagle-vision.service: NoIR camera -> Hailo person boxes (+ identity) -> ~/eagle/vision.jsonl.
# Sets up what ~/hailo-apps/setup_env.sh would (it needs an interactive shell in its own folder).
set -euo pipefail
HAILO_APPS="${HAILO_APPS:-$HOME/hailo-apps}"
EAGLE_KIT="${EAGLE_KIT:-$(cd "$(dirname "$0")/../../eagle-kit" && pwd)}"
PROFILE="${EAGLE_PROFILE:-$HOME/eagle/target_profile.npz}"

export PYTHONPATH="$HAILO_APPS${PYTHONPATH:+:$PYTHONPATH}"
export GST_PLUGIN_PATH="/usr/local/hailo/resources/so${GST_PLUGIN_PATH:+:$GST_PLUGIN_PATH}"
if [ -f /usr/local/hailo/resources/.env ]; then
  set -a; . /usr/local/hailo/resources/.env; set +a
fi
mkdir -p "$HOME/eagle"

args=(--jsonl "$HOME/eagle/vision.jsonl" --headless yes)
if [ -f "$PROFILE" ]; then
  args+=(--profile "$PROFILE")          # identity matching only when a target has been enrolled
else
  echo "no target profile at $PROFILE: person boxes only"
fi
cd "$EAGLE_KIT"
exec "$HAILO_APPS/venv_hailo_apps/bin/python" apps/hailo_vision.py "${args[@]}" "$@"
