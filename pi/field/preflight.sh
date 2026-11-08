#!/usr/bin/env bash
# Field preflight: read-only checks before a run or a demo. Each line prints PASS or FAIL; nothing is started,
# stopped or written. Exit status = number of FAILs (0 = ready).
#
#   bash pi/field/preflight.sh
set -uo pipefail
EAGLE="${EAGLE_DIR:-$HOME/eagle}"
LOGS="${EAGLE_LOGS:-$HOME/field_logs}"
fails=0

check() {  # check "description" <exit status of the test> "detail"
  if [ "$2" -eq 0 ]; then printf 'PASS  %-34s %s\n' "$1" "$3"; else printf 'FAIL  %-34s %s\n' "$1" "$3"; fails=$((fails + 1)); fi
}

age_s() {  # seconds since a file was modified, or empty if it does not exist
  [ -e "$1" ] && echo $(( $(date +%s) - $(stat -c %Y "$1") ))
}

fresh() {  # fresh "description" FILE MAX_SECONDS
  local a; a="$(age_s "$2")"
  if [ -z "$a" ]; then check "$1" 1 "$2 missing"
  else [ "$a" -lt "$3" ]; check "$1" $? "$(basename "$2") ${a} s old (limit < $3 s)"; fi
}

# Pi camera (libcamera lists cameras without opening them, so this works while eagle-vision holds it)
cams="$(timeout 10 rpicam-hello --list-cameras 2>/dev/null | grep -E '^\s*[0-9]+ : ' | sed -E 's/^\s*([0-9]+) : ([^ ]+).*/\1:\2/' | tr '\n' ' ')"
[ -n "$cams" ]; check "Pi camera listed" $? "${cams:-none}"

# Hailo on PCIe
hailo="$(lspci 2>/dev/null | grep -i hailo | sed -E 's/^[^ ]+ [^:]+: //')"
[ -n "$hailo" ]; check "Hailo on PCIe" $? "${hailo:-not on the PCIe bus (reboot; if still missing, power off 10 s)}"

# Power: no under-voltage / throttling since boot
thr="$(vcgencmd get_throttled 2>/dev/null)"
[ "$thr" = "throttled=0x0" ]; check "Power (throttled=0x0)" $? "${thr:-vcgencmd unavailable}"

# Services
for u in eagle-vision eagle-life eagle-nest eagle-demo; do
  st="$(systemctl is-active "$u" 2>/dev/null)"
  [ "$st" = "active" ]; check "$u active" $? "$st, restarts $(systemctl show -p NRestarts --value "$u" 2>/dev/null)"
done

# Live feeds
cam_snap="$(ls -t "$EAGLE"/cam_*.jpg 2>/dev/null | head -1)"
fresh "Camera snapshot < 3 s" "${cam_snap:-$EAGLE/cam_standard.jpg}" 3
fresh "Live thermal < 5 s" "$LOGS/live_thermal.npy" 5
fresh "Live audio < 5 s" "$LOGS/live_audio.npz" 5

# Target profile
prof="$EAGLE/target_profile.npz"
[ -s "$prof" ]; check "Target profile present" $? "$prof$( [ -s "$prof" ] && echo " ($(stat -c %s "$prof") B)")"

# Nest: a decrypted RX line within the last 60 s (nest.log lines start with [HH:MM:SS], local time).
# grep -a: a power cut can leave NUL bytes in the log, after which plain grep stops at "binary file".
last_rx="$(grep -aE '^\[[0-9]{2}:[0-9]{2}:[0-9]{2}\] RX' "$LOGS/nest.log" 2>/dev/null | tail -1)"
if [ -z "$last_rx" ]; then
  check "Nest RX in the last 60 s" 1 "no RX line in $LOGS/nest.log"
else
  ts="${last_rx:1:8}"
  rx_s=$(( 10#${ts:0:2} * 3600 + 10#${ts:3:2} * 60 + 10#${ts:6:2} ))
  now_s=$(( 10#$(date +%H) * 3600 + 10#$(date +%M) * 60 + 10#$(date +%S) ))
  d=$(( (now_s - rx_s + 86400) % 86400 ))            # across midnight too
  [ "$d" -le 60 ]; check "Nest RX in the last 60 s" $? "last RX at $ts (${d} s ago)"
fi

echo
if [ "$fails" -eq 0 ]; then echo "PREFLIGHT: ALL PASS"; else echo "PREFLIGHT: $fails FAIL(s)"; fi
exit "$fails"
