# Eagle Jungle Simulator v2

Version 2 of the simulator covers the case where the isolated person carries nothing that
transmits. The Eagle finds him by heat (primary) and sound (secondary), an onboard evidence gate
decides when it is sure enough to report, and the report leaves from somewhere that does not point
back at him. Version 1, with a tag, is [`../index.html`](../index.html).

Full reference: [`docs/simulator-v2.md`](../../docs/simulator-v2.md).

## Run it

Open `index.html` in Chrome, Edge, Safari or Firefox (on GitHub: **Download raw file**, then open
the download). It needs internet on first load for three.js r128 and fonts. Pick a scenario on the
left, then press **Run mission**.

The Eagle is a flapping-wing drone at Mountain Hawk-Eagle size: it flies in, lands on the perch and
folds its wings at the start of **Run mission**. Pick its disguise under *Input · the Eagle*. To look at
the model on its own, open `eagle_viewer.html`; for every part inside it, `eagle_viewer.html#cutaway` ([reference](../../docs/eagle-drone.md)).

**Present ▶** (or **P**) runs the judges' demo: ten cards with live numbers from the model; → next,
← back, A auto-advance, Esc exit. Run sheet: [`docs/demo-runbook.md`](../../docs/demo-runbook.md).

To draw on a computer with a stronger graphics card and show it on a laptop on the same network,
run `node serve.mjs` on the GPU computer and open `http://localhost:8080/#host` there, then open
the address it prints, ending in `#view`, on the laptop. Details and limits:
[Remote GPU](../../docs/simulator-v2.md#remote-gpu).

The model on its own, with Node 18 or newer:

```bash
node model_table.js    # every preset and what-if case: ranges, evidence over time, decision, way out, accuracy, search
```

```js
const M = require('./model.js');
const r = M.evaluate({ ...M.defaults(), hour: 14, elevation_m: 300 });   // the 14:00 cicada chorus
console.log(r.processing.decision, r.output.chosen, r.timeToReport_s);    // TRANSMIT moveAway 872
```

## Files

| File | What it is |
|---|---|
| `index.html` | The app in one file. Built by `build.js`; do not edit it by hand |
| `index.src.html` | Page source with a `/*@@MODEL@@*/` marker where the model goes |
| `model.js` | Physics and decision model: no dependencies, Node or browser (`window.EagleModel`) |
| `build.js` | `node build.js` inlines `model.js` and `eagle_drone.js` into `index.src.html` and writes `index.html`; also builds `eagle_viewer.html` |
| `eagle_drone.js` | The Eagle as a 3D model: Mountain Hawk-Eagle size, three skins (hawk-eagle, serpent eagle, bare airframe), flight, landing, wing fold and take-off. [Reference](../../docs/eagle-drone.md) |
| `eagle_viewer.html` | Standalone viewer for the model (built from `eagle_viewer.src.html`); `#cutaway` opens the cutaway |
| `cutaway.js` | The cutaway view and parts list, inlined into `eagle_viewer.html` by `build.js` |
| `assets/` | The model as `.glb` files with eight animation clips, for Unreal or Blender |
| `tools/export_glb.js` | Rebuilds `assets/*.glb` from `eagle_drone.js` in headless Chromium |
| `model_table.js` | Prints the model's numbers for every preset and what-if case |
| `serve.mjs` | Serves the page on the local network and connects a GPU computer to a laptop (remote GPU) |
| `tests/` | Headless browser checks for the presenter and remote GPU: `npm install`, `npx playwright install chromium`, then `npm test` |
| `unreal/` | Places an exported scene in Unreal Engine 5 ([README](unreal/README.md)) |

## What the model covers

| Stage | How | Main settings |
| --- | --- | --- |
| Input: heat | Clothing surface against sun-warmed leaves, pixels on a 0.45 m target, line of sight through the canopy (Beer-Lambert above it, exponential decay in the understory) | `hour`, `elevation_m`, `weather`, `camera`, `perch`, `LAI`, `understoryVis_m` |
| Input: sound | Source level, spherical spreading, air and foliage loss (ISO 9613-2) and microphone-array gain against ambient noise; heard at 6 dB above noise | `opSignal`, `mics`, `hour`, `weather` |
| Processing | Prior odds times one likelihood ratio per clue. TRANSMIT once P reaches `decisionP`, otherwise keep watching. Each decision gets a false-alarm rating | `enemy`, `locals`, `animals`, `ai`, `decisionP`, `opState`, `radarPellet` |
| Output | Four ways out: burst from the perch, whisper to the relay overhead, fly away then burst, courier. For each: latency, where the emission happens, how far away it can be heard (Weissberger in the forest, ITU-R P.833 above it) and the risk. Picks the lowest-risk way that meets the urgency (15 min if he is hurt, 2 h if he can move) | `output`, `moveAway_km`, `relayAlt_m`, `relayDist_m`, `radio`, `enemyDist_m`, `enemyDetector` |
| Accuracy | Thermal pixel or sound bearing with the downward angle, plus the bird's own position error without GPS | `heading`, `secondPerch` |
| Search | Perch stops needed to cover the circle around the parachute | `lklRadius_km`, `birds`, `dwell_min` |

Presets: `night` (default), `cicadaDay`, `typhoon`, `preDawn`, `target` (inside the target area,
troops 120 m away). `evaluate()` returns `env`, `input`, `processing`, `output`, `search`, `find`,
`timeline` and `timeToReport_s`.

## Assumptions to tune on recorded data

- Every likelihood ratio in `PARAMS.ai.lr`.
- The tap code (85 dB at 1 m) and cough (70 dB) source levels.
- Ambient noise by hour and weather.
- Canopy clumping (0.7).
- The bird's own position error: 10 m with visual-inertial odometry and map matching, 30 m with
  a magnetometer.
