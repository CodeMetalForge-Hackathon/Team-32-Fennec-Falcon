# Three-sensor human detection (offline)

Runs on the Raspberry Pi 5 and decides whether a human is present from three independent sensors,
then shows all three feeds and the fused verdict on one dashboard. No internet is needed after setup.

| Sensor | Hardware | What it detects | Model |
|---|---|---|---|
| Heat | HTI thermal imager (Hikvision, USB 2BDF:0102) | warm bodies 26-42 C, and person shapes | warm-body detector from `pi/thermal_rtsp.py`; optional `models/thermal_person.onnx` |
| Visual | Raspberry Pi Camera Module 3 (or a USB webcam) | people vs animals vs objects | YOLOv8n trained on COCO, run with OpenCV DNN (`models/coco_yolov8n_320.onnx`) |
| Sound | USB microphone | speech, shouting, coughing, footsteps, clapping vs dogs, birds, insects | Google YAMNet, 521 sound classes (`models/yamnet.tflite`) |

## Fused verdict

| Verdict | Rule |
|---|---|
| HUMAN CONFIRMED (n/3) | two or more sensors report a human (score at least 0.5) |
| VISUAL ONLY: no heat signature | the camera sees a person but the thermal camera sees no warm body (a photo or screen) |
| WARM OBJECT: not confirmed | something is warm but nothing else says human (a mug, an engine, a warm rock) |
| POSSIBLE HUMAN (one sensor) | one sensor only, e.g. a voice heard while nothing is in view |
| ANIMAL: not human | an animal sound or animal in view, with no human evidence |
| NO HUMAN DETECTED | nothing above |

Positive readings are held for 1.5 s so the verdict doesn't flicker. Verdict changes go to
`~/fusion_logs/<session>/events.jsonl`, and all three camera views are saved there every 5 s
while a human is confirmed.

## Setup (once, with internet)

```
bash fusion/setup_fusion.sh
```

Installs OpenCV, Picamera2 and ALSA tools, creates `~/fusion-venv` with the offline audio runtime,
finds the USB microphone, and installs (but does not start) `fusion.service`.

## Run

```
sudo systemctl stop thermal        # the RTSP stream and this app can't share the thermal camera
~/fusion-venv/bin/python fusion/app.py --mic plughw:CARD=<card>,DEV=0
```

Open http://localhost:8080 on the Pi, or http://10.42.0.1:8080 from a phone or laptop on the
`THERMAL-PI` hotspot. Useful options:

- `--rgb usb --rgb-index 0` to use a USB webcam instead of the Camera Module 3
- `--thermal off`, `--rgb off`, `--audio off` to demo with fewer sensors
- `--thermal fake --rgb file --rgb-file x.jpg --audio file --audio-file x.wav` to test without hardware
- `--thermal-device /dev/video0 --thermal-reader ffmpeg` if thermal auto-detect picks the wrong node

Run on every boot instead of the RTSP stream: `sudo systemctl disable --now thermal; sudo systemctl enable --now fusion`

## Showcase script

Each case shows a different reason multiple sensors matter.

1. **A person walks in and talks.** All three agree: HUMAN CONFIRMED (3/3).
2. **Play a dog barking from a phone, nobody in view.** Sound says animal: ANIMAL, not human.
3. **Hold a hot drink or hand warmer in view.** Thermal sees heat, the camera sees a cup, nothing
   sounds human: WARM OBJECT, not confirmed.
4. **Hold up a photo of a person on a phone or laptop screen.** The camera sees a person, but there
   is no heat: VISUAL ONLY, no heat signature. A camera-only system would be fooled here.
5. **A person hidden behind a sheet or in the dark, calling for help.** The camera sees nothing,
   but heat and sound still confirm: HUMAN CONFIRMED (2/3).

## Notes and limits

- The Camera Module 3 needs light. At night the visual panel drops out and heat and sound carry
  the decision (a NoIR camera with an IR light would fix that).
- The warm-body detector cannot tell a person from a dog by heat alone; the person model, the
  camera, or sound has to confirm.
- The thermal person model (`models/thermal_person.onnx`) is trained on HIT-UAV drone imagery
  (people seen from 60-130 m). Without it, thermal reports warm bodies only.
- Licenses: YOLOv8 weights are AGPL-3.0 (Ultralytics). YAMNet is Apache-2.0 (Google).
  HIT-UAV is CC BY 4.0 (Suo et al., Scientific Data 2023).
