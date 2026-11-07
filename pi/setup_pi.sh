#!/usr/bin/env bash
# One-time Pi setup. Run from this folder:  sudo ./setup_pi.sh
# Needs internet. Installs FFmpeg, OpenCV, v4l-utils, MediaMTX, and boot services.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
USER_NAME="${SUDO_USER:-pi}"
HERE="$(cd "$(dirname "$0")" && pwd)"
MTX_VER="v1.21.3"

echo "== packages"
apt-get update
apt-get install -y ffmpeg python3-opencv python3-numpy v4l-utils

echo "== MediaMTX $MTX_VER"
case "$(uname -m)" in
  aarch64) ARCH=linux_arm64 ;;
  armv7l)  ARCH=linux_armv7 ;;
  armv6l)  ARCH=linux_armv6 ;;
  x86_64)  ARCH=linux_amd64 ;;
  *) echo "unknown arch $(uname -m)"; exit 1 ;;
esac
TMP=$(mktemp -d)
curl -fL "https://github.com/bluenviron/mediamtx/releases/download/${MTX_VER}/mediamtx_${MTX_VER}_${ARCH}.tar.gz" -o "$TMP/mtx.tgz"
mkdir -p /opt/mediamtx
tar -xzf "$TMP/mtx.tgz" -C /opt/mediamtx mediamtx
cp "$HERE/mediamtx.yml" /opt/mediamtx/mediamtx.yml
mkdir -p /home/thermal_recordings
chown "$USER_NAME":"$USER_NAME" /home/thermal_recordings

echo "== thermal app"
mkdir -p /opt/thermal
cp "$HERE/thermal_rtsp.py" /opt/thermal/
usermod -aG video "$USER_NAME"

echo "== services"
cat > /etc/systemd/system/mediamtx.service <<UNIT
[Unit]
Description=MediaMTX streaming server
After=network.target
[Service]
User=$USER_NAME
ExecStart=/opt/mediamtx/mediamtx /opt/mediamtx/mediamtx.yml
Restart=always
RestartSec=2
[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/thermal.service <<UNIT
[Unit]
Description=Thermal camera to RTSP
After=mediamtx.service
Wants=mediamtx.service
[Service]
User=$USER_NAME
ExecStart=/usr/bin/python3 /opt/thermal/thermal_rtsp.py --udp 10.42.0.255:5005
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now mediamtx.service thermal.service
sleep 3
systemctl --no-pager --lines=5 status mediamtx.service thermal.service || true
IP=$(hostname -I | awk '{print $1}')
echo
echo "Done. Watch the stream:"
echo "  VLC:     rtsp://$IP:8554/thermal"
echo "  Browser: http://$IP:8889/thermal"
echo "Logs:      journalctl -u thermal -f"
