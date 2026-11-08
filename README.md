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

Team 32: Mitch Kirby and Alex Bartolomei, Ember Corps. [Apache-2.0](LICENSE).
