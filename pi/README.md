# Pi thermal streaming

Runs on the Raspberry Pi with the HTI thermal imager plugged into it. Streams a colorized
thermal view with min/max/center temperatures over RTSP and WebRTC, records the video, and
saves raw temperatures separately (video compression destroys the real values).

```
HTI imager --USB--> Pi: thermal_rtsp.py --> FFmpeg H.264 --> MediaMTX --> RTSP / WebRTC / recording
                         |--> ~/thermal_data/<session>/  raw temps (.npy, 1/s) + telemetry.csv
                         '--> UDP JSON telemetry (5x/s) to the hotspot network
```

## Files

| File | What it does |
|---|---|
| `thermal_rtsp.py` | Capture, temperature conversion, warm-body detection, overlay, encode, publish. Reconnects on its own if the camera or MediaMTX drops. |
| `mediamtx.yml` | MediaMTX config: RTSP, WebRTC, HLS, records to `/home/thermal_recordings` in 10-minute MP4s, kept 3 days. Only the Pi can publish; anyone on the network can watch. |
| `setup_pi.sh` | One-time install (needs internet): FFmpeg, OpenCV, v4l-utils, MediaMTX v1.21.3, boot services. |
| `hotspot_on.sh` / `hotspot_off.sh` | Field Wi-Fi hotspot `THERMAL-PI` (password `thermal1234`). Pi is `10.42.0.1`. |

## Steps

1. Copy this folder to the Pi (e.g. `scp -r pi <user>@<pi-ip>:~/thermal`).
2. Plug the imager into the Pi and check it: `v4l2-ctl --list-devices`
3. Test capture without installing services:
   `sudo apt install -y python3-opencv python3-numpy ffmpeg v4l-utils && python3 ~/thermal/thermal_rtsp.py --check`
   It should print five lines with plausible temperatures (room ~20 C, your hand ~33 C).
4. Install: `cd ~/thermal && sudo bash setup_pi.sh`. Services start now and on every boot.
5. Watch from the laptop: VLC > Open Network Stream > `rtsp://<pi-ip>:8554/thermal`,
   or a browser at `http://<pi-ip>:8889/thermal`.
6. Before the field (still on home Wi-Fi, done last): `bash ~/thermal/hotspot_on.sh`.
   Join `THERMAL-PI` from the laptop/phone and use `10.42.0.1` in the URLs above.
7. Back home: `bash ~/thermal/hotspot_off.sh`.

## Warm-body detection

Each frame is searched for regions between 26 and 42 C that are at least 5 C above the scene
median, at least 6 sensor pixels in size, and not touching anything hotter than 42 C (so the
cooling edge of an engine or fire isn't flagged). Detections are boxed on the stream, logged to
`detections.jsonl`, and included in the UDP telemetry.

It finds warm bodies; it cannot tell a person from a deer or a dog. Tune in the field with
`--det-min`, `--det-max`, `--det-delta` and `--det-min-area` (e.g. raise `--det-min-area` to
ignore small animals, lower `--det-delta` on warm evenings). `--no-detect` turns it off.
The raw temperature saves from field tests are the training data for a learned person/animal
classifier later.

## Useful commands

- Logs: `journalctl -u thermal -f` and `journalctl -u mediamtx -f`
- Restart: `sudo systemctl restart thermal`
- Native resolution (lowest bandwidth): edit `/etc/systemd/system/thermal.service`, add `--scale 1`
- Pi 4 hardware encoder: add `--encoder h264_v4l2m2m` (Pi 5 has none; libx264 is fine at this size)
- Raw saves pause automatically below 2 GB free (`--min-free-gb`); streaming continues
- No camera handy: `python3 thermal_rtsp.py --fake` streams a synthetic warm blob

## If the camera check fails

`--check` tries OpenCV first, then FFmpeg, on each V4L2 node named `HikCamera` or `UVC Camera`
(never the Pi's own ISP/codec nodes), giving each up to 5 seconds to deliver a frame. Run
`v4l2-ctl -d /dev/video0 --list-formats-ext` and confirm a `YUYV 256x392` mode exists. If the
frames come back but temperatures look wrong (e.g. negative or thousands of degrees), the raw
bytes are being converted; force the FFmpeg reader with `--reader ffmpeg`.
