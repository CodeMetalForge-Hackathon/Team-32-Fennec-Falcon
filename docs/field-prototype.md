# Field prototype: the Eagle's onboard computer

The hardware prototype runs the Eagle's perch-and-observe stage on real sensors: detect a human, identify whether it is the person being sought, and produce one short encrypted report. Everything runs on the device with no internet, cell service or cloud.

## Hardware

| Part | Role |
|---|---|
| Raspberry Pi 5, 16 GB | Onboard computer |
| Raspberry Pi AI HAT+ (Hailo-8, 26 TOPS) | Person detection on camera frames |
| HTI HT-203U thermal imager (USB, 256 × 192) | Body heat |
| Camera Module 3, standard lens (66° HFOV); Camera Module 3 NoIR Wide (102°) supported | People and faces |
| reSpeaker XVF3800 4-mic array (USB, 16 kHz, 6-channel firmware) | Human sounds and direction |
| Adafruit Feather RFM69HCW 915 MHz (in progress) | Radio link to the Nest |

For the demonstration the payload is mounted stationary in a model falcon, matching the mission's perched observation with propulsion off.

## Software

```
eagle-vision@standard  hailo_vision.py  camera -> person boxes (Hailo-8) -> face match vs Operator Profile card
eagle-life             life_node.py     thermal + microphones + vision -> fusion -> life list -> 48-byte FIX
eagle-nest             nest_station.py  decrypts FIX (AES-256-GCM), Cursor-on-Target for ATAK
eagle-demo             demo.py          full-screen demo view, http://<pi>:8081
eagle-dashboard        dashboard.py     phone control page, http://<pi>:8080
eagle-hotspot                           THERMAL-PI Wi-Fi hotspot at boot when no known network is found
```

- **Fusion** (`eagle-kit/eagle/fusion.py`) gates each new piece of evidence (heat, person box, sound, identity) against existing tracks, so a person seen by the camera and heard by the microphones becomes one track with a status (RESPONSIVE, BREATHING, MOVING, WARM_ONLY, UNKNOWN) and a life score.
- **Thermal**: warm regions are detected relative to the local background (not an absolute threshold), so the imager's offset and changing ambient temperature do not break detection.
- **Sound**: YAMNet classes are grouped into VOICE, DISTRESS, BREATH, COUGH, SIGNAL (whistle, knock) and MOVE.
- **Identity**: see [operator-profile-card.md](operator-profile-card.md).
- **Message**: one 48-byte FIX (position, error radius, class and match level, confidence, status, time), AES-256-GCM with replay protection, sent at the lowest power that reaches the Nest. In the demonstration the Nest runs on the same Pi over loopback; the Feather radio link is the next step.
- **Keys** are generated on the device (`apps/keygen.py`) and kept outside the repository. The repository contains only labelled test keys.

## Operation

1. Power on. All services start automatically; nothing needs a network.
2. On a phone, join **THERMAL-PI** and open `http://10.42.0.1:8081` (demo view) or `:8080` (control page).
3. The control page enrols the Operator Profile card, sets the site (mount height, heading, tilt, latitude/longitude), runs the microphone clap calibration, starts and stops recording, and shuts down.
4. The demo view shows the verdict banner, camera and thermal side by side, the sound spectrogram and level, sensor status, and the last decrypted message. Any panel enlarges on click. A **REC** button records the screen to `~/Videos`, downloadable from `/videos`.

Install on a fresh Pi: `bash pi/field/install.sh` after `eagle-kit/apps/fetch_models.py` has downloaded the models once. Field checklist: [pi/field/FIELD_TEST.md](../pi/field/FIELD_TEST.md).

## Verification

| Check | Result |
|---|---|
| Unit and integration tests (`cd eagle-kit && python -m pytest -q`) | 303 passing, hardware-free |
| All three sensors live together on the Pi, offline | Working (indoor, 2026-11-08) |
| Encrypted FIX decrypted at the Nest | Working over loopback |
| Operator Profile enrolment and face verification | Working indoors; see the card document for measured values |
| Thermal camera offset | Reads about 29 °C indoors; handled by background-relative detection |
| Microphone direction | Requires the clap calibration at each site |

## Known issues and lessons

- **Camera ribbon cables** came loose repeatedly when the payload was moved; one camera port gave timeouts with two different cameras. Cables are now taped at both ends; the remaining port is the known-good one.
- **Hailo-8 dropout**: the accelerator once dropped off the PCIe bus while appearance matching (CLIP) ran inside the vision process. CLIP is off by default; face matching runs on the CPU in a background thread.
- **Kernel update**: an update to Linux 6.18 coincided with camera trouble; rolling back to 6.12 broke the Hailo driver (version mismatch) and did not fix the cameras, which turned out to be cabling. The Pi stays on 6.18.60.
- **Power**: the Pi 5 needs a 5 V 5 A USB-C supply for the camera, thermal imager, microphones and Hailo together; weaker supplies limit USB current.
