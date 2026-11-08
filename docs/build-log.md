# Build log

## 2026-11-08 · README pitch, mission walkthrough and silent media

Added a first-visit pitch above the existing README text, which is unchanged: product, niche, fit, relevance, a ten-step operator-down walkthrough, what runs today, and links to the branches that hold the Pi and simulator v2 work. The text follows the Fennec pitch deck, linked from the README.

Media is in `docs/images/pitch/`. Every video was re-encoded with no audio stream and no source metadata, and the originals are not in the repository. README clips are GIFs because GitHub does not play repository video files inline; silent MP4s sit beside them. Two bystanders in the face-check screenshot are blurred. The concept film is rendered, not field footage.

Verification: `ffprobe` reports zero audio streams on every clip; the README was rendered through GitHub's README API and all local links resolve. Numbers are labelled computed, modelled or design; no new measurements were made. Open: the deck's 98% perching figure is left out until it has a source.

## 2026-11-07 · Unified Eagle mission and twelve viewpoints

Connected the forest state machine to the existing Eagle observation, fix and relay/storage model through one mission clock. Reused the original Eagle/person actors and thermal/visible/ID rendering in the same dense Fushan forest, with an explicit z-up to Three.js y-up transform. Supported observation now uses actual modeled separation/height and a same-scene thermal center-ray gate. Eagle holds its perch through the observation/transport timeline, then departs. Blocked routes and pose loss cannot generate observations. Failed modeled links retain observations; coordinator markers require delivery. The default person remains silent and identity unverified.

Added twelve shared-state viewpoints, pause/step/reset/speed/export, canopy/collider inspection and a local mission map with modeled fix uncertainty. The former forest route redirects to the main mission. The independent explanatory survey and forest modal are not mounted; their motion components remain unit-testable. Locally vendored the existing r128 OrbitControls/Sky/BufferGeometryUtils so browser scripts do not depend on CDNs.

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

## 2026-11-07 · Initial demonstrations

Established browser environment/acoustic/thermal/local-fix models and the mission display. Established Unreal Editor Python integration with Cesium terrain, canopy occlusion, multi-view thermal observations and CoT markers. Requirements and outputs are superseded by the current no-person-equipment implementation above. Current technical references are simulator.md and unreal-sim.md.

## Entry format

Record the problem, resulting behavior, implementation choices, validation evidence and remaining limits. Distinguish modeled outputs and field measurements. Follow how-we-document.md.
