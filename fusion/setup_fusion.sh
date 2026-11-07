#!/usr/bin/env bash
# One-time setup for the three-sensor demo. Run from the repo:  bash fusion/setup_fusion.sh
# Needs internet once; everything after this runs offline.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
USER_NAME="$(id -un)"
VENV="$HOME/fusion-venv"

echo "== system packages"
sudo apt-get install -y python3-opencv python3-numpy python3-picamera2 alsa-utils ffmpeg v4l-utils

echo "== Python venv (uses the system OpenCV and Picamera2) + offline audio runtime"
python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/pip" install -q ai-edge-litert
"$VENV/bin/python" -c "import cv2, numpy; from ai_edge_litert.interpreter import Interpreter; print('python ok, OpenCV', cv2.__version__)"

echo "== devices"
MIC="default"
CARD=$(awk '/USB/ && /\[/ {gsub(/[\[\] ]/,"",$2); print $2; exit}' /proc/asound/cards || true)
if [ -n "${CARD:-}" ]; then MIC="plughw:CARD=$CARD,DEV=0"; echo "USB microphone: $MIC"; else echo "no USB microphone found yet (arecord -l)"; fi
rpicam-hello --list-cameras 2>&1 | head -5 || echo "no Pi camera found yet"
v4l2-ctl --list-devices 2>/dev/null | grep -A1 -i hikcamera || echo "thermal imager not found yet"

echo "== service (not started)"
sudo tee /etc/systemd/system/fusion.service >/dev/null <<UNIT
[Unit]
Description=Thermal + visual + audio human detection dashboard
After=network.target sound.target
Conflicts=thermal.service

[Service]
User=$USER_NAME
WorkingDirectory=$ROOT
ExecStart=$VENV/bin/python $HERE/app.py --mic $MIC
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload

IP=$(hostname -I | awk '{print $1}')
cat <<MSG

Done. To run the demo now (stops the RTSP thermal stream, which uses the same camera):
  sudo systemctl stop thermal 2>/dev/null; $VENV/bin/python $HERE/app.py --mic $MIC
Dashboard: http://localhost:8080 on the Pi, or http://$IP:8080 (http://10.42.0.1:8080 on the field hotspot)

To start the demo on every boot instead of the RTSP stream:
  sudo systemctl disable --now thermal; sudo systemctl enable --now fusion
MSG
