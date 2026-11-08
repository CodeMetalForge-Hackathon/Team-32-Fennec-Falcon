# Eagle starter kit

A perched listening node for cooperative search and rescue. The perch (stand-in for a bird-shaped
drone that lands, stops its motor and listens) hears a briefed whistle or tap code, measures its
direction with a microphone array, turns bearing + perch height into a position
(range = h / tan(depression)), optionally confirms the person by polling their radio tag, and sends
**one** short AES-GCM-encrypted LoRa burst at the lowest power that reaches the base ("Nest"). The
Nest decrypts it and puts a Cursor-on-Target marker in ATAK. Everything runs in pure simulation;
every hardware choice is in `config/example.yaml`.

```
apps/      eagle_node.py  nest_station.py  sim_demo.py  keygen.py  life_demo.py  life_node.py
           hailo_vision.py  enroll_target.py  fetch_models.py   (section 10)
eagle/     doa detect locate geo cot protocol airtime link sim pipeline node nest config audio_io
           camgeo thermal vitals sounds rfscan vision fusion lifesim   (signs of life, section 9)
           identity                                                     (section 10)
firmware/  eagle_radio/ (silent serial LoRa modem)   tag/ (the person's pellet tag)   radar_bridge/   mr60bha2_vitals/
config/    example.yaml (all settings + array presets)      tests/ (pytest, 249 tests)
```

## 1. Install (5 min)

Raspberry Pi OS (Bookworm) / Debian / Ubuntu:
```bash
sudo apt update && sudo apt install -y python3-venv python3-pip libportaudio2
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt matplotlib pytest
```
macOS (Homebrew Python): `brew install python` then the same venv + pip lines. PortAudio ships
inside the `sounddevice` wheel on macOS/Windows; on Linux install `libportaudio2`.
Serial ports: Linux `/dev/ttyUSB0` (add yourself to `dialout`), macOS `/dev/cu.usbserial-*` or
`/dev/cu.SLAB_USBtoUART`. Check everything: `python -m pytest -q` (~20 s on a laptop).

## 2. Pick a preset (`audio.preset`)

| Preset | Mics / rate | Geometry source | Status |
|---|---|---|---|
| `respeaker_usb_v2` | 4 raw of 6 ch, 16 kHz | ODAS cfg, radius 32 mm; Seeed wiki ch1-4 = raw mics | verified (needs 6-channel firmware) |
| `respeaker_xvf3800` | 4 raw of 6 ch, 16 kHz | AEC_MIC_ARRAY_GEO from the device, 66 mm square; Seeed wiki ch2-5 = raw mics | **channel order not verified: clap test** (needs 6-channel firmware) |
| `respeaker_6mic_hat` | 6 of 8 ch, 16 kHz (max 48) | ODAS cfg, radius 46.3 mm; ch0-5 mics | verified |
| `minidsp_uma16_v2` | 16 ch, 48 kHz | Acoular xml, 4x4 grid, 42 mm pitch, USB channel order | **partial**: which board edge is +x is not verified |
| `diy_tetra_4` / `diy_cross_4` | 4-input USB interface | placeholders | **not verified: measure** |

Best accuracy: UMA-16 (largest aperture) or the DIY tetrahedron (the only non-planar one, so it
measures elevation without the up/down ambiguity). Planar arrays need `doa.hemisphere: lower` and
lose elevation accuracy near the horizon; reverberant halls pull their elevation steeper (see
`doa.onset_s`). Two perches with bearings only (`fuse_bearings`) avoid elevation entirely.
Measuring a DIY array: mark mic 1 as the 0 deg direction, measure every capsule centre from one
reference point (x forward, y left, z up, metres, +-2 mm), list them in `channels` order.

## 3. Calibrate at the venue (10 min)

`cp config/example.yaml config/local.yaml`, then edit `local.yaml`:
1. **Height**: tape/laser the array height above the floor where people will be -> `perch.enu_m[2]`.
   Typical source height (mouth of a sitting/lying person) -> `site.source_height_m`.
2. **Direction**: point the array's 0 deg mark at a known direction; write its compass heading in
   `perch.heading_deg` (true north; phone compasses may show magnetic, ~14 deg W declination in Boston,
   check NOAA). Survey `site.origin` lat/lon (phone GPS is fine) for the ENU origin = perch base.
3. **Clap test**: `python apps/eagle_node.py --config config/local.yaml --clap-test`. Clap ~5 m away
   exactly on the 0 deg mark: set `perch.az_offset_deg` to the printed body az. Clap 90 deg to the
   left: expect ~90; if you get ~270 set `audio.mirror_y: true`. Put the printed std values into
   `doa.sigma_az_deg` / `doa.sigma_el_deg`.
4. **Range check**: whistle from a taped spot. If range is short in an echoey hall, try
   `doa.onset_s: 0.1`; fine-tune `doa.el_bias_deg`.

## 4. Radios

Arduino IDE 2: install the Espressif ESP32 board package and **RadioLib 7.x** (8.0 removes the
`begin()` form used here; the sketches stop with a clear `#error`). Heltec WiFi LoRa 32 V3 is the
default pin map; uncomment `#define BOARD_TBEAM_SX1262` for a LilyGO T-Beam SX1262 (its LoRa rail is
switched by the PMU: if `begin()` returns -2 enable it per LilyGO's examples).
* `firmware/eagle_radio` on the Eagle radio and the Nest radio. It only transmits on `TX` from the
  host (line protocol in `eagle/link.py`). Check: serial monitor at 115200 shows `READY eagle_radio 1`.
* `firmware/tag` on each tag: `python apps/keygen.py --tag-id 7 --out-dir keys` -> copy
  `keys/tag_keys.h` to `firmware/tag/`, merge `keys/eagle_keys.yaml` into Eagles' config and
  `keys/nest_keys.yaml` into the Nest's. Boot prints `SELFTEST challenge OK, covert mask OK, reply OK`.
  PRG button: short press toggles INJURED; hold 3 s arms DURESS (no LED, no visible change).
* Optional Meshtastic modem instead (`radio.transport: meshtastic`, `pip install meshtastic`): set the
  node role to `CLIENT_HIDDEN`, turn off position and telemetry broadcasts, use hop limit 0. TX power
  is then the node's fixed `lora.tx_power`, not per burst.

**RF rules.** US ISM band 902-928 MHz only (default 915.0 MHz). 47 CFR 15.247 expects either
frequency hopping (>= 50 channels if narrower than 250 kHz) or digital modulation with >= 500 kHz
6-dB bandwidth; a fixed 125 or 250 kHz channel (the default is SF7/250 kHz) is neither, so beyond a
brief low-power demo use 500 kHz
(`radio.bw_khz: 500` and `RADIO_BW_KHZ 500.0` in both `eagle_proto.h` copies) and ask the organizers.
Indoors at the venue, set `radio.max_dbm: -5` in `config/local.yaml` (and `nest.beacon_dbm: -5` if the
Nest beacons): with a 2-3 dBi antenna that stays
inside 47 CFR 15.249's low-power limit (50 mV/m at 3 m, about -1 dBm EIRP) and still crosses a hall
with ~55 dB of margin. Stay off emergency and public-safety frequencies; the SDR below is receive-only.

## 5. Run it

```bash
python apps/sim_demo.py                                   # full loop, no hardware; writes sim_floor.png
python apps/sim_demo.py --preset minidsp_uma16_v2 --code long_short_short --duress
python apps/sim_demo.py --save-wav /tmp/w.wav             # also save the synthetic 4-ch capture
# two terminals, no radios (frames over UDP on localhost):
python apps/nest_station.py --transport udp --print-cot --show-raw   # --show-raw: the bytes an eavesdropper gets
python apps/eagle_node.py --transport udp --wav /tmp/w.wav
# real hardware:
python apps/eagle_node.py --list-devices
python apps/eagle_node.py --config config/local.yaml --port /dev/ttyUSB0          # Eagle (Pi/Jetson)
python apps/nest_station.py --config config/local.yaml --port /dev/ttyUSB1 --atak 192.168.1.50
python -m eagle.airtime                                   # LoRa airtime table
```
ATAK: by default the Nest sends multicast SA to 239.2.3.1:6969 (ATAK's mesh SA default); `--atak IP`
adds unicast to the phone's UDP 4242 input (Settings > Network Connections > Manage Inputs: protocol
UDP, address 0.0.0.0, port 4242); `--tcp host:8087` streams plain TCP to a TAK server input
(FreeTAKServer's unencrypted CoT port; TLS 8089 is not implemented). Marker type `a-u-G` = acoustic
only, `a-f-G-U-C` = tag authenticated; change in `cot.*_type`.

**Enemy scanner view.** Any SDR (RTL-SDR, HackRF, Airspy) with SDR++ or GQRX, receive only, centre
915.0 MHz, span >= 1 MHz, waterfall on. A strong LoRa burst shows as diagonal chirps as wide as the LoRa bandwidth (250 kHz by default).
Show the judges `eagle_node.py ... --force-dbm 22` (demo-only fixed power) next to the default
adaptive power: the adaptive burst is decodable at the Nest yet stays under a distant listener's
noise floor (LoRa demodulates down to -7.5 dB SNR at SF7, -20 dB at SF12; an energy detector cannot).
This is a link-budget effect, not a guarantee: a listener closer than the Nest will see it.

Airtime (CR 4/5, preamble 8, explicit header, CRC on; `python -m eagle.airtime`):

| Setting | 20 B | 32 B | 48 B | 225 B |
|---|---|---|---|---|
| SF7/BW125 | 56.6 ms | 71.9 ms | 97.5 ms | 353.5 ms |
| SF7/BW250 | 28.3 ms | 36.0 ms | 48.8 ms | 176.8 ms |
| SF7/BW500 | 14.1 ms | 18.0 ms | 24.4 ms | 88.4 ms |
| SF9/BW125 | 185.3 ms | 246.8 ms | 308.2 ms | 1127.4 ms |
| SF12/BW125 | 1318.9 ms | 1810.4 ms | 2302.0 ms | 8036.4 ms |

## 6. Five-minute demo script

1. (30 s) Problem: an isolated person must be found without making them transmit, and the report
   must not light up an enemy's spectrum display. Show the perch, the tag, the ATAK phone.
2. (60 s) `python apps/sim_demo.py`: read out detection, bearing/range error, the single 48-byte
   burst at the lowest power that reaches the Nest, and the scanner lines (at the sim's outdoor
   distances "not seen"; at +22 dBm it would be "SEEN"). Show the PNG.
3. (90 s) Live: teammate whistles 3 times under the perch. Node logs DETECT -> FIX -> POLL -> TX;
   the ATAK marker is `a-f-G-U-C` when the tag answered the poll, `a-u-G` with the tag switched off.
4. (60 s) Duress: hold the tag button 3 s, whistle again. The Eagle log looks identical; only the
   Nest prints `*** DURESS ***`. Replay: the sim shows the recorded challenge being ignored.
5. (60 s) SDR waterfall: indoors at 15-20 m both bursts show, the adaptive one about 30 dB weaker and
   unreadable. "Not seen" needs the outdoor distances from step 2; say so rather than overclaim.

## 7. Troubleshooting

* No whistles detected: check input levels (`--record 10 raw.wav` and inspect), lower
  `detect.whistle.tonal_db` to 10, make sure whistles last > 0.15 s. Birds chirp: keep codes multi-part.
* Bearings rotate as you move around: wrong channel order or mirrored board -> clap test, `mirror_y`.
* Range far too short/long: height `perch.enu_m[2]` or `source_height_m` wrong; planar array in an
  echoey room (`doa.onset_s: 0.1`); elevation shallower than -2 deg is refused (bearing only).
* `radio did not answer`: wrong port, another program holds it, or the radio is not flashed;
  `READY` must appear at boot. `ERR -2` on boot = SX1262 not found (pins / T-Beam PMU rail).
* Nest says `FIX REJECTED`: different `fix_aes` keys, replayed frame, or clocks differ by more than
  `nest.max_age_s` (set 0 to disable). Never delete `eagle.state_file` while keeping the same keys.
* Tag silent: its `K_TAG` differs from the Eagle's `keys.tag_hmac` (bad MAC -> silence; `SELFTEST`
  checks the code, not your keys), the Eagle's counter is older than the tag remembers (raise it:
  `eagle_node.py --set-counter N`), or it is out of range.
* No ATAK marker: phone and Nest on the same Wi-Fi, multicast allowed (many venue networks block
  it -> use `--atak <phone IP>`), stale time not passed (`cot.stale_s`).

## 8. Protocol and security notes

Byte layouts (CHALLENGE 20 B, REPLY 21 B, FIX 48 B, BEACON 15 B, LIFE 44 + 10/person B) are
documented once, in the docstring of `eagle/protocol.py`; `firmware/*/eagle_proto.h` mirror them and
`tests/test_firmware_layout.py` / `test_firmware_host.py` (host-compiled sketches) fail on drift.
K_tag (tags+Eagles) authenticates CHALLENGE/REPLY with 64-bit HMAC-SHA256 tags; K_fix
(Eagles+Nest) encrypts FIX with AES-256-GCM (nonce = boot salt + monotonic counter + Eagle id);
K_covert (tags+Nest only) masks the REPLY status byte, which is how DURESS stays invisible to the
Eagle and to eavesdroppers. Replays are dropped by per-sender counter windows (tag: 32, persisted in
NVS; Nest: 64 + timestamp age). Power: reply = clamp(sensitivity + margin + (announced TX - RSSI)).
Known gaps: no ESP32 compile in this repo's CI (host build only); tag battery reads 255 (unknown);
Meshtastic transport untested on hardware; DEM must be supplied as a numpy grid; plain TCP only for
TAK servers; the example keys are public test keys. Accuracy limits seen in simulation: at shallow
depression (< ~12 deg) small elevation errors become large range errors (dr = h*dtheta/sin^2 theta),
and a tonal whistle plus a strong nearby reflection can bias the 4-mic arrays by several degrees;
use two perches (`fuse_bearings`), a bigger or non-planar array, or claps (broadband) when it matters.

## 9. Signs of life (multi-sensor, multi-person)

The acoustic kit finds people who can whistle. The life list also finds the silent ones and
triages everyone it sees, then sends the whole list home in one or two short encrypted bursts.

```bash
python apps/life_demo.py                          # the story below, no hardware; writes life_floor.png
python apps/life_demo.py --people 10 --seed 3     # random 3-12 m placements: count + position stats
python apps/life_node.py --no-radio               # live loop with whatever sensors are present (log only)
python apps/life_node.py --config config/local.yaml --modules thermal vitals rf --thermal mlx90640 \
       --vitals-port /dev/ttyACM0                 # live, LIFE bursts over radio.transport
python apps/nest_station.py --transport udp --print-cot          # bench pair, two terminals:
python apps/life_node.py --transport udp --modules thermal       #   synthetic thermal scene -> Nest
```

**Demo story** (`life_demo.py`, perch 3 m up). P1 is conscious: whistles the briefed code, then
talks, and carries tag 7 set to INJURED. P2 is unconscious: silent and warm, breathing 10/min,
with a radar pellet 1.3 m away. P3 is a warm decoy, a jacket with hand warmers, with a pellet
next to it. P4 is an allowlisted team radio with nobody there, and six bystanders' phones are
only counted. Simulated result (seed 0):

| Truth | Status reported | Why | Position error |
|---|---|---|---|
| P1 talker | RESPONSIVE (Nest adds INJURED) | whistle code + VOICE + tag reply | 0.01 m |
| P2 unconscious breather | BREATHING, 10/min, 62 bpm | heat + radar breathing/heartbeat | 0.17 m |
| P3 warm decoy | WARM_ONLY | heat, radar says no vital signs | 0.17 m |
| P4 radio, no person | RF_ONLY (ring around the perch) | BLE allowlist hit, no person on the ring | - |

The whole list is one 88-byte LIFE frame, 76.9 ms at SF7/BW250. The Nest makes one ATAK
marker per person (`eagle-1-p<track>`), `a-f-G-U-C` for the tag-authenticated one and
`a-u-G` for the rest, with status, evidence and rates in the remarks. `--people 10` over six
seeds: 47/47 persons found, no false tracks, 59/60 statuses right (the miss is two people on
one bearing; that sound is left unassigned rather than guessed), position error median
~0.08 m, max 0.19 m, two LIFE frames (124 + 64 B, 161.5 ms). These numbers come from
simulation, not from a measurement.

**What each sensor adds**

| Sensor (library parts) | Adds | Misses |
|---|---|---|
| Mic array (existing presets) | RESPONSIVE: briefed codes, voice, distress, cough, breath sounds, with a bearing (and a floor point when the depression allows) | silent people, two talkers on one bearing |
| Thermal camera: radiometric 160x120 over USB (PureThermal/Lepton class) best, MLX90640 32x24 on I2C usable close in | every warm body in view, floor position (~0.1-0.5 m in sim), count | cannot tell a warm decoy (or a body still warm) from a breather; blocked by cover and glass |
| mmWave vital-sign module or Doppler radar as a "pellet" dropped next to a warm body | BREATHING (and heart rate from a phase / I-Q / vendor module); "no vital signs" makes WARM_ONLY immediate | short range only; body motion masks it |
| PIR (on the radar bridge, `PIR <0|1>`) | MOVING | a still person |
| BLE scan (Pi Bluetooth, `bleak`) | allowlisted team devices as range rings | position; strangers' devices are only counted |
| Tag poll (existing) | authenticated identity; INJURED / DURESS / button, readable only by the Nest | people without tags |
| AI / NoIR camera + Hailo-8 (any person detector writing JSON lines) | upright people in light or IR, as floor points | people under debris, in the dark without IR |

The Pi 5 runs everything. Hailo-8 is only needed for the vision boxes: run any person
detector (Hailo's examples, Ultralytics) and append one line per frame
`{"t": .., "boxes": [[x1, y1, x2, y2, score], ...], "w": 640, "h": 480}` to the file in
`life.vision.jsonl`. Upright boxes use the feet (bottom centre) on the floor; lying boxes use
the centre at 0.15 m. SDRs, UWB and Wi-Fi are not used here (`rfscan.WifiProbeStub` explains
why).

**Wiring**
* MLX90640 on the Pi's I2C: 3V3 (pin 1), GND (pin 6), SDA = GPIO2 (pin 3), SCL = GPIO3 (pin 5),
  or a Grove I2C port. Run `sudo raspi-config`, then Interface Options, I2C, and
  `pip install adafruit-circuitpython-mlx90640`. `i2cdetect -y 1` should list the sensor
  (default address 0x33). Set `life.thermal.source: mlx90640` and `camera: {hfov_deg: 55, width: 32,
  height: 24}` (or `110` for the wide version), then check orientation by putting a hand in the
  top-left of the view and setting `flip_h` / `flip_v`.
* PureThermal on USB: it is a UVC camera (`v4l2-ctl --list-devices`). Use `source: uvc_y16`
  with `uvc.device`, which needs radiometric (TLinear) Y16 output in centi-Kelvin. If you only
  get 8-bit AGC frames, use `source: gray8` (relative units, `delta_c` in grey levels).
* Radar bridge: flash `firmware/radar_bridge/radar_bridge.ino` on the Arduino with the Grove
  shield. The Doppler IF goes through an amplifier to A0 (see the sketch header), and an
  optional PIR goes on D2. Set `life.vitals.port` to the board's serial port and
  `life.vitals.enu_m` to where that pellet lies. A vendor vital-sign module (for example Seeed
  MR60BHA2) runs its own example sketch plus one `VITALS ...` print line, as described in the
  sketch comments.
* Seeed MR60BHA2 on its XIAO ESP32C6: flash `firmware/mr60bha2_vitals/mr60bha2_vitals.ino`
  (needs the Seeed Arduino mmWave library as a ZIP install; follows Seeed's "Breath Module"
  example; not compiled here). It prints `VITALS <breath> <heart> <range_m> <presence>` over
  USB at 115200, which `life.vitals.port` reads directly. Flashing it replaces the kit's
  pre-loaded ESPHome firmware.
* Mount and measure: the thermal camera's height (`camera.mount.enu_m`, default = the perch),
  heading and tilt drive every floor position. A 1 deg tilt error at 10 m range from 3 m up
  moves the point by ~0.6 m.

**Honest limits**
* **Radar range.** Consumer mmWave vital-sign modules measure breathing and heart rate only at
  short range, so a pellet must lie next to the person. Check the datasheet of the module you
  get. For example, Seeed's MR60BHA2 wiki states 1.5 m for breathing/heartbeat detection and up
  to 6 m for static presence. A single-channel Doppler IF (`RADAR` lines) gives breathing only,
  and at an unlucky distance it can read double; the host never reports heart rate from it.
  Simulated accuracy is ±1/min breathing (6-30/min) and ±3 bpm heart (50-120 bpm) at good SNR,
  but only when the heart rate is not within ~5 bpm of a breathing harmonic.
* **Thermal resolution against range** (perch 3 m, pinhole model, one pixel's floor footprint and
  pixels on a lying adult of ~0.6 m^2):

  | Camera | 3 m | 5 m | 8 m | 10 m | 12 m |
  |---|---|---|---|---|---|
  | 160x120, 57 deg | 3x4 cm, ~540 px | 4x8 cm, ~200 px | 6x16 cm, ~70 px | 7x23 cm, ~40 px | 8x31 cm, ~25 px |
  | MLX90640, 55 deg | 13x18 cm, ~25 px | 19x38 cm, ~8 px | 27x80 cm, ~3 px | 33x116 cm, ~2 px | ~1 px |
  | MLX90640, 110 deg | 36x51 cm, ~3 px | ~1 px | <1 px | <1 px | <1 px |

  The detector needs >= 2 pixels, so a 55 deg MLX90640 is good to ~8 m and the 110 deg one only
  close in. Bodies closer than ~1 m (more at long range) merge into one blob and are counted as
  one person. The centroid of a sitting person biases the floor point away from the camera
  (~1 m at 10 m); the covariance covers it. A warm decoy passes the thermal stage by design,
  and only the radar or a timeout marks it WARM_ONLY. A WARM_ONLY track may still be an
  unconscious person whose breathing nobody has checked, so send someone to look.
* **Sounds.** The fallback classifier is a heuristic, not ML. It scored 94-100 % on clean
  synthetic clips in our runs (test threshold 80 %) and has not been validated on real recordings. The kit's
  whistle/tap detectors also fire on speech, so briefed-code matches inside VOICE events are
  ignored. Two people on one bearing leave a bearing-only sound unassigned (counted, not guessed).
* **BLE privacy rule.** Only devices on `life.rf.allowlist` (exact address or name substring)
  are reported, logged or sent. Every other advertisement becomes one anonymous number per scan,
  so no address, name, RSSI or hash is ever kept (`tests/test_rfscan.py` checks logs, results,
  scanner state and the life list). RSSI range carries a ~70 % 1-sigma, so a ring is attached
  only when it holds exactly one track. Otherwise the device is an RF_ONLY entry at the perch.
* **Not verified on hardware.** None of the section 9 hardware paths ran on real devices here
  (MLX90640 via Adafruit's library API, UVC Y16 via OpenCV, `bleak`, serial bridge). The bridge
  sketch only compiled and ran against a host stub.
* **Radio.** LIFE uses the same AES-256-GCM key, counter and replay window as FIX. A frame is
  <= 128 B (44 + 10 per person, +4 per tagged person, 8 untagged persons per frame), split
  across frames for longer lists: 10 people = 2 frames, ~162 ms at SF7/BW250 (88 B: 76.9 ms;
  128 B: 107.6 ms; at 500 kHz half of that). The FIX path is unchanged.

**Model downloads (YAMNet, optional)**
* `pip install ai-edge-litert` (or `tflite-runtime` / `tensorflow`), then set
  `life.sounds.yamnet_model` to a YAMNet `.tflite`. MediaPipe's Audio Classifier page
  (https://developers.google.com/edge/mediapipe/solutions/audio/audio_classifier) lists
  `https://storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/latest/yamnet.tflite`
  (input 1 x 15600 samples at 16 kHz, 521 classes). That URL was checked on the documentation page
  on 2026-11-07, but the file was not downloaded or run here. Test it with
  `EAGLE_YAMNET_MODEL=/path/yamnet.tflite python -m pytest tests/test_sounds.py`.
* Class names: `eagle/data/yamnet_class_map.csv`, vendored unchanged from tensorflow/models
  (Apache-2.0, see `eagle/data/NOTICE.md`). The category mapping (`eagle.sounds.YAMNET_MAP`) uses
  its exact display names, and a test checks every one.

## 10. Search-and-rescue drone: HT-203U, XVF3800, Hailo AI HAT+, missing-person identity

Hardware: Raspberry Pi 5 + Hailo AI HAT+, HTI HT-203U thermal camera (USB), reSpeaker XVF3800
(USB, 6-channel firmware), Camera Module 3 Wide NoIR. Everything runs offline after one setup pass.

**Once, with internet** (Pi; the OSNet export can run on a laptop):
```bash
sudo bash ../pi/setup_pi.sh                       # uvcvideo quirks=0x2 for the HT-203U, ffmpeg, OpenCV
# hailo-apps: https://github.com/hailo-ai/hailo-apps (its install.sh sets up HailoRT + the venv)
pip install onnxruntime insightface               # identity: face (CPU) + OSNet runtime
python apps/fetch_models.py --arch auto --list    # ~800 MB for hailo8l, ~610 MB for hailo8
python apps/fetch_models.py --arch auto           # verified downloads -> ~/eagle-models + models.lock.json
python apps/fetch_models.py --only osnet --export-osnet --arch hailo8l   # needs torch, onnx, pandas
python apps/fetch_models.py --verify              # offline re-check, any time
```

**Config** (`config/local.yaml`): `audio.preset: respeaker_xvf3800`, `life.thermal.source: hti` with
`life.thermal.camera` = 256 x 192, `hfov_deg: 50.0`; the vision camera defaults already describe the
Camera Module 3 Wide (102 x 67 deg, 1280x720). Then the clap test (section 3): the XVF3800 channel
order is **not verified** until it passes.

**Enroll the missing person** (on the Pi, same models as runtime):
```bash
python apps/enroll_target.py --photos ~/target_photos --description "red rain jacket, blue jeans"     --out ~/eagle/target_profile.npz          # logs and skips photos without a usable face
```

**Run** (two processes):
```bash
source ~/hailo-apps/setup_env.sh
python apps/hailo_vision.py --jsonl ~/eagle/vision.jsonl --profile ~/eagle/target_profile.npz
python apps/life_node.py --config config/local.yaml      # life.vision.jsonl: ~/eagle/vision.jsonl
```

Identity levels: 0 none, 1 appearance candidate, 2 strong appearance, 3 face verified (ArcFace only
on faces >= 40 px wide). Thresholds are under `identity:` in the config; calibrate them with a
volunteer. They travel in the FIX class byte bits 5-6 (layout unchanged, see `eagle/protocol.py`)
and show in the Nest's CoT remarks. In the life list identity is additive only: it raises a
track's score and its rank within its status tier, and never creates, moves, re-classifies,
suppresses or delays a track (`tests/test_fusion.py` checks this on random scenes).

Licences of the fetched weights: `eagle/data/NOTICE.md`. **InsightFace buffalo_l weights are for
non-commercial research only**; the OSNet weights are trained on MSMT17 (research terms).

## Sources

* ReSpeaker USB array geometry: https://github.com/introlab/odas/blob/master/config/odaslive/respeaker_usb_4_mic_array.cfg
* ReSpeaker 6-mic geometry: https://github.com/introlab/odas/blob/master/config/odaslive/respeaker_6_mic_array.cfg
* ReSpeaker Mic Array v2.0 (channels, 16 kHz, firmware): https://wiki.seeedstudio.com/ReSpeaker_Mic_Array_v2.0/
* ReSpeaker 6-Mic kit (8 ch, 48 kHz max): https://wiki.seeedstudio.com/ReSpeaker_6-Mic_Circular_Array_kit_for_Raspberry_Pi/
* miniDSP UMA-16 v2 (URA, 48 kHz, UAC2): https://www.minidsp.com/products/usb-audio-interface/uma-16-microphone-array
* UMA-16 positions: https://github.com/acoular/acoular/blob/master/acoular/xml/minidsp_uma-16.xml
* PyTAK defaults (udp+wo://239.2.3.1:6969, tls 8089): https://pytak.readthedocs.io/en/latest/configuration/
* ATAK UDP input 0.0.0.0:4242: https://docs.deltaquad.com/gov/setup/ground-control-station-gcs/atak-setup
* Multicast 239.2.3.1:6969 and UDP 4242 examples: https://projectqai.github.io/docs/integration/atak
* FreeTAKServer ports (8087 TCP, 8089 SSL): https://www.mintlify.com/FreeTAKTeam/FreeTakServer/configuration/services
* CoT event/point attributes (ce = 1 sigma): https://babeloff.github.io/FreeTAKServer-User-Docs/About/architecture/cot_domain
* Meshtastic roles (CLIENT_HIDDEN): https://meshtastic.org/docs/configuration/radio/device/
* Meshtastic Python sendData: https://python.meshtastic.org/mesh_interface.html
* RadioLib SX1262 API (-9..22 dBm): https://jgromes.github.io/RadioLib/class_s_x1262.html
* RadioLib releases (7.7 begin() change, 8.0 removal): https://github.com/jgromes/RadioLib/releases
* Heltec V3 pins: https://github.com/meshtastic/firmware/blob/master/variants/esp32s3/heltec_v3/variant.h
* T-Beam SX1262 pins: https://github.com/meshtastic/firmware/blob/master/variants/esp32/tbeam/variant.h and https://raw.githubusercontent.com/meshcore-dev/MeshCore/main/variants/lilygo_tbeam_SX1262/platformio.ini
* T-Beam LoRa power rail note: https://wiki.lilygo.cc/products/t-beam-series/t-beam/quick-start.html
* Semtech AN1200.13 LoRa Modem Designer's Guide (airtime/sensitivity tables; mirror): https://dtsheet.com/doc/1354366/sx1272-3-6-7-8--lora-modem-designer-s-guide-an1200
* TTN airtime examples: https://www.thethingsnetwork.org/forum/t/fair-use-policy-explained/1300
* 47 CFR 15.247: https://www.law.cornell.edu/cfr/text/47/15.247
* sounddevice install: https://python-sounddevice.readthedocs.io/en/latest/installation.html
* MediaPipe Audio Classifier (YAMNet .tflite URL, 1 x 15600 input, 521 classes): https://developers.google.com/edge/mediapipe/solutions/audio/audio_classifier
* YAMNet class map (vendored, Apache-2.0): https://github.com/tensorflow/models/blob/master/research/audioset/yamnet/yamnet_class_map.csv
* Adafruit MLX90640 example (I2C setup, getFrame, RefreshRate): https://github.com/adafruit/Adafruit_CircuitPython_MLX90640/blob/main/examples/mlx90640_simpletest.py
* bleak BleakScanner.discover(return_adv=True): https://github.com/hbldh/bleak/blob/develop/bleak/__init__.py
* Seeed MR60BHA2 (1.5 m breathing/heartbeat, 6 m presence, Seeed Arduino mmWave library): https://wiki.seeedstudio.com/getting_started_with_mr60bha2_mmwave_kit/
* Pointers not reviewed for this kit: SDR++ https://www.sdrpp.org/ , GQRX https://gqrx.dk/ ,
  NOAA declination https://www.ngdc.noaa.gov/geomag/calculators/magcalc.shtml
