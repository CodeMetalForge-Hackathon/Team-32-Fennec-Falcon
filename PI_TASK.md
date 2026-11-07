# Task for Claude Code on the Pi: fix the HikCamera UVC negotiation

The camera streamed fine on Windows via Media Foundation, so it is standard UVC. The
"UVC probe control : -32" stalls point to Linux's uvcvideo negotiation, not hardware.

1. Try uvcvideo quirks one at a time. For each value in 0x100, 0x2, 0x102, 0x80, 0x180, 0x200:
   - `sudo modprobe -r uvcvideo && sudo modprobe uvcvideo quirks=<value>`
   - wait 3 s, confirm the HikCamera node exists (`v4l2-ctl --list-devices`)
   - `timeout 15 python3 pi/thermal_rtsp.py --check --reader ffmpeg`, and if that fails the same with `--reader opencv`
   - check `dmesg` after each attempt
2. Stop at the first value that gives frames with plausible temperatures (room ~20 C, a hand ~33 C).
3. Make it permanent: `/etc/modprobe.d/uvcvideo-hikcamera.conf` containing `options uvcvideo quirks=<value>`.
   Add that step to `pi/setup_pi.sh` and `pi/README.md`.
4. Run `sudo bash pi/setup_pi.sh`, confirm the mediamtx and thermal services run, and open
   http://localhost:8889/thermal in Chromium.
5. Do NOT run hotspot_on.sh.
6. If no quirk works, unplug and replug the camera once, retry 0x100 and 0x2, then stop and report.
7. Commit and push to thermal-cam.
