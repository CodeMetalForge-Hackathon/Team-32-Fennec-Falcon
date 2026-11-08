#!/usr/bin/env bash
# Turn the Pi into a Wi-Fi hotspot for the field (no internet needed).
# WARNING: this drops the Pi off your current Wi-Fi. If you are SSH'd in over Wi-Fi you will be
# disconnected; reconnect by joining the hotspot and running ssh <user>@10.42.0.1
# Pi address on the hotspot is 10.42.0.1. Stream: rtsp://10.42.0.1:8554/thermal
set -euo pipefail
SSID="${1:-THERMAL-PI}"
[ -f "$HOME/eagle/hotspot.env" ] && . "$HOME/eagle/hotspot.env"
PASS="${2:-${HOTSPOT_PASS:-}}"     # kept out of the repo: pass it, or set HOTSPOT_PASS in ~/eagle/hotspot.env
[ "${#PASS}" -ge 8 ] || { echo "usage: $0 [SSID] PASSWORD (8+ chars), or set HOTSPOT_PASS in ~/eagle/hotspot.env" >&2; exit 1; }
sudo nmcli connection delete thermal-hotspot >/dev/null 2>&1 || true
sudo nmcli connection add type wifi ifname wlan0 con-name thermal-hotspot autoconnect yes ssid "$SSID" \
  802-11-wireless.mode ap 802-11-wireless.band bg ipv4.method shared \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PASS"
sudo nmcli connection modify thermal-hotspot connection.autoconnect-priority 100
sudo nmcli connection up thermal-hotspot
echo "Hotspot '$SSID' up. Pi is 10.42.0.1. It will come back on every boot until hotspot_off.sh."
