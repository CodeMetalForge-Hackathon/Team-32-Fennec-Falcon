#!/usr/bin/env bash
# Field page "Enroll face": stop vision (it holds the cameras), run a live face scan, restart vision.
# Extra enroll_target arguments (for example camera selection) come from ~/eagle/enroll_args, one line;
# the default turns off the NoIR tuning because faces are enrolled on the standard camera.
set -uo pipefail
source "$(dirname "$0")/field_common.sh"
ARGS="--tuning-file none"
[ -s "$HOME/eagle/enroll_args" ] && ARGS="$(head -n1 "$HOME/eagle/enroll_args")"
mkdir -p "$HOME/eagle"
echo "== $(date '+%F %T') enroll: stopping vision"
# shellcheck disable=SC2046
svc stop $(vision_units)
sleep 2
echo "== look at the camera and slowly turn your head left, right, up and down"
cd "$EAGLE_KIT"
# shellcheck disable=SC2086
"$PY" apps/enroll_target.py --live --out "$HOME/eagle/target_profile.npz" $ARGS
rc=$?
echo "== enroll exit code $rc; restarting vision"
# shellcheck disable=SC2046
svc start $(vision_units)
[ $rc -eq 0 ] && echo "== DONE: target enrolled" || echo "== FAILED: see messages above"
exit $rc
