# Eagle starter kit

Technical reference for [`eagle-kit/`](../eagle-kit/). Installation, wiring, calibration, the
five-minute demo script and troubleshooting are in [`eagle-kit/README.md`](../eagle-kit/README.md).
All results below come from simulation (**modelled**). No part of the kit has run on real radios,
microphones or cameras yet.

## What it is

Python software for the Eagle's perch computer (a Raspberry Pi or Jetson) and for the
coordinator's base station, the **Nest**, plus Arduino sketches for the radios. The perch hears a
briefed whistle or tap code, measures its direction with a microphone array, and turns the bearing
and the perch height into a position. It can confirm the person by polling a tag, then sends
**one** short encrypted LoRa burst at the lowest power that reaches the Nest. The Nest decrypts it
and puts a Cursor-on-Target marker on ATAK.

Version 2 adds **signs of life**: a status for each of up to ten people, from heat, sound, radar,
radio and vision, sent home in one or two short bursts.

## Files

| Path | What |
|---|---|
| `eagle/` | The library. Find loop: `detect` (whistle and tap codes), `doa` (direction), `locate` and `geo` (position), `protocol` (messages and cryptography), `airtime`, `link` (radio transports), `node` and `pipeline` (the perch loop), `nest` and `cot` (base station and ATAK), `sim` (synthetic audio and radio). Signs of life: `thermal`, `camgeo`, `vitals`, `sounds`, `rfscan`, `vision`, `fusion`, `lifesim` |
| `apps/` | `eagle_node.py` (perch), `nest_station.py` (Nest), `sim_demo.py` and `life_demo.py` (full loops with no hardware), `life_node.py` (live signs of life), `keygen.py` |
| `firmware/` | `eagle_radio` (silent serial LoRa modem for the Eagle and the Nest), `tag` (the person's tag), `radar_bridge`, `mr60bha2_vitals`. ESP32 and Arduino, RadioLib 7.x |
| `config/example.yaml` | Every setting, with microphone-array presets |
| `tests/` | 184 pytest tests, including the firmware compiled for the host against stub libraries |

## The find loop

1. **Detect** (`detect.py`): match the briefed code in the audio, for example three whistles or
   long-short-short. A code is reported after 1.5 s of quiet, so longer codes can finish.
2. **Direction** (`doa.py`): GCC-PHAT time differences across the microphone array give azimuth
   and elevation. Presets cover the ReSpeaker USB and 6-mic arrays, the miniDSP UMA-16 and DIY
   4-mic arrays.
3. **Position** (`locate.py`, `geo.py`): range = perch height / tan(depression angle), then local
   east-north-up to latitude and longitude. Bearings from two perches can be crossed instead
   (`fuse_bearings`).
4. **Tag poll, optional** (`protocol.py`): one CHALLENGE and one REPLY, each at the lowest power
   that gets through.
5. **Report** (`node.py`, `link.py`): one FIX, encrypted with AES-256-GCM, at the lowest power that
   reaches the Nest, measured from the Nest's beacon.
6. **Map** (`nest.py`, `cot.py`): Cursor-on-Target to ATAK by multicast 239.2.3.1:6969, unicast
   UDP 4242 or TCP 8087. Type `a-u-G` for a find by sound only, `a-f-G-U-C` when the tag answered.

## Messages

The byte layouts are defined once, in the docstring of
[`eagle/protocol.py`](../eagle-kit/eagle/protocol.py). The firmware headers mirror them, and the
tests fail if they drift. Airtime at SF7, 250 kHz, coding rate 4/5:

| Message | From → to | Size | Protection | Airtime |
|---|---|---|---|---|
| BEACON | Nest → Eagles | 15 B | HMAC-SHA256, 64-bit tag | 23.2 ms |
| CHALLENGE | Eagle → tag | 20 B | HMAC-SHA256, 64-bit tag (K_tag) | 28.3 ms |
| REPLY | tag → Eagle | 21 B | HMAC-SHA256, 64-bit tag (K_tag); status byte masked (K_covert) | 28.3 ms |
| FIX | Eagle → Nest | 48 B | AES-256-GCM (K_fix) | 48.8 ms |
| LIFE | Eagle → Nest | 44 B + 10 per person (+4 if tagged), at most 128 B per frame | AES-256-GCM (K_fix) | 76.9 ms for 88 B, 107.6 ms for 128 B |

**Keys.** K_tag (tags and Eagles) authenticates the poll. K_fix (Eagles and the Nest) encrypts
FIX and LIFE; the nonce is a per-boot salt, a monotonic counter and the Eagle's ID. K_covert (tags
and the Nest only) masks the REPLY status byte, so a duress answer looks exactly like a normal one
to the Eagle and to anyone listening; only the Nest can read it. Replays are dropped by a counter
window per sender: 32 on the tag (kept in flash), 64 plus a timestamp age at the Nest.
`apps/keygen.py` makes a fresh set. The keys in `config/example.yaml` and
`firmware/tag/tag_keys.h` are public test keys.

**Power control.** Path loss = the transmit power announced in a received frame minus its RSSI.
Reply power = clamp(sensitivity + margin + path loss). In simulation the FIX goes out at +7 dBm and
a receiver 1.6 km from the perch does not see it; the same FIX at +22 dBm would be seen. That is a
link-budget effect, not a guarantee: a listener closer than the Nest will hear it.

## Signs of life (version 2)

| Status | Meaning |
|---|---|
| RESPONSIVE | Briefed code, voice or a tag reply |
| BREATHING | Warm body with breathing measured by a radar pellet (and heart rate when the module gives it) |
| MOVING | Motion (PIR sensor) |
| WARM_ONLY | Warm body, but the radar found no vital signs or nothing confirmed life within 30 s. Still send someone to look |
| RF_ONLY | Only an allowlisted team radio, shown as a range ring |
| UNKNOWN | Not enough evidence |

`fusion.py` builds tracks from thermal blobs placed on the ground (`thermal.py`, `camgeo.py`),
sound bearings (`sounds.py`: YAMNet if a model file is present, otherwise a heuristic), radar
vitals (`vitals.py`), a Bluetooth scan (`rfscan.py`) and person boxes from any detector
(`vision.py`, for example on a Hailo-8).

**Bluetooth privacy rule.** Only devices on the allowlist are reported, logged or sent. Every other
device becomes one anonymous count per scan. `tests/test_rfscan.py` checks that no address, name,
RSSI or hash of a bystander's device is kept anywhere.

## Results (modelled)

- **Find loop** (`sim_demo.py`: ReSpeaker USB array 8 m up, three whistles at 15 dB SNR, person
  16.6 m away): bearing error 0.1°, elevation error 1.3°, position error 0.94 m; 1.15 m when
  crossed with a second perch. One 48-byte FIX at +7 dBm, 48.8 ms on air. A replayed challenge
  sent at +22 dBm is ignored by the tag.
- **Signs of life** (`life_demo.py`, perch 3 m up): a conscious talker, an unconscious breather, a
  warm decoy and an unattended team radio all get the right status (4 of 4), with positions within
  0.17 m. The whole list is one 88-byte LIFE frame, 76.9 ms on air.
- **Ten people, six random layouts:** 47 of 47 people tracked, 59 of 60 statuses right, no false
  tracks. Ten people fit in two LIFE frames, 161.5 ms in total.

## Verification

- `python -m pytest -q` in `eagle-kit/`: 183 passed, 1 skipped (the skipped test needs a YAMNet
  model file), about 26 s, with the optional `pyroomacoustics` and `pymap3d` installed. Without
  them, their two tests skip as well.
- The firmware sketches are compiled and run on the host against stub libraries
  (`tests/firmware_host/`), and `tests/test_firmware_layout.py` checks their byte layouts against
  `protocol.py`.
- **Not verified on hardware:** live audio, real radios, the ESP32 toolchain, TAK over TLS, the
  MLX90640 and PureThermal cameras, the Bluetooth scan, the radar serial link, YAMNet inference and
  the MR60BHA2 sketch.

## Limitations

- At shallow depression angles (below about 12°), small elevation errors become large range
  errors. Use two perches or a larger, non-planar array.
- A tonal whistle plus a strong nearby reflection can bias a 4-mic array by several degrees.
- Bodies closer than about 1 m merge into one thermal blob. Two people on one bearing leave a
  sound unassigned rather than guessed.
- Consumer radar vital-sign modules work only within about 1.5 m, so a pellet must land next to
  the person.
- A fixed 125 or 250 kHz LoRa channel does not meet 47 CFR 15.247 beyond a brief low-power demo.
  Use 500 kHz and check with the organisers (README, section 4).
- The tag mode assumes the person carries a tag. When the person carries nothing, as in
  [simulator v2](simulator-v2.md), only heat and sound apply.
