# Eagle Jungle Simulator: technical reference

## Requirement

The missing person carries no technology. All sensing, processing and communications hardware is on the bird or relay/base infrastructure. Detection requires no cooperative response. Optional voice, shouting and mouth whistling are natural sounds; the default person is silent. An unconscious person produces no intentional sound in this model.

Human detection and verified identity are separate. Identity is always UNVERIFIED because the simulator does not implement visual identity recognition. The separate sensing prototype's recognition pipeline is not running inside this demo.

## Run and architecture

Open simulator/index.html in a browser, or run `node simulator/run-model.mjs`. The pure eagle-model script exposes defaults(), presets, evaluate(settings) and airtime(). Rendering consumes the same model. `node simulator/run-model.mjs night --json` exposes full results. Unknown override keys are ignored.

## Inputs and default scenario

Environment: hour, elevation, weather, leaf area index, canopy height and understory visibility. Person: distance, posture, condition and natural sound or silence. Bird: perch, thermal camera, microphone count, heading source, second perch and optional onboard radar. Infrastructure: LoRa mode, relay altitude/distance and Iridium/courier onward delivery. Unknown-group controls affect the rendered scene only.

Default: 02:00, 1,000 m elevation, clear, LAI 4.5, 15 m canopy, 20 m understory visibility, 8 m perch, person 20 m away, lying injured and silent, HTI 256 thermal camera, six microphones, visual-inertial heading, one perch, onboard radar disabled, relay 2 km away at 300 m and Iridium onward delivery.

## Sensing and fix equations

Constants and source notes remain in PARAMS. Environment retains July CWA-based temperature interpolation and estimated biophony/rain noise.

Acoustics uses spherical spreading, air/vegetation absorption, array gain 10 log10(microphone count), estimated band-noise allowance and a 6 dB detection SNR. Silence yields no acoustic detection. Natural sound levels require validation; passive breathing detection is not established.

Thermal requires three pixels across a 0.45 m torso plus sufficient apparent contrast. Clothing temperature, background clutter, atmospheric extinction and weather determine contrast. Canopy gap probability and understory sight lines limit visibility. Thermal does not see through opaque foliage. The renderer ID pass is ground truth, not person recognition.

Optional onboard radar uses nominal 6 m presence and 1.5 m vital-sign ranges from the MR60BHA2 reference. Presence is bounded by bird-to-person slant distance. Motion, vegetation and signal processing remain unvalidated. Radar alone supplies no location fix.

Fix uses acoustic bearing/depression or thermal pixel-to-ground geometry with heading and tilt error. A second perch supplies crossing bearings. The displayed local geometric error excludes bird navigation and map-origin uncertainty.

## Detection and communication

find.by contains bird-mounted sensing only. Two modalities yield FOUND, one PARTIAL, none NOT FOUND. These evidence-count labels are not calibrated probabilities. find.identity remains UNVERIFIED. The map uses an unknown-ground observation (a-u-G).

RF is bird-to-relay transport only. The existing illustrative 923 MHz model retains foliage loss, LoRa sensitivity and a 10 dB link margin, with transmit bounds of -9 to +22 dBm. Relay slant range uses endpoint height difference. A failed link stores the observation on the bird and produces no delivered marker. Terrain profiles, interference and operational range remain unvalidated. Frequency settings are simulation inputs, not spectrum authorization.

FIX is 48 B from bird to relay. SBD MO is 56 B including assumed overhead from relay to satellite/coordinator. Courier is a physical-delivery alternative. Intended encryption is descriptive, not implemented cryptography. Airtime uses Semtech AN1200.13. Onward latency is assumed 30 s for Iridium or 3,600 s for courier. No electronic endpoint or electronic instruction delivery to the person is assumed.

Timeline: Listen, optional natural sound or thermal Look, Fix, Relay and Marker if the link closes, then sensor Track. Failed delivery produces Store observation instead.

## Current modeled outputs

All results are model estimates, not field measurements. Default person is silent.

| Preset | Evidence | Status | Identity | Local fix CE | Delivered marker |
|---|---|---|---|---|---|
| Summer night | Thermal | PARTIAL | UNVERIFIED | 1.1 m | 32 s |
| Cicada afternoon | None | NOT FOUND | UNVERIFIED | None | None |
| Typhoon rain | Thermal | PARTIAL | UNVERIFIED | 1.1 m | 32 s |
| Pre-dawn mist | Thermal | PARTIAL | UNVERIFIED | 1.1 m | 32 s |

Selecting an optional mouth whistle can add acoustic evidence. It is not required by the mission. No acoustic signal is synthesized for an unconscious person.

## Display and verification

The current browser uses the [unified mission controller](unified-mission.md): one forest scene, aircraft pose and clock connect route planning, supported observation, modeled transport and departure. The thermal/visible camera renders the same forest. The sensor cards show the last supported observation; their default equations remain available in the standalone headless evaluator. The former forest URL redirects to this mission.

The navigation area also contains the [forest navigation and perching lab](forest-navigation.md): synthetic 3D A* planning, support-candidate rejection, attachment verification and injected failure recovery. It has its own state and canvas and does not drive the main scene or the separate ArduPilot SITL service.

Cards separate sensing from bird-to-relay communication. People remain identity-unverified. The GPS-denied panel and multicopter SITL viewer are independent; see navigation-demo.md.

Run `node simulator/test-sensors.cjs` and `node simulator/test-navigation.cjs`. Field validation remains required for sensing, visual identity, propagation, flight and perching. References retained in code include CWA climate normals, ISO 9613 acoustic absorption orders, Pearsons voice levels, ITU-R P.833, Weissberger attenuation, Semtech airtime, MR60BHA2 nominal ranges and thermal-camera specifications.
