# Thermal camera probe

`thermal_probe.py` finds, views and streams a USB (UVC) thermal camera with OpenCV. It works on
Windows (DirectShow, Media Foundation) and Linux (V4L2, for example the Pi 5). Reference:
[`docs/thermal-probe.md`](../docs/thermal-probe.md).

```bash
pip install opencv-python numpy        # Windows, optional: pip install pygrabber (camera names)
python thermal_probe.py                # scan every camera, backend, size and format; prints the best mode
python thermal_probe.py --view         # live false-colour view with hot-spot boxes
python thermal_probe.py --list         # camera names and the modes ffmpeg sees
python thermal_probe.py --view --ffmpeg "<camera name>" --size 256x192    # when OpenCV can't open it
python thermal_probe.py --view --rtsp rtsp://127.0.0.1:8554/eagle         # stream to MediaMTX
```

To view the stream, run `mediamtx` first, then open `http://<laptop IP>:8889/eagle` (WebRTC) or
`rtsp://<laptop IP>:8554/eagle` in VLC.

Run the tests with `python test_probe.py`. They use a simulated camera and real ffmpeg.
