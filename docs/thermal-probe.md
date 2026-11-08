# Thermal camera probe

Technical reference for [`thermal-probe/`](../thermal-probe/).

## What it is

`thermal_probe.py` finds a working mode for a USB (UVC) thermal camera, shows a live false-colour
view with hot-spot boxes, and can stream it as H.264 to other screens. It runs on Windows
(DirectShow, Media Foundation) and Linux (V4L2, for example on a Raspberry Pi 5). It was written
for HTI phone thermal imagers (256 × 192) and also covers similar USB-C cameras such as the
InfiRay P2 Pro, Topdon TC001 and PureThermal with a Lepton.

## Why

Our HTI thermal imager shows up as a USB camera, but OpenCV could not open it with default
settings. Low-cost thermal cores often need one particular backend, frame size or pixel format,
and some send the picture and the raw 16-bit data stacked in one double-height frame. Rather than
guess at the demo table, the probe tries every combination and reports the one that works.

## How it works

- **Scan:** every camera × backend × size (256 × 192, 256 × 384, 160 × 120, 384 × 288, 640 × 512
  and others) × format (YUY2, Y16, with OpenCV's colour conversion on and off). Modes that carry
  raw values rank above plain pictures, and a flat picture is penalised. `--quick` tries only the
  most likely modes.
- **Frame kinds:** `y16` (16-bit raw), `split` (picture on top, 16-bit raw underneath), `yuyv`,
  `gray8` and `bgr`. Only `y16` and `split` carry raw values; the rest are pictures with relative
  grey levels.
- **View:** false colour with hot-spot boxes. Keys: q quit, s save a PNG and the raw array, p
  palette, h hot spots on and off.
- **ffmpeg fallback** (`--ffmpeg "<camera name>"`) for cameras OpenCV cannot open; `--list` shows
  camera names and the modes ffmpeg sees.
- **Stream** (`--rtsp rtsp://127.0.0.1:8554/eagle`): H.264 to MediaMTX, viewable at
  `http://<laptop IP>:8889/eagle` (WebRTC) or in VLC.
- Prints a ready-made `life.thermal` block for the [Eagle kit's](eagle-kit.md) config.

## Verification

- `python test_probe.py`: a simulated camera plus real ffmpeg. It covers split-frame decoding,
  mode selection, hot spots, the ffmpeg reader and the H.264 output (768 × 576).
- **Not yet run on Windows with the HTI imager.**
