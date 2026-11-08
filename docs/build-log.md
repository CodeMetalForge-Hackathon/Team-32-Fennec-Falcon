# Build log

## 2026-11-08 · Field prototype on the Raspberry Pi and the Operator Profile Recognition Card

**Files:** [`eagle-kit/`](../eagle-kit/), [`pi/field/`](../pi/field/), [`fusion/`](../fusion/) · **Docs:** [field-prototype.md](field-prototype.md), [operator-profile-card.md](operator-profile-card.md)

### What
The perch-and-observe stage running on real hardware, offline: a Raspberry Pi 5 with a Hailo-8, an HTI HT-203U thermal imager, a Camera Module 3 and a reSpeaker XVF3800. Thermal, camera and sound evidence are fused into one status per person; the person being sought is identified against an Operator Profile Recognition Card; the result goes out as one 48-byte AES-256-GCM message that the Nest decrypts. A demo screen and a phone control page run over the Pi's own hotspot.

### Why
The simulations stop at "a person is here" and report identity UNVERIFIED. Rescuers also need to know whether it is the person they are looking for, and the payload has to work where there is no signal.

### How it works
- Drivers for the HT-203U (raw counts / 16 = kelvin, with a uvcvideo quirk) and the XVF3800 (6-channel firmware, 66 mm square array) feed eagle-kit's existing fusion, protocol and Nest.
- Person detection runs on the Hailo-8; face matching (SCRFD + ArcFace) runs on a background thread so the camera never waits.
- The Operator Profile card is built from a full face scan and photos (HEIC supported, duplicates skipped, faces that do not match the group dropped).
- Identity can only raise a detection's priority; every human is still reported. The match level rides in the FIX message's class byte, so the 48-byte layout is unchanged.
- systemd services start everything on power-up; a hotspot appears when no known Wi-Fi is found.

### Key results (measured, indoor, one enrolled person)
- 26 photo faces enrolled, all agreeing with each other (lowest cosine 0.60); live scan matched the photos at mean cosine 0.46.
- All three sensors, fusion and the encrypted message running together on the Pi with no network.
- 303 automated tests pass without hardware.

### Decisions and trade-offs
- Assumption: the person sought has a full face scan on file before going missing. Without it the payload still detects humans but cannot identify them.
- Appearance matching (CLIP) is off by default after a Hailo dropout; face verification is the identity signal.
- The demonstration is stationary (model falcon), matching the perched stage; flight is out of scope here.

### Verification
`cd eagle-kit && python -m pytest -q`; on-device checks in [field-prototype.md](field-prototype.md#verification).

### Open issues
- Score separation between the enrolled person and others at 1, 3 and 6 m not yet measured; no outdoor or night tests yet.
- Feather RFM69 radio link to a separate Nest not yet integrated (loopback today).
- Camera ribbon connections are fragile when the payload is moved.
- InsightFace pretrained weights are non-commercial; a different model is needed for any commercial use.

## 2026-11-07 · Unified Eagle mission and twelve viewpoints

Connected the forest state machine to the existing Eagle observation, fix and relay/storage model through one mission clock. Reused the original Eagle/person actors and thermal/visible/ID rendering in the same dense Fushan forest, with an explicit z-up to Three.js y-up transform. Supported observation now uses actual modeled separation/height and a same-scene thermal center-ray gate. Eagle holds its perch through the observation/transport timeline, then departs. Blocked routes and pose loss cannot generate observations. Failed modeled links retain observations; coordinator markers require delivery. The default person remains silent and identity unverified.

Added twelve shared-state viewpoints, pause/step/reset/speed/export, canopy/collider inspection and a local mission map with modeled fix uncertainty. The former forest route redirects to the main mission. The independent explanatory survey and forest modal are not mounted; their motion components remain unit-testable. Locally vendored the existing r128 OrbitControls/Sky/BufferGeometryUtils so browser scripts do not depend on CDNs.

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

Validation: forest-component and original sensor/navigation regressions; new mission tests for support/evidence/delivery gates, recovery, aborts, long courier holds and export; unified browser checks for shared pose/clock, twelve viewpoints, thermal rendering, redirects and mobile widths. Updated screenshots in unified-mission.md. Live VIO/depth/contact/radio, the separate ArduPilot SITL controller and Unreal runtime integration remain outside this browser change.

## 2026-11-07 · Dense understory and overlapping canopy

Expanded Fushan crown envelopes and layered meshes from isolated crowns into overlapping cover. A 1 m sample grid reports about 78% modeled crown-footprint coverage; the interface explicitly distinguishes this from measured canopy closure. Added 911 retained low-cover, fern and sapling placements, removing the broad ground-level vegetation exclusions while retaining immediate launch/landing/contact footprints. Taller represented understory affects collision checks. One hundred context trees and extended rendered ground outside the active flight bounds remove the bare diorama edge. Added denser litter and wider, more detailed fern fronds, with instanced leafy shrubs.

Enlarged crowns blocked the old ridge terminal descent; lowered the approach to enter beneath them instead of removing geometry. All 18 baseline/valley/ridge scenarios passed their termination, clearance, support and recovery checks. Density checks require over 850 understory placements and projected crown coverage between 70% and 90%. Camera, display layers and mobile/browser checks are in check-forest.cjs. Updated screenshots document the result. All density/coverage settings remain procedural, not a surveyed Fushan reconstruction.

## 2026-11-07 · Fushan evidence and ecological reconstruction

Replaced the default generic block display with a Fushan-informed perspective forest. Added primary-source context from TFRI and published canopy, understory-light and typhoon studies. The site analysis explicitly separates historical measurements, engineering inferences and invented local geometry; actual tree census and topographic stake data have not been obtained. A coarse DEM spot check was not adopted because registration to the surveyed plot was unresolved.

Added deterministic valley and shorter-ridge scenes with sloping ground, broadleaf crowns, branches, understory ferns and fallen wood; the original blocks remain selectable. The planner now handles per-world bounds, terrain clearance and reachable grid attachment. A local Three.js renderer adds shaded world geometry, drone POV, canopy cutaway, collision-envelope inspection and visual mist. No measured sensor degradation or wet-grip physics is implied. Standalone rendering uses a pinned local Three.js r128/MIT bundle; parent simulator retains its existing version.

Verification is recorded in test-forest.cjs and check-forest.cjs: baseline plus both Fushan presets, terrain/obstacle clearance, support-gated motor shutdown, failure/reselection, perspective rendering, camera controls, scene switching, layers and responsive widths. Updated screenshots show the forest, cutaway and drone viewpoint. This is an ecological reconstruction with synthetic pose/contact and a complete map, not a surveyed digital twin or hardware-validated navigation system.

## 2026-11-07 · Orbitable forest camera

Replaced the forest lab's fixed oblique projection with an interactive orthographic 3D camera. Mouse/touch orbit, wheel/pinch zoom, right/shift-drag and two-finger pan, top/side/overview presets, reset and drone-follow controls allow inspection of the same mission from different viewpoints. All obstacle faces use camera-depth sorting; the drone's arms and rotor rings now project from world geometry. Transparent obstacles and always-visible route overlays retain explanatory visibility. Camera input is independent of mission state and works while paused or playing.

Validation: browser tests passed for changed image pixels after orbit, no time advance while dragging, zoom, pan, camera presets, follow selection, keyboard navigation and actual Chromium touch pinch/pan. Existing six forest scenarios and main-simulator/mobile integration checks passed without JavaScript errors. Refreshed desktop/mobile screenshots. Planning, synthetic contact assumptions and the separate SITL boundary are unchanged. See forest-navigation.md for interaction details and rendering limitations.

## 2026-11-07 · Forest navigation, landing and perching lab

Extended the existing navigation area with a focused forest lab and standalone entry page. Six-connected 3D A* plus exact inflated-box segment tests compute clearance-checked routes. A kinematic state machine separates inspection, approach, contact, support verification, perch/ground landing, release and departure. Six selectable scenarios cover nominal branch, moving-branch rejection, grip-failure retreat/reselection, clearing landing, blocked canopy and pose/depth loss. Readiness notes distinguish code running here, the separate ArduPilot service and candidate open-source/hardware integrations.

Validation: all six forest model scenarios passed, including continuous trajectory clearance and support-gated motor shutdown. Browser playback, stepping, reset, main-simulator insertion and mobile overflow checks passed with no JavaScript errors. Desktop/mobile screenshots saved as forest-navigation.png and forest-navigation-mobile.png. Existing navigation and sensing regression checks passed. Exact slab intersection replaced sampled segment checks after the clearing test exposed a corner-clearance miss. See forest-navigation.md for constants and modeled timings.

Limits: complete synthetic map and exact pose; no real perception, moving-branch physics, aerodynamic/contact dynamics, EGO-Swarm runtime or forest-to-autopilot bridge. No actual aircraft or SITL validation is claimed for this extension. No third-party implementation was copied or installed.

## 2026-11-07 · No equipment on the missing person

The missing person carries no technology. Both demos now use observations originating on the bird. The browser defaults to a silent person; its radio is bird-to-relay transport, never person-detection evidence. Unreal searches, confirms human-shaped thermal observations, perches to observe and holds a relay orbit. Identity stays unverified; neither demo automatically classifies a person as friendly.

Removed person-side electronic protocols, cooperative electronic cues, delivery-device sequences, controls, visuals and obsolete documentation/results. Optional radar is mounted on the bird and gated by slant range. A failed browser relay stores the observation without claiming delivery. Updated runners, scene adapters, menus, tests and screenshots.

Validation: browser sensor regression and navigation regression passed; 18 Unreal/core tests passed; browser playback and mobile layout passed; all six tracked screenshots refreshed. Checks cover silent/unconscious people, no automatic identity, failed relay delivery and blocked sight lines. Real Unreal Editor rendering and field performance remain separate acceptance work.

Current modeled outputs: silent-person night/rain/mist have thermal-only PARTIAL evidence, identity UNVERIFIED, local geometric CE 1.1 m and a 32 s marker using the default modeled link. Afternoon contrast fails and the silent person is NOT FOUND. These supersede prior scenario results.

Added coastal-relay-sizing.md as a separate assumption study: a 60 km example requires 3–5 intermediate repeaters only if tested usable hops are 10–15 km. Neither terrain feasibility nor these radio ranges is established. Deployment energy and replacement inventory are separate constraints; the existing simulator remains a single-hop model.

## 2026-11-07 · GPS-denied navigation demonstration

Added a deterministic navigation panel and separate ArduPilot SITL viewer. Scenarios cover baseline, unavailable GPS, degraded canopy, position inconsistency and backup loss. Synthetic visual/inertial fallback is illustrative; SITL external position uses simulator truth. See navigation-demo.md.

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

## 2026-11-07 · Initial demonstrations

Established browser environment/acoustic/thermal/local-fix models and the mission display. Established Unreal Editor Python integration with Cesium terrain, canopy occlusion, multi-view thermal observations and CoT markers. Requirements and outputs are superseded by the current no-person-equipment implementation above. Current technical references are simulator.md and unreal-sim.md.

## Entry format

Record the problem, resulting behavior, implementation choices, validation evidence and remaining limits. Distinguish modeled outputs and field measurements. Follow how-we-document.md.
