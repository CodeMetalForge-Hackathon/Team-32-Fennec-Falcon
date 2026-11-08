# Pi runtime: the on-board model and the decision to transmit

Technical reference for `eagle-kit/apps/eagle_runtime.py`, `eagle-kit/eagle/decide.py` and `eagle-kit/eagle/infer.py`: the one process that runs on the Eagle's Raspberry Pi 5 with the Hailo AI HAT+, finds the person, decides on board when it is sure, and only then sends one sealed report.

Every number here is labelled **modelled** (from a formula or the simulator), **assumed** (a starting value we chose) or **measured** (on this hardware). Nothing in this document has been measured on the Pi or in the field yet.

## What it is

```
 HT-203U thermal ──> warm-blob detector (CPU) ─────────────┐
                 └─> thermal person model (Hailo or CPU) ──┤ HEAT (+ shape score) / PERSON
 XVF3800 mic bar ──> briefed-code matcher + DOA, YAMNet ────┤ SOUND (code, cough, voice ...) + bearing
 Camera Module 3 ──> hailo_vision.py (Hailo: person         │ PERSON + IDENTITY level
   Wide NoIR         detector, CLIP; CPU: face) ─ vision.jsonl
 radar pellet, BLE, tag (optional) ─────────────────────────┘
                                   ▼
            life list (eagle/fusion.py), one update per sensor
                                   ▼
     decision engine (eagle/decide.py): P(him) per track, gates, EMCON
         WATCH ──> HOLD (+ REQUEST_RELOCATE) ──> SEND ──> REPORTED
                                   ▼
     FIX 48 B, AES-256-GCM, lowest power that reaches the Nest,
     two bursts of the same frame 30-120 s apart, then silence
                                   ▼
            LoRa modem ──> Nest ──> Cursor on Target ──> ATAK
```

On the field Pi it runs as `eagle-runtime.service` (`pi/field/run_runtime.sh`, recording toggled by the field page like `run_life.sh`) in place of `eagle-life.service`, next to Alex's `eagle-vision` (the camera on the Hailo), `eagle-nest`, `eagle-dashboard` and `eagle-hotspot` services (`pi/field/`). The runtime subclasses `apps/life_node.py`'s `LifeNode`: every sensor driver, the recorder (`--record`, `eagle/record.py`) and `--require` are reused unchanged; the LIFE broadcast every 30 s is replaced by the decision gate.

## Why

The kit had no on-board decision. `eagle_node.py` sends a FIX for every detection that has a position, and `life_node.py` sends the life list every 30 s whether or not anyone is there. Both break the rule simulator v2 is built on: stay silent until sure, then emit once. The P >= 0.95 gate existed only in the simulator's JavaScript. This moves it onto the Pi, adds the rules a real detector needs (corroboration, EMCON, one report per person), and runs the thermal person model on the Hailo or the CPU.

## Which model runs where

| Model | Input | Runs on | Rate | Source |
|---|---|---|---|---|
| Person detector + tracker (hailo-apps HEF) | Camera Module 3 Wide NoIR | Hailo NPU | written at 2 lines/s | `apps/hailo_vision.py` |
| CLIP ViT-B/32 image encoder (HEF) | person crops | Hailo NPU | per box | `eagle/identity.py` |
| SCRFD + ArcFace (buffalo_l ONNX) | faces >= 40 px | CPU, onnxruntime | per box with a face | `eagle/identity.py` |
| Thermal person, YOLOv8n 320 px | HT-203U 256 x 192 | CPU now; Hailo once compiled | every thermal frame (0.5/s) | `eagle/infer.py` (new) |
| YAMNet (TFLite) | XVF3800, 16 kHz | CPU | every 4 s window | `eagle/sounds.py` |
| Briefed-code matcher, SRP-PHAT DOA | XVF3800 raw mics | CPU, numpy | every 4 s window | `eagle/detect.py`, `eagle/doa.py` |
| Decision engine | life list | CPU, pure Python | every 0.5 s | `eagle/decide.py` (new) |

The NPU carries the camera models, which need it. The thermal model is small enough for the CPU at the rate the decision needs, so it runs there until a HEF exists; then `life.thermal.model.prefer: [hailo, ...]` moves it to the NPU with no code change. It joins the NPU through hailo-apps' `HailoInfer`, the wrapper `eagle/identity.py` already uses (round-robin scheduler, group `SHARED`).

**Thermal model caveats.** `fusion/models/thermal_person.onnx` is an Ultralytics YOLOv8n trained on HIT-UAV, aerial thermal footage from a drone 60-130 m up. On a crude aerial-style test frame it found a person at 0.55; on the kit's synthetic perch view of a lying body it scored 0.002 (both **measured** on a desktop CPU, synthetic frames). Expect it to under-fire from a perch until it is fine-tuned on perch footage, which `--record` collects. The runtime does not depend on it: a warm blob without a model box still counts as a faint shape. Its weights carry Ultralytics' AGPL-3.0 licence (ONNX metadata), unlike this repository's Apache-2.0.

## The decision

### Is it him?

Per track, log-odds that it is the briefed person rather than anyone else who may come into view:

- **Prior** = 1 / (1 + `mission.others_expected`). Default 6 others (simulator v2: patrol 4 + locals 2), so 1 in 7.
- Each clue multiplies the odds by a likelihood ratio LR = P(clue | him) / P(clue | anyone else). All **assumed** (simulator v2 values where it had one):

| Clue | LR | Kind |
|---|---|---|
| Thermal person shape, model score >= 0.6 / warm human-sized blob | 3 / 1.5 | best seen |
| Camera person box, lying / upright | 2 / 1 | best seen |
| Identity: appearance candidate / strong / face verified | 2 / 5 / 30 | best seen |
| Briefed code: per event before the rule holds (max 2) / rule holds | 3 each / 100 | events |
| Cough or groan (max 4), distress (max 2), breath sound (max 2) | 2, 2, 1.5 each | events |
| Radar breathing / radar no vital signs | 2 / 0.5 | best seen |
| Authenticated tag reply | 1000 | best seen |
| Alone (nobody within 30 m) / in a group (someone within 5 m) | 2 / 0.3 | every tick |
| Inside the search area | 2 | every tick |
| Still 30 min / still 2 h | 4 / 3 | every tick |
| Moving > 10 m in 60 s (when he is briefed as immobile) | 0.5 | every tick |

The LRs below 1 are what keep a resting patrol member or a warm decoy from being reported.

### May it transmit?

SEND only when all of these hold:

1. **P** >= 0.95 (default), 0.99 (strict).
2. **Corroboration:** the briefed-code rule (>= 3 code events within 180 s, bearings within +-10 deg of their mean, mean confidence >= 0.9 when the detector gives one) **or** >= 2 independent sensor modalities (thermal, acoustic, vision, radar, tag). One detector's false alarm can never report on its own.
3. **Dwell:** the track is >= 20 s old and was seen within the last 120 s.
4. **Fix:** circular error <= 200 m.
5. **EMCON:** no unknown person inside the burst's intercept radius of the perch, and no briefed threat inside it (`mission.threat_dist_m`). The radius comes from the power the radio would use and the kit's log-distance path loss: ~1,100 m at the planned 8 dBm for the default 800 m Nest link, ~300 m at the radio's minimum -9 dBm (**modelled**, listener at -115 dBm). So anyone the Eagle can see can hear it. If blocked: HOLD and log `REQUEST_RELOCATE`; the report goes once the Eagle has moved away (`touch ~/eagle/relocated`, later the autopilot), or, in default mode only, after 15 min for an injured person.

Then: one FIX, two bursts of the same frame 30-120 s apart (the Nest's replay window keeps one, so ATAK shows one marker), and the subject is REPORTED. It is re-sent only if he moved > 25 m, the identity level rose or the flags changed, at most 4 reports per hour. The FIX's circular error adds the Eagle's own position error (10 m without GPS, **assumed** from simulator v2).

Every decision carries a false-alarm label from what carried it: LOW (briefed code, face verified or tag), MEDIUM (stillness and sounds), HIGH (warm, still and alone only).

### Modes

| Mode | P | EMCON hold times out | Re-send | On the Pi's disk | Radio |
|---|---|---|---|---|---|
| training | 0.95 | sends (logged) | yes | positions | muted, except `--transport udp` to a bench Nest |
| default | 0.95 | sends after 15 min (injured) / 2 h | yes | positions | on |
| strict | 0.99 | never; waits for relocation | no | no positions; fix wiped after the second burst | on |

The runtime refuses to transmit on a real radio with the repository's public test keys (training mode and `--allow-test-keys` excepted). The field config (`pi/field/field.yaml`) sends over loopback UDP to the Nest on the same Pi, so no key change is needed for that.

### Bearing-only

Afternoon heat can hide him from the thermal camera, and a linear mic bar gives no elevation, so a code can be heard on a bearing with no track. Code events on one bearing form a bearing candidate; with the code rule and 5 min on the same bearing (LR 2) it reaches P 0.971 and is reported as the circle covering the ray from the perch to 150 m (centre 75 m out, ce 75 m). If a track appears on that bearing, the track carries the evidence instead.

### Results (modelled, synthetic scenes)

`python apps/eagle_runtime.py --sim <scene> --no-radio --speed 0`; the night, patrol, bearing and crowded timelines are compressed.

| Scene | Outcome | P | False-alarm label |
|---|---|---|---|
| night: warm, taps the code 3 times | SEND at the third tap (100 s) | 0.990 | LOW |
| cough: faint shape, coughs at 20/50/80/110 min | SEND at 80 min (simulator v2: 80 min) | 0.970 | MEDIUM |
| unconscious: warm and still 2.5 h, nothing else | never (one modality) | 0.923 (simulator v2: 0.92) | - |
| patrol: three people walking, talking | never | <= 0.13 | - |
| crowded: night + a stranger 10 m from the perch | HOLD, REQUEST_RELOCATE; default sends at 15 min | 0.980 | LOW |
| bearing: code heard, never seen | SEND at 5 min, ce 75 m | 0.971 | LOW |

End to end over UDP (`tests/test_runtime.py`): the Nest decrypts the 48-byte FIX, drops the second burst as a replay, and the position decodes to within 0.5 m of the scene's.

**False reports from the code rule (modelled).** If the code detector false-alarms lam times an hour, independently and on random bearings, the rule fires falsely about lam^3 T^2 / 2 x (20/360)^2 times an hour (T = 3 min): one per 1,350 days at lam = 2/h, 50 days at 6/h, 32 h at 20/h (`decide.false_report_rate_per_h`). Halving the detector's false alarms cuts false reports eight-fold. Real false alarms cluster (a bird repeating a call), so measure lam on night forest recordings.

## How to run it

On the Pi, from the repository, after Alex's field install (`pi/field/install.sh`, README section 10):

```bash
git fetch origin && git checkout pi-runtime          # or merge it into sar-integration
cd eagle-kit
PY=~/hailo-apps/venv_hailo_apps/bin/python           # the venv the field services use
# bench, two terminals: the whole chain with no sensors
$PY apps/nest_station.py --transport udp --print-cot
$PY apps/eagle_runtime.py --sim night --transport udp
# field: swap eagle-life for eagle-runtime (self-test first); the field page http://<pi>:8080 shows the gate
bash ../pi/field/use_runtime.sh                      # back: bash ../pi/field/use_runtime.sh off
```

Bring-up order:

1. `--sim night --transport udp` with `nest_station.py --print-cot`: the decrypted FIX prints.
2. Site setup and clap test from the field page (`pi/field/FIELD_TEST.md`); they land in `~/eagle/site.yaml`, which the runtime reads like life_node.
3. Enroll the person (field page "Enroll face"): a verified face is the strongest clue after the briefed code.
4. `bash pi/field/use_runtime.sh`; watch the field page's Verdict card ("Decision gate": action, P, reason) and `~/field_logs/runtime.log`.
5. Score the runs already recorded: `$PY apps/replay_decisions.py ~/field_logs/latest` works on life_node's recordings too. Record a volunteer and some strangers, then change settings until the gate opens for the volunteer only: `--set decide.lr.shape_clear=4`.
6. Before a real radio: new keys in the config and on the Nest; `decide.mode: default` or `strict` (`--mode` in the service's ExecStart, or a `decide:` block in the config).

### Thermal model on the Hailo

1. Collect frames: `--record` saves the thermal frames (`thermal_NNNN.npz`, eagle/record.py).
2. `python eagle-kit/apps/export_thermal_calib.py ~/field_logs/<dir> ... --out ~/calib_thermal` (8-bit white-hot PNGs, as the model sees them).
3. On an x86-64 Linux PC with the Hailo Dataflow Compiler and Model Zoo: `bash pi/compile_thermal_hef.sh hailo8 ~/calib_thermal` (`hailo8l` for the 13 TOPS board). The command follows Hailo community examples and has not been run here; the Model Zoo's yolov8n recipe assumes 640 px, so the 320 px model may need its YAML edited.
4. Copy the HEF to `~/eagle-models/thermal_person.hef` on the Pi. The runtime logs `thermal person: hailo (NPU)`.

## Repository map: what runs where

| Path | Role | Where |
|---|---|---|
| `eagle-kit/apps/eagle_runtime.py` | **The field process** (`eagle-runtime.service`): sensors, thermal model, life list, decision, radio | Pi |
| `eagle-kit/apps/hailo_vision.py` | Camera person boxes + identity on the Hailo -> `~/eagle/vision.jsonl` (`eagle-vision.service`) | Pi |
| `eagle-kit/apps/nest_station.py` | Base: decrypt, ATAK | Nest laptop |
| `eagle-kit/apps/enroll_target.py`, `fetch_models.py`, `keygen.py` | Setup tools | Pi / laptop |
| `eagle-kit/apps/replay_decisions.py`, `export_thermal_calib.py` | Calibration tools | anywhere |
| `eagle-kit/apps/life_node.py` | Signs-of-life node, LIFE every 30 s (`eagle-life.service`); the runtime replaces it in the field | Pi / bench |
| `eagle-kit/apps/eagle_node.py` | Acoustic find loop with the tag (version 1) | bench |
| `eagle-kit/apps/sim_demo.py`, `life_demo.py` | Simulations | laptop |
| `eagle-kit/eagle/` | The library (fusion, protocol, link, sensors; `decide`, `infer`, `scenes` new) | |
| `eagle-kit/firmware/` | LoRa modem, tag, radar bridges | ESP32 / Arduino |
| `fusion/app.py` | Three-sensor dashboard for demos; holds the same cameras, own verdict logic | Pi, demo only |
| `fusion/models/` | The ONNX models `eagle/infer.py` and `enroll_target.py` load | |
| `pi/setup_pi.sh`, `thermal_rtsp.py` | HT-203U quirk, RTSP stream; its readers are the kit's HTI driver | Pi |
| `pi/field/` | Field services (nest, life, runtime, vision, dashboard, hotspot), the phone page, `install.sh`, `use_runtime.sh`, `run_runtime.sh`, `field.yaml` | Pi |
| `pi/compile_thermal_hef.sh` | Thermal model ONNX -> HEF | x86 PC |
| `thermal-probe/` | USB thermal camera bring-up | laptop |
| `simulator/`, `unreal-sim/` | Mission simulators | browser / Unreal |

### Overlaps to clean up later

Left alone on purpose, to avoid conflicts with work in flight:

- **Two runtimes.** `fusion/app.py` and the kit both read the thermal camera, the Pi camera and the microphones, with separate detection code and verdicts; only one can hold a camera at a time. Field runs: `eagle_runtime.py` + `hailo_vision.py`; `fusion/app.py` for demos. `eagle-runtime.service` conflicts with `eagle-life`, `fusion` and `thermal`, so they never fight over a sensor.
- **`enroll_target.py` loads `fusion/app.py`** (via importlib) for its YOLO wrapper. `eagle/infer.py` now has a YOLOv8 decoder that could replace it.
- **YAMNet is loaded twice** (`fusion/app.py`, `eagle/sounds.py`), and **person detection runs on two stacks** (CPU ONNX in `fusion/app.py`, Hailo in `hailo_vision.py`).
- **Three thermal readers** (`pi/thermal_rtsp.py`, `thermal-probe/`, `eagle/thermal.py`, which reuses the first).
- **Score floors differ:** `hailo_vision.py --min-score` defaults to 0.3, `life.vision.min_score` to 0.4.
- **Before the repository goes public:** `pi/hotspot_on.sh` carries a default SSID and password in plain text (privacy checklist in [how-we-document.md](how-we-document.md)).

## Decisions and trade-offs

- **The runtime subclasses `LifeNode`** instead of editing it: every sensor driver, the recorder and `--require` are reused, and work on `life_node.py` does not conflict. The cost: a change inside `LifeNode.step` or `step_thermal` must be mirrored in `EagleRuntime` (done for `--record`, `--require` and the recording toggle, Alex's commits a9d4945, c6c3f73 and 4fbee88).
- **One life-list update per sensor.** In one batch, a heat blob and a sound from the same person start two tracks (the life list gives each track one detection per batch, and both kinds may start tracks), so evidence splits. The runtime updates per sensor, and the engine folds a track born within 1.5 m of a live one into it.
- **Stillness from track history, not the life list's filter.** The filter is tuned for people who barely move, so a walker spawns new tracks; walkers never build evidence, which is what the gate wants.
- **48-byte FIX unchanged.** The Nest, the firmware and the replay window work today. A shorter packet is a protocol change for the Nest and the firmware, not for the gate.
- **Identity only raises P** (LR >= 1), matching `eagle/fusion.py`'s rule that identity never suppresses a human.
- **One status page.** Decisions go into the recording's `events.jsonl` as `decision` events and Alex's field page (`pi/field/dashboard.py`, port 8080) shows them at the top of its Verdict card; the runtime keeps writing `life_list` events so the rest of the card still works. The page's Record and Site buttons restart whichever detection service is enabled (`node_unit()`), so they never swap the runtime back to life_node.

## Verification

- `python -m pytest -q` in `eagle-kit/`: 320 passed, 4 skipped, on top of `sar-integration` at 4fbee88. 41 tests are new (`test_decide.py` 24, `test_infer.py` 7, `test_runtime.py` 10). The 4 skips need pyroomacoustics, pymap3d or a YAMNet file.
- `test_decide.py` reproduces simulator v2 (code: TRANSMIT after the code; coughs: 80 min; unconscious: never at P 0.92) and checks every gate, EMCON, modes, re-send, budget, bearing-only, duplicate tracks and inheritance after occlusion.
- `test_infer.py`: both decoders on synthetic tensors, the real ONNX model on onnxruntime and OpenCV (agree to 1e-2), Hailo-to-CPU fallback.
- `test_runtime.py`: the night scene over UDP to the Nest, two identical bursts, the second dropped as a replay; training mutes a real radio; test keys refused; thermal model scoring; record then replay gives the same SEND, and `decide.lr.code=10` gives none; a life_node recording replays; thermal frames export as calibration images; the dashboard shows the gate.

## Open issues

- Nothing of this has run on the Pi, the Hailo, the HT-203U or the XVF3800. First run: the bring-up list above.
- Every LR is assumed. Calibrate with recordings and `replay_decisions.py`.
- The thermal model needs perch-view fine-tuning data, then a HEF (`compile_thermal_hef.sh` is unverified).
- Relocation is a flag file; the autopilot hook (MAVLink) is not written.
- The intercept radius uses the kit's log-distance model with no terrain; `site.dem` is not used for it yet.
- Briefed-code events carry no detector confidence yet, so the code rule's 0.9 mean-confidence test is skipped until `step_audio` passes one.
