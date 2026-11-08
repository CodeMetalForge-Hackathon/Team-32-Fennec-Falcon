#!/usr/bin/env bash
# eagle-hotspot.service (root, at boot): if the Pi has not joined a known Wi-Fi network within
# HOTSPOT_WAIT seconds, start the THERMAL-PI hotspot so a phone can reach http://10.42.0.1:8080.
# The hotspot profile is autoconnect=no, so at a venue with known Wi-Fi the Pi joins that instead.
set -uo pipefail
SSID="${HOTSPOT_SSID:-THERMAL-PI}"
PASS="${HOTSPOT_PASS:-}"          # from ~/eagle/hotspot.env (EnvironmentFile), never from the repo
WAIT="${HOTSPOT_WAIT:-30}"
CON=thermal-hotspot

if ! nmcli -t -f NAME connection show | grep -qx "$CON"; then
  if [ "${#PASS}" -lt 8 ]; then
    echo "$(date '+%F %T') no hotspot profile and no HOTSPOT_PASS (8+ chars) in ~/eagle/hotspot.env; skipping"
    exit 0
  fi
  nmcli connection add type wifi ifname wlan0 con-name "$CON" autoconnect no ssid "$SSID" \
    802-11-wireless.mode ap 802-11-wireless.band bg ipv4.method shared \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PASS"
else
  nmcli connection modify "$CON" connection.autoconnect no   # hotspot_on.sh sets yes; boot logic decides now
fi

for _ in $(seq "$WAIT"); do
  state="$(nmcli -t -f DEVICE,STATE,CONNECTION device | awk -F: '$1=="wlan0"{print $2":"$3}')"
  case "$state" in
    connected:"$CON") echo "$(date '+%F %T') hotspot already up"; exit 0 ;;
    connected:*)      echo "$(date '+%F %T') joined Wi-Fi (${state#connected:}), no hotspot"; exit 0 ;;
  esac
  sleep 1
done

nmcli radio wifi on
nmcli connection up "$CON" && echo "$(date '+%F %T') no known Wi-Fi: hotspot $SSID up at 10.42.0.1"
