#!/usr/bin/env bash
# Switch the field Pi from life_node (life list to the Nest every 30 s) to eagle_runtime (decision gate:
# silent until sure, then one sealed FIX), or back. Run after pi/field/install.sh.
#   bash pi/field/use_runtime.sh          # install + start eagle-runtime.service, stop + disable eagle-life
#   bash pi/field/use_runtime.sh off      # back to eagle-life
# The dashboard (http://<pi>:8080) shows the gate either way; logs: ~/field_logs/runtime.log.
set -euo pipefail
source "$(dirname "$0")/field_common.sh"           # HERE, REPO, EAGLE_KIT, PY
USER_NAME="${SUDO_USER:-$USER}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"

if [ "${1:-}" = "off" ]; then
  sudo systemctl disable --now eagle-runtime.service 2>/dev/null || true
  sudo systemctl enable --now eagle-life.service
  echo "eagle-life is back; eagle-runtime disabled"
  exit 0
fi

[ -x "$PY" ] || { echo "missing $PY: install hailo-apps first (README section 10)"; exit 1; }
if ! "$PY" -c "import onnxruntime" 2>/dev/null; then
  "$PY" -m pip install -q onnxruntime || echo "onnxruntime not installed: the thermal model falls back to OpenCV"
fi
echo "== self-test: the night scene through the gate, no radio"
(cd "$EAGLE_KIT" && "$PY" apps/eagle_runtime.py --sim night --no-radio --speed 0 | grep -E "DECIDE  track|TX ") \
  || { echo "self-test failed: see the output above"; exit 1; }

chmod +x "$HERE/run_runtime.sh"
sed -e "s|@USER@|$USER_NAME|g" -e "s|@REPO@|$REPO|g" -e "s|@HOME@|$HOME_DIR|g" "$HERE/eagle-runtime.service" \
  | sudo tee /etc/systemd/system/eagle-runtime.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl disable --now eagle-life.service 2>/dev/null || true
sudo systemctl enable eagle-runtime.service
sudo systemctl restart eagle-runtime.service
systemctl --no-pager --lines=0 status eagle-runtime.service | grep -E "●|Active:" || true
echo "eagle-runtime on (eagle-life off). Logs: $HOME_DIR/field_logs/runtime.log. Back: bash pi/field/use_runtime.sh off"
