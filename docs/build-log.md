# Build log

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
