#!/usr/bin/env bash
# Field page "Clear target": remove the enrolled profile and restart vision (person boxes only).
set -uo pipefail
source "$(dirname "$0")/field_common.sh"
rm -f "$HOME/eagle/target_profile.npz"
echo "== $(date '+%F %T') target profile removed; restarting vision"
# shellcheck disable=SC2046
svc restart $(vision_units)
echo "== DONE"
