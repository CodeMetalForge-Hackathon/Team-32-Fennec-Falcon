#!/usr/bin/env bash
# eagle-life.service: life_node, recording to ~/field_logs unless ~/eagle/norecord exists.
# The field page's Start/Stop recording buttons create or remove that file and restart this service,
# so every Start makes a new run folder. Detection and the Nest keep running either way.
set -euo pipefail
source "$(dirname "$0")/field_common.sh"
args=(apps/life_node.py --config "$FIELD_CFG" --transport udp --require audio thermal --step 0.1)
if [ -e "$HOME/eagle/norecord" ]; then
  echo "recording off (~/eagle/norecord present)"
else
  args+=(--record "$HOME/field_logs")
fi
cd "$EAGLE_KIT"
exec "$PY" "${args[@]}" "$@"
