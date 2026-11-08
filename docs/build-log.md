# Build log

Every build gets an entry here: what we built, why, how it works, what we decided and why, how we checked it, and what is still open. Newest first. The template is at the bottom and in [how-we-document.md](how-we-document.md).

---

## 2026-11-07 · Pi runtime v1: on-board decision gate and thermal model on Hailo or CPU

**Files:** [`eagle-kit/apps/eagle_runtime.py`](../eagle-kit/apps/eagle_runtime.py), [`eagle-kit/eagle/decide.py`](../eagle-kit/eagle/decide.py), [`eagle-kit/eagle/infer.py`](../eagle-kit/eagle/infer.py), [`eagle-kit/eagle/scenes.py`](../eagle-kit/eagle/scenes.py), [`eagle-kit/apps/replay_decisions.py`](../eagle-kit/apps/replay_decisions.py), [`eagle-kit/apps/export_thermal_calib.py`](../eagle-kit/apps/export_thermal_calib.py), [`pi/field/eagle-runtime.service`](../pi/field/eagle-runtime.service), [`pi/field/run_runtime.sh`](../pi/field/run_runtime.sh), [`pi/field/use_runtime.sh`](../pi/field/use_runtime.sh) · **Docs:** [pi-runtime.md](pi-runtime.md)

### What
One process for the Eagle's Pi 5 + Hailo AI HAT+: every sensor of the kit, the thermal person model (Hailo HEF when compiled, else the CPU), the life list, and a decision engine that keeps P(him) per track and transmits nothing until one track passes every gate. Then one 48-byte FIX goes out as two bursts of the same sealed frame. It runs as `eagle-runtime.service` in Alex's field setup (in place of `eagle-life`), records with his recorder, shows in his field page's Verdict card, and comes with a replay tool for calibration and six synthetic scenes for bench tests.

### Why
The kit transmitted on detection (`eagle_node.py`) or every 30 s (`life_node.py`), and the simulator v2 evidence gate existed only in JavaScript. A perched Eagle near an isolated person must stay silent until it is sure, and must be sure for the right reasons.

### How it works
- `eagle/decide.py`: prior 1 / (1 + people expected); one likelihood ratio per clue (thermal shape, camera box, identity level, briefed code, coughs, radar, alone / group, still, moving, search area). SEND needs P >= 0.95 (strict 0.99), the briefed-code rule or 2 sensor modalities, 20 s dwell, a fix, and EMCON (nobody unknown inside the burst's intercept radius, no briefed threat inside it); otherwise HOLD and ask to relocate.
- `eagle/infer.py`: Alex's `thermal_person.onnx` through hailo-apps' `HailoInfer` (HEF), onnxruntime or OpenCV, with the preprocessing of `fusion/app.py`; boxes score the warm blobs they cover.
- `apps/eagle_runtime.py` subclasses `LifeNode`: same sensors, recorder and `--require`, one life-list update per sensor, the gate instead of LIFE bursts, training / default / strict modes, a refusal to transmit with the public test keys. Decisions go into `events.jsonl`; the field dashboard shows them.
- `apps/replay_decisions.py` re-runs any recording (runtime or life_node) with other settings; `apps/export_thermal_calib.py` turns recorded thermal frames into calibration images for the Hailo compiler.
- `pi/field/use_runtime.sh` swaps `eagle-life` for `eagle-runtime` (self-test first) and back.

### Key results (modelled, synthetic scenes)
- **Briefed code + warm body:** SEND at the third tap, P 0.990, false-alarm risk LOW.
- **Coughs only:** SEND at 80 min, P 0.970, MEDIUM; same as simulator v2.
- **Unconscious, thermal only:** never sends; P 0.923 after 2.5 h (simulator v2: 0.92).
- **Patrol of three:** P <= 0.13. **Stranger 10 m from the perch:** HOLD with REQUEST_RELOCATE; default mode sends after 15 min.
- **Intercept radius** of a perch burst: ~1,100 m at the planned 8 dBm, ~300 m at -9 dBm (log-distance n = 3, listener -115 dBm): anyone the Eagle can see can hear it.
- **False reports from the code rule:** one per 1,350 / 50 days / 32 h at 2 / 6 / 20 detector false alarms per hour (Poisson; scales with the cube).
- **Thermal model** (measured, desktop CPU, synthetic frames): 0.55 on aerial-style figures, 0.002 on a perch-view lying body. Trained on aerial footage; needs perch data.

### Decisions and trade-offs
| Decision | Why | Cost |
|---|---|---|
| Subclass `LifeNode`, do not edit it | Reuse every driver, the recorder and `--require`; no conflict with work on `life_node.py` | Changes inside `LifeNode.step` must be mirrored |
| Corroboration rule on top of P | One detector's false alarm cannot report alone | An unconscious person seen only by thermal is never reported without a second sensor |
| EMCON radius from the planned power | Same model the radio uses for power control | No terrain yet; conservative in the open, may be optimistic on a ridge |
| Thermal model on the CPU until a HEF exists | NPU carries the camera models; 0.5 frames/s is enough | Compile step on an x86 PC |
| Keep the 48-byte FIX | Nest, firmware, replay window work today | 48.8 ms at SF7/BW250 instead of a shorter packet |

### Verification
320 tests pass, 4 skip (41 new), on top of `sar-integration` 4fbee88. Simulator v2 cases reproduced; end to end over UDP the Nest decrypts the FIX and drops the second burst as a replay; record then replay reproduces the SEND. See [pi-runtime.md](pi-runtime.md#verification).

### Open issues / next
Not run on the Pi, the Hailo or the sensors yet; every likelihood ratio is assumed; HEF compile unverified; relocation is a flag file (no autopilot hook); no terrain in the intercept radius.

---

## 2026-11-07 · Eagle Jungle Simulator v2: the person carries nothing

**Files:** [`simulator/v2/`](../simulator/v2/) · **Docs:** [simulator-v2.md](simulator-v2.md), [simulator/v2/unreal/README.md](../simulator/v2/unreal/README.md)

![Simulator v2 at 02:00, mission running: the Eagle cam, the evidence chart and the four ways out](images/sim-v2-mission-night.jpg)

### What
A second version of the simulator in which the isolated person carries nothing that transmits. The Eagle finds him with its thermal camera (primary) and microphone array (secondary). An onboard evidence gate decides when it is sure it has found him, and the model picks the safest of four ways to get one 48-byte report out: a burst from the perch, a whisper to the relay overhead, flying 3 km before transmitting, or carrying the fix out. The page shows this as input, processing and output panels over a 3D jungle with a ridge outpost, patrols, local farmers and wildlife, plays a mission for each way out, and exports the scene for Unreal Engine.

### Why
Version 1 assumes a tag. In the case we design for, the person may carry nothing that works, and every transmission near him is a risk. The mentors' recurring question was what evidence justifies claiming he has been found before anything is sent. Version 2 makes that claim explicit, as a probability that must reach 0.95, and makes the risk of every emission visible.

### How it works
- `model.js` is one pure function with no dependencies: heat contrast and canopy line of sight; sound levels and foliage loss against ambient noise; a Bayesian evidence gate (prior odds × one likelihood ratio per clue); four ways out, scored on latency, where the emission happens and how far away it can be heard; position error; search time.
- Radio loss: plane-earth plus Weissberger through the trees; ITU-R P.833-10 with one end above the canopy.
- `index.src.html` is the page; `build.js` inlines the model, so the numbers on screen are the model's.
- **Export to Unreal** writes the scene as JSON; `unreal/ue_import_scene.py` places it in an Unreal Engine 5 level.

Full detail: [simulator-v2.md](simulator-v2.md).

### Key results (modelled)
- **02:00 night, injured, taps the briefed code:** TRANSMIT at 10 min (P 0.99), flies 3 km and bursts, ATAK marker at 14.5 min, fix error 10.1 m.
- **A burst from the perch would be heard 246 m away**, past the patrol 230 m from the perch.
- **Silent, only coughs:** 80 min to decide. **Unconscious:** the gate holds at P 0.92 and sends nothing, unless a radar pellet confirms breathing (120 min).
- **14:00 cicada chorus:** heat fails (-0.7 °C contrast against a 2.3 °C threshold), the tap code is heard to 26 m instead of 94 m, and the search needs 13 times as many perch stops.
- **Error budget:** 10 of the 10.1 m is the Eagle's own position without GPS; with a magnetometer alone it is 30.1 m, missing the 30 m target.

### Decisions and trade-offs
| Decision | Why | Cost |
|---|---|---|
| No tag: heat and sound only | The realistic worst case; nothing on him can give him away | Slower, less certain decisions; an unconscious, silent person may never be reported |
| A probability gate (P ≥ 0.95) before any emission | Answers "how do you know it's him?" with a number and its evidence | The likelihood ratios are assumptions until tuned on recorded data |
| Never transmit from the perch by default | The burst is heard beyond the nearest patrol | 4.5 min more to the marker |
| Briefed tap code every 30 min after dark | A cheap signal that carries no radio and counts ×100 as evidence | He must be conscious and briefed |
| Model inlined into the page by a build step | The page cannot drift from the model; `model.js` still runs in Node | `index.html` must be rebuilt after edits |
| Separate v2 folder; v1 kept | v1 (with a tag) stays documented and runnable | Two simulators to maintain |

### Verification
- `node simulator/v2/model_table.js` runs all 5 presets and 7 what-if cases; the numbers above come from it.
- In-forest radio loss is within 2 dB of a LoRa range measurement in Brazilian rainforest.
- Page loaded in headless Chromium with software WebGL: no console errors except the Google Fonts request the test sandbox blocks, no horizontal scroll at 1280 px or 390 px; presets, all four missions, the cinematic and the export exercised.
- The page's real export (`unreal/sample_scene.json`, 25 actors) passes `python unreal/test_ue_import.py`.

### Open issues / next
- Tune the likelihood ratios and sound levels on recorded data (tap code under canopy, cicada chorus, rain).
- Detection probability and false-alarm rates instead of pass/fail ranges.
- Frame rate on a real GPU; Safari and iOS.
- Run the scene import inside the Unreal Editor.
- Feed v2's thermal contrast into the Unreal live thermal demo.

---

## 2026-11-07 · Unreal live thermal demo v1

**Files:** [`unreal-sim/`](../unreal-sim/) · **Docs:** [unreal-sim.md](unreal-sim.md), [unreal-sim/README.md](../unreal-sim/README.md)

### What
An Unreal Engine 5 project that flies one hawk-eagle-sized Eagle over real Taiwan mountain terrain
(Google Photorealistic 3D Tiles through Cesium for Unreal). The Eagle searches three evasion-plan
cells with a white-hot thermal camera, sees a hidden person only through canopy gaps, confirms them
by circling, perches on a tree above them, drops a comms pellet, authenticates them and holds a relay
orbit. Each step appears on ATAK as a Cursor-on-Target marker. It runs from an **Eagle Sim** menu in
the editor, with an auto director that cuts between chase, thermal, perch and overview cameras.

### Why
The browser simulator answers the physics questions at one perch. Judges and partners also need to
see the wide-area search that comes first, on real terrain, with the ATAK picture a coordinator would
watch. Unreal with Cesium gives real terrain at any site in Taiwan without building a map by hand.

### How it works
- The mission is plain Python with no Unreal dependency (`eagle_sim/core`): flight, detection
  geometry, airborne optical sectioning, CoT. It talks to the scene through a ten-method engine
  interface, so the same code runs in Unreal, in the tests and headless.
- In Unreal (`eagle_sim/ue`), detection uses real line traces: trees block the Visibility channel,
  terrain blocks everything, and terrain probes pass through the trees.
- The thermal look is a post-process material on the Eagle's camera only. Warm bodies are drawn from
  the Custom Depth stencil where nothing stands in front of them.
- Everything runs in the editor world on one tick callback: no Play-In-Editor, C++ or Blueprints, and
  the whole scene (materials, level, forest) is generated by script, so the repo holds no binary assets.

Full detail: [unreal-sim.md](unreal-sim.md).

### Key results (modelled)
- **Detection range** for a person with a 640 × 512, 32° thermal core: 172 m (3 pixels across a 0.45 m torso).
- **Reference run** (seed 32): cue at 206.9 s, confirmed at 248.5 s from 8 views over 120°,
  authenticated at 266.2 s, relay orbit at 275.4 s; about 1 min 50 s on screen at the default speed.
- **Muntjac decoy** in the first cell is detected and rejected without a cue.
- **Cooperative SWIR beacon:** confirmed at 230.5 s instead of 248.5 s in this three-cell area.

### Decisions and trade-offs
| Decision | Why | Cost |
|---|---|---|
| Python in the editor, no C++ | No Visual Studio or compile step; anyone with Unreal can open it | Runs in the editor rather than as a packaged app |
| Mission logic engine-free, behind an interface | Testable in seconds without Unreal; a headless backup can drive ATAK | Two adapters to keep in step |
| Scene generated by script | No binary assets in git; change `config.py` and rebuild | First build takes 1–3 minutes |
| Google Photorealistic 3D Tiles via Cesium ion | Real terrain anywhere in Taiwan | Needs internet and a free ion account; attribution must stay on screen |
| Stylised trees for canopy | Exact, controllable occlusion that matches the tests | Looks less real than the terrain |
| Thermal view as a stencil post-process | Clear, deterministic picture of what the camera can see | Not radiometric; the browser simulator covers contrast physics |

### Verification
- 18 tests pass, including an end-to-end run of the Unreal code against a stand-in `unreal` module:
  build, terrain streaming, 1,220 tree parts, start, fly to the relay orbit, camera cuts, labels,
  pellet landing and all CoT types arriving over UDP.
- All 332 Unreal Python references checked against the UE 5.6 Python API stub; Cesium calls checked
  against the Cesium for Unreal v2 headers.
- Headless mission into the CoT listener over UDP: 53 events received.
- **Not yet run in a real Unreal Editor**; see the checklist in [unreal-sim.md](unreal-sim.md#verification).

### Open issues / next
- First run on the demo laptop: confirm the menu, build, thermal view, text orientation and ATAK markers; add screenshots.
- Record a backup video of a full run.
- Feed the browser simulator's thermal contrast model into the Unreal thermal view (time of day).
- Detection probability and false alarms instead of pure geometry.
- Route CoT through the relay and satellite path instead of straight to ATAK.

---

## 2026-11-07 · Eagle Jungle Simulator v1

**Files:** [`simulator/index.html`](../simulator/index.html), [`simulator/run-model.mjs`](../simulator/run-model.mjs) · **Docs:** [simulator.md](simulator.md)

![Simulator overview, 02:00 night scenario](images/overview-night.jpg)

### What
A browser-based 3D simulation of one full personnel-recovery mission: the Eagle perches with its motor off near an injured operator in Taiwan mountain jungle, detects him by sound, heat and an authenticated LoRa tag poll, computes a position fix, relays it via a relay bird and Iridium SBD to an ATAK map, and carries the coordinator's text back to the operator's e-ink tag.

### Why
The problem we are solving: locate an isolated person with no GPS, cell or comms, and deliver critical information to them when comms are down. Before building hardware we need to know which sensors actually work under canopy, at what range, at what time of day, and what each radio emission costs in exposure. The simulator answers those with physics rather than guesses, and lets us show the whole concept of operations in one screen.

### How it works
- A pure-function physics model (`EagleModel`) computes environment, acoustic, thermal, radar, RF, fix accuracy, LoRa airtime, the message list and the timeline from 24 settings.
- A three.js scene renders the terrain, jungle, actors and weather, and draws the model's ranges as overlays.
- The Eagle cam renders the scene from the drone's camera through a thermal shader at the chosen sensor's resolution, so you can see whether the body actually stands out.
- The mission player animates the timeline and prints every message with size, airtime and power.

Full detail: [simulator.md](simulator.md).

### Key results (model estimates)
- **Night, 20 m, sub-canopy perch:** found by all three sensors, identity authenticated, fix error 3.2 m, ATAK marker in 34.4 s.
- **14:00 cicada chorus:** thermal fails (sun-warmed leaves are as warm as the body: -0.7 °C apparent contrast vs a 2.3 °C threshold); the tag and the whistle still find him.
- **Fix accuracy collapses with distance from a single perch:** 3.2 m at 20 m, 42.5 m at 80 m. A second perch brings 80 m back to 6.2 m.
- **Under the canopy, sight lines limit thermal, not the sensor:** a 640×512 core reaches 23 m, the same as a 256×192 one.
- **A patrol 60 m away** puts the Eagle in EMCON hold for 139 s; the marker arrives at 173 s instead of 34 s.
- **Iridium latency is 87% of the time to marker** (30 of 34.4 s).

### Decisions and trade-offs
| Decision | Why | Cost |
|---|---|---|
| One self-contained HTML file, no build step | Anyone (judges included) can open it on any machine | Depends on CDNs for three.js and fonts on first load |
| Physics in a separate pure-function block | Testable and runnable without a browser (`run-model.mjs`); the UI can't drift from the numbers | Two code styles in one file |
| Every constant in `PARAMS` with its source, estimates labelled | Reviewers can check and challenge each number | Some inputs (biophony, whistle level) are still estimates |
| Pass/fail thresholds instead of probability curves | Simple to explain and verify for v1 | No false-alarm or detection-probability statistics yet |
| Seeded forest with a gap matching the model's 34% clear-line chance | The default scene shows the case the camera can see | The 3D scene is one realisation, not the distribution |
| Taiwan Central Mountain Range, July climate | The scenario we are designing against | Other seasons and terrain need new climate inputs |

### Verification
- Model runs headless in Node 22 via `run-model.mjs`; all four presets and the what-if cases in [simulator.md](simulator.md) were generated by it.
- Page loads in headless Chrome (SwiftShader WebGL) with zero console errors or failed requests; screenshots in `docs/images/` are from that run, including a full mission.
- Phone layout checked at 390×844.

### Open issues / next
- Add terrain masking to the RF model (currently flat ground).
- Include the Eagle's own poll in the EMCON decision (it reaches ~413 m in the default scene).
- Detection probability and false-alarm modelling (wildlife as decoys).
- Vendor three.js and fonts so the simulator runs fully offline.
- Field-validate: acoustic ranges, thermal contrast and LoRa path loss under real canopy.

---

## 2026-11-07 · Thermal camera probe v1

**Files:** [`thermal-probe/`](../thermal-probe/) · **Docs:** [thermal-probe.md](thermal-probe.md)

### What
A command-line tool that finds a working mode for a USB thermal camera, shows a live false-colour view with hot-spot boxes, and streams it as H.264 to other screens.

### Why
Our HTI thermal imager shows up as a USB camera, but OpenCV could not open it with default settings, which blocked every thermal demo. Low-cost thermal cores often need one particular backend, size or pixel format, and some stack the picture and the raw data in one frame.

### How it works
- Tries every camera × backend (DirectShow, Media Foundation, V4L2) × size × format (YUY2, Y16, colour conversion on and off) and ranks the modes that work, preferring ones that carry raw 16-bit data.
- Decodes double-height frames (picture above 16-bit raw data).
- Falls back to ffmpeg when OpenCV cannot open the camera; streams H.264 to MediaMTX for WebRTC or VLC.
- Prints a config block for the Eagle kit's thermal input.

### Key results
- With a simulated camera: the split-frame mode is found and decoded, hot spots are boxed, and the H.264 output runs at 768 × 576. No measurements with the real camera yet.

### Decisions and trade-offs
| Decision | Why | Cost |
|---|---|---|
| Scan every combination instead of writing a driver per camera | Works with an unknown camera at the venue table | Slower than opening a known mode (`--quick` tries the likely ones) |
| OpenCV first, ffmpeg as the fallback | OpenCV gives raw frames on most cameras; ffmpeg opens the rest | Two code paths |
| MediaMTX for streaming | One small server gives both WebRTC and RTSP | One more program to run |

### Verification
- `python thermal-probe/test_probe.py`: a simulated camera plus real ffmpeg; covers decoding, mode selection, hot spots, the ffmpeg reader and the H.264 output.

### Open issues / next
- Run it on Windows with the HTI imager and record the working mode.
- Calibrate raw values to °C for that camera.

---

## 2026-11-07 · Eagle starter kit v2: find loop, encrypted link and signs of life

**Files:** [`eagle-kit/`](../eagle-kit/) · **Docs:** [eagle-kit.md](eagle-kit.md), [eagle-kit/README.md](../eagle-kit/README.md)

### What
The software for a real perch and base station. Version 1: the perch hears a briefed whistle or tap code, measures its direction with a microphone array, turns bearing and perch height into a position, can poll the person's tag, and sends one 48-byte FIX, encrypted with AES-256-GCM, by LoRa to the Nest, which puts a Cursor-on-Target marker on ATAK. Version 2 adds signs of life: a status for each of up to ten people (RESPONSIVE, BREATHING, MOVING, WARM_ONLY, RF_ONLY, UNKNOWN) from thermal, sound, radar, Bluetooth and vision, sent home in LIFE frames of 128 bytes or less.

### Why
To turn the concept into something we can bench-test at the venue with off-the-shelf parts (Raspberry Pi, USB microphone arrays, Heltec LoRa boards, thermal and radar modules), and to show that a find can travel as one short, encrypted, low-power burst.

### How it works
- Direction by GCC-PHAT; range = perch height / tan(depression); two perches can cross their bearings.
- Messages are defined once, in `eagle/protocol.py`: CHALLENGE 20 B and REPLY 21 B (HMAC), FIX 48 B and LIFE up to 128 B (AES-256-GCM), BEACON 15 B. The firmware headers mirror them, and the tests fail on drift.
- Power control by reciprocity: each burst goes out at the lowest power that reaches the receiver.
- A duress bit only the Nest can read; replay windows on every receiver.
- Signs of life fuse thermal blobs, sound bearings, radar vitals, Bluetooth (allowlist only: bystanders are counted, never logged) and person detections into one list.

### Key results (modelled)
- Find loop in simulation: position error 0.94 m from one perch 8 m up (1.15 m crossed with a second perch); one FIX at +7 dBm, 48.8 ms on air; a receiver 1.6 km away does not see it.
- Signs of life: 4 of 4 statuses right, positions within 0.17 m. Ten people over six random layouts: 47 of 47 tracked, 59 of 60 statuses right, no false tracks, two LIFE frames (161.5 ms).

### Decisions and trade-offs
| Decision | Why | Cost |
|---|---|---|
| LoRa at SF7 and 250 kHz | Short airtime (48.8 ms for a FIX) with 3 dB more sensitivity than 500 kHz | A fixed 250 kHz channel only suits a brief low-power demo under 47 CFR 15.247 |
| One canonical byte layout in Python, mirrored in C | Firmware and host cannot drift silently | Two copies to keep in step (tests catch drift) |
| Allowlist-only Bluetooth scan | Never stores bystanders' identifiers | Unknown devices are only counted |
| Simulation first | Bench-testable without hardware | Hardware paths are unverified |
| Optional tag mode kept | Fast, authenticated identity when a tag exists | Not available when the person carries nothing (see simulator v2) |

### Verification
- `python -m pytest -q` in `eagle-kit/`: 183 passed, 1 skipped (it needs a YAMNet model file), with the optional pyroomacoustics and pymap3d installed.
- Firmware sketches compiled and run on the host against stub libraries.

### Open issues / next
- Live audio, real radios, the ESP32 toolchain and TAK over TLS.
- The thermal cameras, Bluetooth scan, radar serial link and YAMNet inference on hardware.
- A 500 kHz channel for anything beyond a brief demo.

---

## Template

```markdown
## YYYY-MM-DD · <what was built> <version>

**Files:** <paths> · **Docs:** <links>

### What
One paragraph: what it is and what it does.

### Why
The problem it addresses and the question it answers.

### How it works
3-6 bullets. Link to the detailed doc.

### Key results
Numbers, with units, labelled as measured or modelled.

### Decisions and trade-offs
| Decision | Why | Cost |
|---|---|---|

### Verification
How we know it works: tests, runs, screenshots, measurements.

### Open issues / next
What is still wrong or missing.
```
