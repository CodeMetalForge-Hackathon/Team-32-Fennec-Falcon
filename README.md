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

**Source of truth: [Eagle Jungle Simulator](simulator/v2/index.html)** ([reference](docs/simulator-v2.md#source-of-truth-merged-simulator)). One page carries the realistic flapping Eagle (95-bone model from this repo's eagle-drone work) and the navigation brain of the unified mission below, run unchanged (`forest-model.js`, `navigation-model.js`, `forest-site.js` inlined at build): GPS denied on visual-inertial, a collision-checked route through boxes built from the trees on screen, support verified before motor off, retry on a failed grip, abort with nothing sent when no route or position. The sensing model then runs from the support actually held: silent person by default, Linear-4 mic bar (azimuth only, so sound alone gives a BEARING), the Pi's gate (P ≥ 0.95 and two independent kinds of evidence, dwell, EMCON), a 15-byte Fennec Fix (AES-128-CCM) sent twice 30–120 s apart, onward by relay Eagle, Nest or Iridium, and an energy card (the 59 Wh parts-list pack does not reach the perch; the 150 Wh plan does). Serve `simulator/v2/` (`node simulator/v2/serve.mjs`) or open the published copy on claude.ai. Checks: `cd simulator/v2/tests && npm test`.

**[Unified Eagle mission](simulator/index.html)** (navigation reference; its modules run inside the source-of-truth page above). One aircraft, scene and mission clock connect dense Fushan forest navigation, support verification, observation/listening, modeled relay/storage and departure. Twelve viewpoints include nose, gimbal, chase, shoulders, overhead and perch approach. The former forest-lab URL redirects here. [Integration and limits](docs/unified-mission.md) · [Site analysis](docs/taiwan-forest-analysis.md).

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
| [docs/build-log.md](docs/build-log.md) | Change record and limitations |
| [docs/how-we-document.md](docs/how-we-document.md) | Documentation workflow |

## Checks and status

```
node simulator/test-sensors.cjs
node simulator/test-navigation.cjs
node simulator/test-forest.cjs
node simulator/test-mission.cjs
python -m pytest unreal-sim/tests
```

Browser and Unreal demonstrations are software models. Actual sensing, identity verification, communications propagation, flight power and perching require hardware/field validation. No person-worn or delivered electronic endpoint is part of the mission.

Team 32: Mitch Kirby and Alex Bartolomei, Ember Corps. [Apache-2.0](LICENSE).
