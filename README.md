<div align="center">

<h1>Fennec Falcon</h1>

<h3>Operator down: locate. Stay covert.</h3>

Disguised, perching eagle drones that report recovery-grade information to inform the command's next step.

<b><a href="https://canva.link/02k57fkmh9afe6c">Open the pitch deck</a></b> &nbsp;·&nbsp; <a href="#the-mission-step-by-step">Walk the mission</a> &nbsp;·&nbsp; <a href="#what-runs-today">See what runs today</a> &nbsp;·&nbsp; <a href="#find-the-code">Find the code</a>

<br><br>

<img src="docs/images/pitch/00-concept-film.gif" width="720" alt="Concept film: an operator down in the jungle, command losing contact, an autonomous craft launching eagle drones, an eagle perching, a thermal detection, and command confirming the operator alive.">

<sub>Concept film. Rendered, not field footage. No sound. <a href="docs/images/pitch/00-concept-film.mp4">Full-quality MP4</a></sub>

</div>

## Operator down

A fighter jet goes down over Taiwan's mountain jungle. Recovery forces have to **find** the aviator, **confirm** whether they are alive and in what condition, and **report** the fix without exposing them or the drone.

It is too big to sweep and too dangerous to transmit:

- **20,340 km²** inside a 50-mile circle from the last known position, 57% of Taiwan's main island *(computed)*
- **75 km²** for the first search, once the evasion plan and terrain shrink the circle *(modelled)*

## The product

**Perch. Listen. Decide. Whisper.**

Fennec is a one-way eagle drone that hides in plain sight. It flies in alone, perches like a real raptor, searches with sensors and compute on board, and sends one sealed 15-byte burst per person found. The aviator carries nothing that transmits. No pilot, no video feed, no chatter.

*Fennec* is the system. The *Eagle* is the bird, and the engineering notes further down use that name.

## The niche

Today's options are loud, deaf, or leave a radio trail. Fennec is the only option built to be all three things a covert recovery search needs.

| Option | Gives you | Costs you | Silent | Persistent | Decides at the edge |
|---|---|---|:-:|:-:|:-:|
| Aircraft | Fast coverage | Loud and transient | – | – | – |
| Ground teams | Human judgment | Slow and exposed | ✓ | – | ✓ |
| Beacon / SATCOM | Known signal | Persistent RF trail | – | ✓ | – |
| **Fennec** | **A sealed fix from a bird nobody notices** | **Still bench-stage** | **✓** | **✓** | **✓** |

## The fit

Personnel recovery is a standing joint mission. Recovery units own it, Fennec fleets are the deployable layer, and program partners are the procurement path. The offer is the hardware fleet, the mission software and the sustainment behind them.

## Why it's relevant

- **The search itself is the risk.** On contested ground, a loud aircraft or a radio trail can lead the threat to the person being recovered.
- **Edge AI makes a quiet searcher possible.** Person detection and a face check already run offline on a Raspberry Pi with a Hailo accelerator. No cloud, no link.
- **It carries beyond this mission.** The same sensing questions apply to wilderness and wildfire searches.

## The mission, step by step

<sub>Stills from the concept film, except step 7, which is the simulator's thermal view.</sub>

<table>
<tr>
<td width="360"><img src="docs/images/pitch/01-operator-down.jpg" width="340" alt="An aviator alone in the jungle beside a cracked tablet."></td>
<td><b>1. Operator down</b><br>The aviator is alone in the jungle with comms gone, carrying nothing that transmits.</td>
</tr>
<tr>
<td><img src="docs/images/pitch/02-lost-contact.jpg" width="340" alt="Command map of Taiwan with a red LOST CONTACT marker."></td>
<td><b>2. Command loses contact</b><br>The last known position goes up on the board at command: LOST CONTACT. With nothing coming from the aviator, that point is all command has.</td>
</tr>
<tr>
<td><img src="docs/images/pitch/03-launch.jpg" width="340" alt="An autonomous craft at speed offshore with birds lifting off."></td>
<td><b>3. Launch</b><br>A SOCOM autonomous craft runs in and releases the birds about 20 km offshore.</td>
</tr>
<tr>
<td><img src="docs/images/pitch/04-fly-in.jpg" width="340" alt="An eagle drone gliding in toward a mossy branch."></td>
<td><b>4. Fly in</b><br>Each bird flies alone to the last known position and drops a prey relay at the midpoint. No pilot, no video feed, no chatter.</td>
</tr>
<tr>
<td><img src="docs/images/pitch/05-perch.jpg" width="340" alt="The eagle drone perched on a branch with wings folded."></td>
<td><b>5. Perch</b><br>The bird lands on a branch, folds its wings and shuts off the motor. It has serpent-eagle markings and copies a real raptor's routine, which is to spend nearly all day perched.</td>
</tr>
<tr>
<td><img src="docs/images/pitch/06-look-down.jpg" width="340" alt="View past the eagle's shoulder down through a gap in the canopy to a tiny figure."></td>
<td><b>6. Listen and look</b><br>The Fennec Ear, a four-mic bar (33 mm spacing) in a 90° V, boosts the whistle band by +9 to +14 dB <i>(modelled)</i>. Thermal and visible cameras watch the ground below. A briefed tap or whistle helps, but the aviator doesn't have to make a sound to be seen.</td>
</tr>
<tr>
<td><img src="docs/images/pitch/07-thermal-detection.png" width="340" alt="Simulated thermal view from EAGLE-1 with a box on a person labelled P 0.99+."></td>
<td><b>7. Decide</b><br>All on board. The bird speaks only when the gate passes: 3+ briefed-pattern events in 3 minutes, bearings within ±10°, mean confidence 0.9 or more <i>(design rule)</i>. Raw audio and thermal never leave it.</td>
</tr>
<tr>
<td><img src="docs/images/pitch/08-burst.jpg" width="340" alt="Eagles over a coastal ridge with a thin signal line reaching out to sea."></td>
<td><b>8. Whisper</b><br>The Fennec Fix packs who, where, how sure and when into 64 bits, sealed to 15 bytes (AES-128-CCM). It goes out twice after a hop of 2 km or more. The first path that closes carries it: the delivery craft, the midpoint relay, a relay eagle, a courier or Iridium SBD.</td>
</tr>
<tr>
<td><img src="docs/images/pitch/09-confirmed.jpg" width="340" alt="Command map of Taiwan with a green CONFIRMED: OPERATOR ALIVE marker."></td>
<td><b>9. Command knows</b><br>A live marker appears in TAK: CONFIRMED: OPERATOR ALIVE. Command now has what it needs to decide the next step.</td>
</tr>
<tr>
<td colspan="2"><b>10. Authenticate, guide, recover</b><br>After the fix, the bird answers an ISOPREP challenge so the aviator can trust it, then guides night walks to a boat pickup or partner link-up, or helps them hold in place. Spent birds wipe their keys and dive into water or a ravine.</td>
</tr>
</table>

## What runs today

Built over the hackathon weekend. Every range, gain and link number is still a model; the ear A/B test will be the first measurement.

### Dashboard simulation

<img src="docs/images/pitch/10-dashboard-still.jpg" alt="Eagle Jungle Simulator dashboard: scenario controls, top-down view of the operator and EAGLE-1, thermal camera, probability chart from HOLD to TRANSMIT, and a table of ways to send the report.">

<img src="docs/images/pitch/10-dashboard-simulation.gif" alt="Screen recording of the Eagle Jungle Simulator dashboard moving between camera views.">

The whole mission on one screen: pick a scenario, watch the Eagle's thermal view, see the probability climb from HOLD (P 0.50) to TRANSMIT (P 0.99), and see which way home it chooses and why. All outputs are modelled. The simulator still sends the older 48-byte FIX; the 15-byte Fennec Fix is on `pi-runtime`. [Full-quality MP4, no sound](docs/images/pitch/10-dashboard-simulation.mp4)

### Bench demo and face check

<table>
<tr>
<td width="300"><img src="docs/images/pitch/11-bench-demo.gif" width="280" alt="Phone video of the bench setup: the screen changes from HUMAN DETECTED: NOT YET IDENTIFIED to TARGET FOUND: MITCH."></td>
<td>
<b>Real camera, real compute.</b> The Pi's screen starts at HUMAN DETECTED: NOT YET IDENTIFIED and flips to TARGET FOUND: MITCH once his face matches the enrolled profile. One person, indoors. Accuracy and range are not measured yet. <a href="docs/images/pitch/11-bench-demo.mp4">Full-quality MP4, no sound</a>
<br><br>
<img src="docs/images/pitch/12-face-check.jpg" alt="Local face-matching test labelling a webcam photo as Mitch.">
<br>
<sub>Local face-matching test against reference photos. The 0.61 cutoff is a setting, not a measured score. People in the background are blurred.</sub>
</td>
</tr>
</table>

### Status

| Piece | Status | Branch |
|---|---|---|
| Person detection on Raspberry Pi + Hailo, real camera | **Ran on the bench** | `sar-integration` |
| Face check and on-screen verdict | **Ran on the bench.** One person, indoors; accuracy not measured | `sar-integration` |
| HT-203U thermal camera | **Streams on the Pi.** The thermal detection in the dashboard is modelled | `sar-integration` |
| 15-byte Fennec Fix and on-board decision gate | **Code and tests.** Not yet run on the Pi | `pi-runtime` |
| Dashboard simulator and eagle drone model | **Runs in software.** Modelled | `eagle-drone-model` |
| Unified browser mission and Unreal demo | **Software models** | `main` |
| Flight, branch perch, radio range, field audio | **Not done yet** | Next |

## What's next

**Bench demo** (now) → **Field audio** → **Perch and flight** → **Recovery exercise** with a partner.

Gates: detection confidence, acoustic range, flight mass, partner validation.

We're seeking a recovery partner, field-test access and the path to a flight-weight prototype.

## Find the code

| To see | Go to |
|---|---|
| Unified browser mission and Unreal demo | This branch: [simulator/](simulator/), [unreal-sim/](unreal-sim/) |
| Dashboard simulator and eagle drone model | [`eagle-drone-model`: simulator/v2](https://github.com/CodeMetalForge-Hackathon/team-32/tree/eagle-drone-model/simulator/v2) |
| Pi edge kit: detection, face check, thermal, on-screen verdict | [`sar-integration`: pi/field](https://github.com/CodeMetalForge-Hackathon/team-32/tree/sar-integration/pi/field) |
| On-board decision gate and 15-byte Fennec Fix | [`pi-runtime`: eagle-kit/eagle](https://github.com/CodeMetalForge-Hackathon/team-32/tree/pi-runtime/eagle-kit/eagle) |
| All pitch images and silent videos | [docs/images/pitch/](docs/images/pitch/) |

---

# Eagle: finding people who carry no technology

**Code Metal hackathon · Team 32**

Eagle is a bird-inspired search-and-rescue concept for finding an isolated person under forest canopy. The missing person carries **no technology**. All sensing, processing and radio equipment is on the bird or the relay/base infrastructure.

## How it works

1. Navigate the forest and verify a reachable structural support.
2. Perch with propulsion off to observe using the bird's thermal/visible cameras and microphones. Detection requires no cooperative response.
3. Combine sensor evidence and geometry to estimate the observed person's location.
4. Send observations from bird to relay and onward to the coordinator. A failed modeled link retains the observation on the bird.
5. Depart along the checked corridor after the observation/transport stage. Distinguish human detection from individual identity verification.

The simulations currently report **identity UNVERIFIED**. Thermal imagery and natural sound alone do not establish who a person is. Visual recognition must be implemented and validated before identity claims are made. Optional voice, shouting or mouth whistles are natural sounds, not required signals. The default person is silent.

The reference scenario is Taiwan mountain forest; the same sensing questions apply to wilderness and wildfire searches. The physical prototype's body is 16.5 inches beak to tail; the separate full-size design reference uses a 1.65 m span and provisional 2.4 kg mass. These are different development stages, not measured flight performance.

## Run the browser simulator

**[Unified Eagle mission](simulator/index.html).** One aircraft, scene and mission clock connect dense Fushan forest navigation, support verification, observation/listening, modeled relay/storage and departure. Twelve viewpoints include nose, gimbal, chase, shoulders, overhead and perch approach. The former forest-lab URL redirects here. [Integration and limits](docs/unified-mission.md) · [Site analysis](docs/taiwan-forest-analysis.md).

Serve the repository with `python -m http.server 8770 --bind 127.0.0.1` and open `http://127.0.0.1:8770/simulator/` in Chrome, Edge, Safari or Firefox. Browser scripts are bundled locally; external fonts are optional. Headless sensor model:

```
node simulator/run-model.mjs
node simulator/run-model.mjs night opDist_m=80 --json
```

Original headless silent-person estimates are below. The unified mission additionally gates observations on verified support and modeled geometric line of sight; its results can therefore be **NOT FOUND** even when this statistical baseline predicts thermal evidence.

| Scenario | Evidence | Result | Identity | Marker |
|---|---|---|---|---|
| Summer night | Thermal | PARTIAL | UNVERIFIED | 32 s |
| Cicada afternoon | None | NOT FOUND | UNVERIFIED | None |
| Typhoon rain | Thermal | PARTIAL | UNVERIFIED | 32 s |
| Pre-dawn mist | Thermal | PARTIAL | UNVERIFIED | 32 s |

The delivered scenarios have 1.1 m local geometric error under default assumptions; this excludes bird navigation/map-origin uncertainty. These are model estimates, not field measurements. Thermal contrast can fail in daytime, vegetation blocks sight, and radio communication is separate from person detection.

![Current sensor-only simulation](docs/images/overview-night.jpg)

## Unreal demonstration

The [Unreal demo](unreal-sim/README.md) searches real Taiwan terrain through canopy gaps, accumulates thermal views, perches to observe and holds a relay orbit. It requires no electronics on the missing person. Its geometry-based human observations remain identity-unverified. The fake-engine tests do not replace real editor or flight validation.

## Field prototype: the Eagle's onboard computer

The sensing payload runs today on real hardware, entirely offline: a Raspberry Pi 5 (16 GB) with a Hailo-8 AI accelerator, an HTI HT-203U thermal imager, a Camera Module 3 (standard; a NoIR Wide is supported), and a reSpeaker XVF3800 4-microphone array. For the demonstration it is mounted stationary in a model falcon, which matches the perch-and-observe stage of the mission. No internet, cell service or cloud is used at any point; models, fusion, decisions and encryption run on the Pi.

```
thermal imager --USB--+                         +--> verdict + Operator Profile match level
camera (Hailo-8) -----+--> fusion (eagle-kit) --+--> one 48-byte AES-256-GCM FIX message --> Nest (decrypts, ATAK marker)
4-mic array --USB-----+   heat, person boxes,   +--> demo screen and phone control page (local Wi-Fi hotspot)
                          sounds, identity
```

1. **Detect** a human from three independent sensors: body heat (thermal), a person in view (camera, YOLO on the Hailo-8), and human sounds such as voice, shouts and whistles (YAMNet on the microphone array).
2. **Identify** whether the person is the one being searched for, by matching faces against their [Operator Profile Recognition Card](#operator-profile-recognition-card).
3. **Decide and report**: fused evidence becomes one status per person; the result, position and match level go out as a single short encrypted message.

Everything starts on power-up as systemd services, and a phone joins the Pi's own hotspot to watch and control it. Setup, the services and the field checklist: [docs/field-prototype.md](docs/field-prototype.md) · [pi/field/FIELD_TEST.md](pi/field/FIELD_TEST.md).

### Run the hardware demo

Setup on a fresh Raspberry Pi 5 (Raspberry Pi OS Bookworm, Hailo-8 driver and `hailo-apps` installed, sensors plugged in):

```
git clone <this repo> ~/team-32 && cd ~/team-32/eagle-kit
pip install -r requirements.txt opencv-python onnxruntime insightface ai-edge-litert pillow-heif --break-system-packages
python apps/fetch_models.py              # one-time model download (needs internet once; nothing downloads at runtime)
python apps/keygen.py --out-dir ~/eagle/keys   # field keys, kept outside the repository
bash ../pi/field/install.sh              # installs and starts the services
```

Run it:

1. Enrol the person being sought (their Operator Profile card): `python apps/enroll_target.py --photos <folder> --live`.
2. Power on. All services start by themselves with no network.
3. Open `http://localhost:8081` on the Pi's screen for the demo view (camera, thermal, sound, verdict, signal-sent badge), or `http://localhost:8080` for the control page. A phone can join the Pi's hotspot and use `http://10.42.0.1:8081`.
4. Walk into view: the banner moves from NO HUMAN DETECTED to HUMAN DETECTED to TARGET FOUND, and the encrypted FIX received by the Nest shows as SIGNAL SENT TO RESCUE HQ.

Details, troubleshooting and the field checklist: [docs/field-prototype.md](docs/field-prototype.md) and [pi/field/FIELD_TEST.md](pi/field/FIELD_TEST.md).

## Operator Profile Recognition Card

The **Operator Profile Recognition Card** is the identity record the Eagle carries for the person it is looking for. It is built *before* a search from a full face scan and recent photos, and loaded onto the onboard computer. During the search, every face the camera sees is compared against it, on the device, with no connection.

**Key assumption of this solution: the individual being searched for has a full face scan on file before they go missing** (for example, taken when registering for a backcountry permit, joining a team, or deploying as an operator). The card is how the Eagle can say "this is the person we are looking for" rather than only "a person is here".

| On the card | Used for |
|---|---|
| Face signatures (512-number ArcFace embeddings) from the face scan and photos | Face verification at close range |
| Short appearance description (clothing, pack) | Appearance match at distances where the face is too small |
| Enrolment date and source | Audit and expiry |

The card stores signatures, not photos, and stays on the device. Identity can only raise a detection's priority: every human detected is still reported, so a missed match never costs a rescue. The match level (0 none, 1 to 2 appearance, 3 face verified) travels inside the 48-byte message. Full description, enrolment steps, privacy and limits: [docs/operator-profile-card.md](docs/operator-profile-card.md).

**Optional tag.** `eagle-kit` also contains firmware for a small person-worn radio tag (`eagle-kit/firmware/tag/`). It is an optional extension for cooperative cases (for example teams that carry one) and is not part of the core mission, which assumes the missing person carries no electronics.

The simulations above remain identity UNVERIFIED by design; the card is what the hardware prototype adds, and its accuracy is so far measured only in indoor tests (see the reference doc).

## Project map

| Path | Purpose |
|---|---|
| [simulator/](simulator/) | Browser model, visualization, runner and regression checks |
| [unreal-sim/](unreal-sim/) | Unreal Editor demo, headless mission and tests |
| [docs/simulator.md](docs/simulator.md) | Current sensing, communication and mission reference |
| [docs/unreal-sim.md](docs/unreal-sim.md) | Unreal geometry, scene and CoT reference |
| [docs/navigation-demo.md](docs/navigation-demo.md) | GPS-denied navigation and separate multicopter SITL viewer |
| [docs/forest-navigation.md](docs/forest-navigation.md) | 3D forest planning, landing/perching states and hardware integration gaps |
| [docs/unified-mission.md](docs/unified-mission.md) | Shared flight/observation/transport clock, scene and twelve viewpoints |
| [docs/coastal-relay-sizing.md](docs/coastal-relay-sizing.md) | Assumption-based inland-to-boat relay count and deployment energy |
| [eagle-kit/](eagle-kit/) | Onboard software: fusion of heat, vision and sound, identity matching, encrypted LoRa messages, Nest station, ATAK output ([reference](docs/eagle-kit.md)) |
| [pi/field/](pi/field/) | Field deployment on the Raspberry Pi: services, offline hotspot, demo screen, phone control page, recording |
| [fusion/](fusion/) | Earlier three-sensor dashboard and the bundled detection models |
| [pi/](pi/) | Thermal imager streaming (RTSP/WebRTC) and hotspot scripts |
| [thermal-probe/](thermal-probe/) | Finds, views and streams a USB thermal camera ([reference](docs/thermal-probe.md)) |
| [simulator/v2/](simulator/v2/) | Simulator v2: heat and sound evidence gate, four ways to get one report out ([reference](docs/simulator-v2.md)) |
| [docs/field-prototype.md](docs/field-prototype.md) | Hardware prototype: architecture, services, operation and verification |
| [docs/operator-profile-card.md](docs/operator-profile-card.md) | Operator Profile Recognition Card: assumption, enrolment, matching, privacy and limits |
| [docs/build-log.md](docs/build-log.md) | Change record and limitations |
| [docs/how-we-document.md](docs/how-we-document.md) | Documentation workflow |

## Checks and status

```
node simulator/test-sensors.cjs
node simulator/test-navigation.cjs
node simulator/test-forest.cjs
node simulator/test-mission.cjs
python -m pytest unreal-sim/tests
cd eagle-kit && python -m pytest -q   # onboard software, runs without hardware
```

Browser and Unreal demonstrations are software models. The field prototype runs the sensing and identity pipeline on real hardware, tested indoors so far. Actual long-range sensing, identity verification, communications propagation, flight power and perching require hardware/field validation. No person-worn or delivered electronic endpoint is part of the mission.

## Open-source components

Pre-existing open-source libraries and models used, with licences and sources in [eagle-kit/eagle/data/NOTICE.md](eagle-kit/eagle/data/NOTICE.md):

- **InsightFace** buffalo_l (SCRFD face detector, ArcFace recognition): MIT code; pretrained weights are for non-commercial research only.
- **Ultralytics YOLOv8n / YOLOv5** person detection (AGPL-3.0), compiled for the Hailo-8 from the **Hailo Model Zoo** and **hailo-apps** (MIT).
- **YAMNet** sound classifier and class map (Google, Apache-2.0).
- **OpenAI CLIP** ViT-B/32 (MIT, optional appearance matching) and **torchreid OSNet** (MIT code, research-only weights).
- Thermal person model trained on the **HIT-UAV** dataset (CC BY 4.0, Suo et al., 2023).
- Python libraries: NumPy, OpenCV, ONNX Runtime, LiteRT (`ai-edge-litert`), `cryptography` (AES-256-GCM), PyYAML, pyserial, sounddevice, Pillow and pillow-heif; GStreamer and Picamera2 on the Pi.

The model files in `fusion/models/` are stored with Git LFS (`git lfs pull` after cloning). Larger weights are downloaded by `eagle-kit/apps/fetch_models.py` and are not committed.

## Team

Team 32, Ember Corps: Mitch Kirby and Alex Bartolomei. Licensed under [Apache-2.0](LICENSE).
