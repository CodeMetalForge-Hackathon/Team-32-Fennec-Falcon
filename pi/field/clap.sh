#!/usr/bin/env bash
# Field page "Clap test": life_node holds the mic array, so stop it, run eagle_node's clap test
# (never transmits), then restart life_node. Clap ~5 m away ON the module's 0 deg mark first.
set -uo pipefail
source "$(dirname "$0")/field_common.sh"
SECONDS_RUN="${1:-20}"
echo "== $(date '+%F %T') clap test: stopping life_node"
svc stop eagle-life.service
sleep 2
cd "$EAGLE_KIT"
"$PY" apps/eagle_node.py --config "$FIELD_CFG" --clap-test --seconds "$SECONDS_RUN"
rc=$?
echo "== clap test exit code $rc; restarting life_node"
svc start eagle-life.service
echo "== DONE. Put 'body az mean' (claps on the 0 deg mark) into Site setup > Mic az offset."
exit $rc
