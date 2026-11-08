#!/usr/bin/env bash
# eagle-runtime.service: eagle_runtime (life_node's sensors + the decision gate: silent until sure, then
# one sealed FIX to the Nest), recording to ~/field_logs unless ~/eagle/norecord exists, like run_life.sh.
# The field page's buttons restart whichever of eagle-runtime / eagle-life is enabled.
set -euo pipefail
source "$(dirname "$0")/field_common.sh"
args=(apps/eagle_runtime.py --config "$FIELD_CFG" --transport udp --require audio thermal)
if [ -e "$HOME/eagle/norecord" ]; then
  echo "recording off (~/eagle/norecord present)"
else
  args+=(--record "$HOME/field_logs")
fi
cd "$EAGLE_KIT"
exec "$PY" "${args[@]}" "$@"
