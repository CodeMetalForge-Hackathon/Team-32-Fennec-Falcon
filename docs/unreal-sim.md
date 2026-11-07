# Unreal demo: technical reference

The Unreal demo shows the wide-area part of the mission that comes before the perch-and-listen fix
modelled in the [browser simulator](simulator.md): a hawk-eagle-sized drone searches a real Taiwan
mountain valley with a thermal camera, finds a person hidden under canopy, confirms them, perches
near them, delivers a comms pellet, authenticates them and holds a relay orbit, while ATAK shows each
step. How to install and run it: [unreal-sim/README.md](../unreal-sim/README.md).

All numbers on this page are **modelled** (design values or outputs of this code), not measured.

## Architecture

```
init_unreal.py ──> ue/menu.py ──> ue/app.py ──(one Slate tick per editor frame)──┐
                                     │                                          │
                     ue/scene.py: level, Cesium terrain and sun,                │
                     forest, targets, eagle, cameras, on-screen text            │
                                     │                                          v
core/mission.py (state machine) <── engine interface ──> ue/engine.py (UEEngine) ──> Unreal actors
core/flight.py  core/detect.py  core/cot.py  core/layout.py  core/config.py         line traces
                                       │                                            Cesium georeference
                                       └──> UDP ──> ATAK (CoT)
```

- **`eagle_sim/core`** has no Unreal dependency. The mission talks to an *engine* object with
  ten methods: `ground`, `visible`, `set_eagle`, `aim`, `set_beacon`, `pellet`, `label`, `to_lla`,
  `send`, `log` (plus `status`). The tests and `tools/headless_demo.py` use a simple engine
  (`core/headless.py`); Unreal uses `ue/engine.py`.
- **`eagle_sim/ue`** runs in the editor world through one `unreal.register_slate_post_tick_callback`.
  No Play-In-Editor session, C++ module or Blueprint is needed. Cameras are shown by piloting
  CameraActors in the level viewport, so each camera's own field of view and post-process settings
  apply (the thermal look exists only on the Eagle's camera).
- The mission sub-steps at 0.1 simulated seconds or less per step, so detection sampling does not
  depend on frame rate. Actors move once per editor frame.

## Coordinates

| Frame | Units | Axes |
|---|---|---|
| Mission (`core`) | metres | x east, y north, z up, from the georeference origin |
| Unreal with a Cesium georeference | centimetres | +X east, +Y **south**, +Z up |

`ue/util.py` converts (`to_ue`, `from_ue`). Mission headings are counter-clockwise from east; Unreal
yaw is clockwise, so yaw = −heading. The build first places the georeference at `SITE_LON`,
`SITE_LAT`, height 0, finds the ground under the site with a line trace, then moves the origin onto
that ground so heights in the scene are small. `to_lla` uses Cesium's
`TransformUnrealPositionToLongitudeLatitudeHeight`, so CoT points carry real latitude, longitude
and height above the ellipsoid.

## Mission

| Phase | What the bird does | Key settings (config.py) |
|---|---|---|
| INGRESS | Straight leg from 1.5 km south-east at 150 m above ground, dropping to 100 m near the cells | `INGRESS_START_EN`, `INGRESS_AGL_M` |
| SEARCH | Soaring circle r = 100 m at 100 m above ground around each evasion-plan cell, camera on the cell centre, 1.25 laps per cell | `SEARCH_*`, `SEARCH_CELLS_EN` |
| CONFIRM | Circle r = 40 m at 60 m around the cue, camera on the cue, up to 2.5 laps | `CONFIRM_*` |
| PERCH | Flies to the top of the cover tree nearest the person, descending so it arrives as the descent ends; motor off | |
| DROP | Tosses an 80 g comms pellet (LoRa + e-paper, notional) to land 1.5 m from the person | |
| AUTH | Challenge 20 B, reply 21 B (notional message sizes); identity confirmed, status and route exchanged | |
| HOLD | Relay orbit r = 90 m at 120 m above the person; one-way bird note after 20 s | `HOLD_*` |

Flight: cruise 12 m/s (design point for a 1.65 m, 2.4 kg airframe). Bank angle for a coordinated
turn, atan(v²/(g·r)): 8.4° at r = 100 m, 20.2° at r = 40 m. Climb 3 m/s, descent 4 m/s.

## Detection model

| Quantity | Value | How |
|---|---|---|
| Camera | 640 × 512, 32° horizontal field of view (FLIR Boson+ 640 class) | `CAM_*` |
| Instantaneous field of view | 32° / 640 = 0.873 mrad | |
| Detection range for a person | **172 m** | 3 pixels across a 0.45 m torso: 0.45 / (3 × 0.000873) |
| In frame | Inside the 32° × 25.6° frustum around the aim point | `detect.in_frame` |
| Line of sight | Unreal line trace (Visibility channel) from the camera to head, torso and legs; trees block it, terrain blocks it | `UEEngine.visible` |
| Glimpse | A frame with the person in range, in frame and in sight, at least 1.5 s after the last glimpse | `detect.GlimpseTracker` |
| Cue | 3 glimpses within 12 s; cue placed 5–12 m from the true position (random, seeded) | `CUE_*` |
| Confirm | Airborne optical sectioning: count a view each time the person is seen from a new 30° azimuth bin; confirm at 8 views across at least 4 bins | `CONFIRM_VIEWS`, `CONFIRM_BINS`, `AZ_BINS` |
| False target | A muntjac (0.9 m, 0.6 m tall) in cell A is detected, labelled and not cued | `ANIMAL_EN` |
| Cooperative answer (optional) | SWIR beacon flashing at 1 Hz during the first lap of a cell, seen by a 90° × 70° cue camera out to 1,500 m; fixes the cue to 3 m | `COOPERATIVE_SIGNAL`, `BEACON_RANGE_M` |

The forest is 300 trees scattered within 260 m of the cells plus 4 cover trees 4–7 m from the
person, each a trunk and 3–4 canopy spheres (radius 2.8–5 m, tops 13–22 m), seeded (`SEED = 32`).
The same layout code places the Unreal trees and the test geometry, so the Unreal line traces and the
tests see the same canopy.

## Unreal scene

| Piece | Built from |
|---|---|
| Terrain | Google Photorealistic 3D Tiles from Cesium ion (asset 2275207) on a `Cesium3DTileset`; frustum culling off and culled tiles kept at a coarser level so terrain probes work anywhere in the area; on-screen data attribution on |
| Sun and sky | `CesiumSunSky`: UTC+8, 10:00 solar time, 7 Nov 2026, no daylight saving. A plain directional light and sky are added if it cannot spawn |
| Forest | Engine basic shapes. Collision profile `UI`: blocks the Visibility channel (canopy hides the person) and lets the Camera channel through (terrain probes ignore trees) |
| Person, muntjac, beacon | Basic shapes writing Custom Depth with stencil 1, 2 and 3 |
| Eagle | Six shapes attached to one movable root: 1.65 m span, banks into turns |
| Cameras | Eagle thermal (32°, 1.25 aspect, letterboxed like the sensor), chase (60°, smoothed, 14 m behind and 4 m above), perch (85°, placed 20 m from the person where both the person and the perch are in sight), overview (55°, 650 m south-east and 520 m above the cells) |
| On-screen text | TextRenderActors 60 cm in front of each camera: status line and the current step in two lines; stencil 4 |
| Labels | Cells, EAGLE-1, CUE, MUNTJAC, IP-1, COMMS PELLET; turned to the viewer and scaled with distance so they keep a constant size on screen |

Settings that matter for rendering are in `EagleSim/Config/DefaultEngine.ini`: `r.CustomDepth=3`
(Custom Depth with stencil, required for the thermal view) and no static lighting.

### Thermal view

`M_EagleThermal` is a post-process material (after tonemapping) built by script with one Custom
HLSL node. It reads the scene colour, scene depth, custom depth and custom stencil:

- Background grey level 0.10 + 0.42 × luminance (vegetation reads cool, sunlit ground warmer); sky 0.03–0.08.
- Stencil 1 (person) 0.97, stencil 2 (animal) 0.82, stencil 3 and 4 (beacon, text) 1.0, **only where
  scene depth is no closer than custom depth − 30 cm**, so a warm body shows only through gaps.
- ±2.5% per-pixel noise and a light vignette.

This is a visualisation of what the camera would see, not radiometry. Whether a body actually stands
out depends on time of day and canopy temperature; the browser simulator models that and finds the
contrast can vanish in the early afternoon.

## Cursor-on-Target messages

All UDP, by default to the ATAK SA multicast group 239.2.3.1:6969 (`COT_TARGETS`).

| uid | type | callsign | When | ce |
|---|---|---|---|---|
| EAGLE-1 | a-f-A-M-F-Q (friendly UAV) | EAGLE-1 | Every 2 s real time | 5 m |
| CUE-1 | a-u-G (unknown ground) | CUE | Cue from 3 glimpses | 2 × cue error |
| IP-1 | a-u-G | IP-1 (unauthenticated) | CONFIRMED | 3 m |
| IP-1 | a-f-G-U-C (friendly ground unit) | IP-1 | Reply authenticated | 3 m |
| EAGLE-TEST | a-f-A-M-F-Q | EAGLE-TEST | Menu: Send test CoT | |

The same uid IP-1 is sent twice, so ATAK replaces the unknown marker with the friendly one. In the
concept these messages travel over the relay and satellite path in the browser simulator; here they
go straight to ATAK.

## Reference run

`tools/headless_demo.py --fast` (seed 32, the default layout, simple sloping ground; the Unreal run
uses real terrain, so its times differ a little):

| Simulated time | Event |
|---|---|
| 0 s | Launch; ingress at 150 m |
| 125.8 s | Cell A search; muntjac logged and rejected at 125.9 s |
| 191.3 s | Cell A clear after 1.25 laps |
| 199.1 s | Cell B search; glimpses at 199.2, 200.8 and 206.9 s |
| 206.9 s | Cue sent, 6 m from the true position |
| 214.3 s | Confirm orbit |
| **248.5 s** | **CONFIRMED**: 8 views over 120° |
| 259.7 s | Perched 5 m from the person |
| 261.7 s | Pellet dropped |
| **266.2 s** | **Authenticated** |
| 275.4 s | Relay orbit |

With the cooperative SWIR beacon the cue comes at 156.4 s and confirmation at 230.5 s (18 s sooner
in this small three-cell area; the gain grows with the area to search). One run sends 49 CoT events
up to the relay orbit.

## Verification

- `python -m pytest unreal-sim/tests`: 18 tests. Mission logic: orbit geometry, bank angle, the
  172 m detection range, frame test, glimpse spacing, view spread, valid CoT XML, UDP delivery that
  survives a bad target, forest layout, a full mission to the relay orbit, the beacon being faster,
  the bird never dropping below ground or jumping, and confirmation needing several sides.
- **Unreal glue code, end to end without Unreal:** `tests/test_ue_smoke.py` runs `eagle_sim.ue`
  against `tests/fake_unreal.py`, a stand-in for the `unreal` module with simple terrain, sphere
  collision and Cesium tiles that stream in. It checks the build (level, origin moved to the ground,
  1,220 tree parts, stencils, the thermal material on the Eagle camera), then flies the mission to the
  relay orbit and checks camera cuts, labels, on-screen text, the pellet landing next to the person
  and all CoT types arriving over UDP.
- **Unreal API names:** `tools/check_unreal_api.py` checked all 332 Unreal Python references against
  the UE 5.6 Python API stub; Cesium names were checked against the Cesium for Unreal v2 headers.
- `tools/headless_demo.py` into `tools/cot_listen.py` over UDP: 53 events received.

**Not yet verified:** a run in a real Unreal Editor. The first time it runs, check that the menu
appears, the build completes, the person shows white in the thermal view only through gaps, on-screen
text faces the camera (`TEXT_YAW_FIX_DEG` flips it if not) and the markers appear in ATAK. Then add
screenshots to `docs/images/`.

## Limitations

- Trees are simple shapes on top of photographic terrain; in these mountains Google's tiles carry the
  forest as imagery on the ground surface, not as 3D trees.
- Detection is geometric (range, frame, line of sight). No detection probability, false-alarm
  statistics, weather, or thermal contrast physics.
- No wind, thermals or battery model; the bird flies at a constant speed.
- The pellet exchange is scripted; message sizes are notional.
- CoT goes straight to ATAK over the local network rather than over the relay and satellite path.
