# Eagle Unreal demo: live thermal search over real Taiwan terrain

A hawk-eagle-sized drone flies into a Taiwan mountain valley, searches three evasion-plan cells with
its thermal camera, finds a hidden person through gaps in the canopy, confirms them by circling,
perches on a tree above them, drops a comms pellet, authenticates them, and holds a relay orbit.
Every step appears on an ATAK map as Cursor-on-Target markers.

It runs inside the Unreal Editor (no Play button, no C++ and no Visual Studio). The mission logic is
plain Python and is tested without Unreal. Technical reference: [docs/unreal-sim.md](../docs/unreal-sim.md).

## What you need

| Item | Notes |
|---|---|
| Windows PC with a gaming-class GPU | Unreal 5 with Lumen. About 60 GB free for the engine |
| Unreal Engine **5.7** | Epic Games Launcher → Unreal Engine → Library → **+** → 5.7 → Install. 5.6 and 5.8 should also work (Cesium for Unreal supports all three); open the project with whichever you have |
| **Cesium for Unreal** plugin | Free on Fab. Add it to your library, then Launcher → Library → Fab Library → Cesium for Unreal → **Install to Engine** (5.7) |
| Free **Cesium ion** account | Streams Google Photorealistic 3D Tiles (ion asset 2275207). Sign up at ion.cesium.com |
| Internet | Terrain streams the first time; Cesium then keeps a disk cache |
| ATAK (optional) | ATAK-CIV on an Android phone, or WinTAK, on the same network as the PC |

## First run (about 20 minutes, mostly installs and shader compiling)

1. **Open the project.** Double-click `unreal-sim/EagleSim/EagleSim.uproject`. If Windows asks which
   engine, pick 5.7. The first open compiles shaders for several minutes.
2. **Sign in to Cesium ion.** Click **Cesium** in the toolbar (or Window → Cesium), then **Connect to
   Cesium ion** and approve it in the browser. When asked about a token, let it create a project
   default token. If Cesium later reports that asset 2275207 is not in your account, add **Google
   Photorealistic 3D Tiles** from the ion Asset Depot (or from the Cesium panel's Quick Add).
3. **Build the scene.** Main menu bar → **Eagle Sim → 1. Build Taiwan scene**. Open Window → Output Log
   to watch it. It creates the level `Maps/Taiwan`, looks down from 6.5 km until terrain arrives,
   moves the map origin to the ground, flies to the overview, waits for the tiles to sharpen, then
   plants the forest (304 trees), places the person, a muntjac and the eagle, adds four cameras and
   saves. One to three minutes, depending on the network.
4. **Run it.** **Eagle Sim → 2. Start mission.** The auto director cuts between cameras by phase.
   Press **F11** for full screen. Game view (no editor icons) is turned on for you; **G** toggles it.

Next time: open the project, **File → Open Level → Taiwan** (or run Build again), then Start.

## Run of show (default speed x3, about 2 minutes)

| Real time | Camera | What happens | ATAK |
|---|---|---|---|
| 0:00 | Chase | EAGLE-1 arrives from the east at 150 m above ground | EAGLE-1 track starts |
| 0:42 | Thermal (white-hot) | Cell A: soaring circles. A warm muntjac shows up and is rejected | |
| 1:06 | Thermal | Cell B: three glimpses of the person through canopy gaps; cue sent at 1:09 | Unknown marker **CUE** |
| 1:11 | Thermal | Confirm orbit at 60 m: views from every side stack up; **CONFIRMED** at 1:23 | **IP-1 (unauthenticated)** |
| 1:23 | Perch | Real time from here: lands on a tree 5 m from the person (1:34), tosses the pellet (1:36), challenge and reply (1:39) | IP-1 turns **friendly** |
| 1:45 | Overview | Relay orbit over the person; one-way bird note at 1:52 | EAGLE-1 circling |

The times are from the reference run (see [docs/unreal-sim.md](../docs/unreal-sim.md)); real terrain
shifts them a little. The bottom of every camera view shows the current step in words.

## Menu

| Entry | Does |
|---|---|
| 1. Build Taiwan scene | Level, Cesium terrain and sun, forest, targets, eagle, cameras (run again after big changes) |
| 2. Start mission / Restart / Pause / Stop | Runs the mission in the editor |
| Views | Auto director, Eagle thermal camera, Chase, Perch, Overview, Free camera, Game view on/off |
| Toggle cooperative SWIR beacon | Next start: the person answers the recognition pass with a beacon (faster find) |
| Speed x1 / x3 / x6 | Simulated seconds per real second (the perch, pellet and authentication always play at x1) |
| Send test CoT to ATAK | One EAGLE-TEST marker: check the network before you present |
| Re-place forest and props | Respawn everything except the terrain, for example after editing `config.py` |
| Reload Python scripts | Pick up edits to the Python files without restarting |

## Settings

Everything is in [`EagleSim/Content/Python/eagle_sim/core/config.py`](EagleSim/Content/Python/eagle_sim/core/config.py):
the site (`SITE_LON`, `SITE_LAT`), where the person and the animal hide, the search cells, the forest,
flight heights and speeds, the thermal camera (FLIR Boson+ 640 class, 32°), ATAK targets and camera
framing. Edit, then **Start** again: if a change moves the props, Start re-places them first. A
shorter ingress for rehearsals: `INGRESS_START_EN = (400.0, -300.0)`.

## ATAK

ATAK listens to SA multicast `239.2.3.1:6969` by default, which is what `COT_TARGETS` sends to.

1. Put the PC and the phone on the **same network**. Venue Wi-Fi often blocks multicast or isolates
   devices from each other; a phone hotspot or a small travel router avoids both.
2. **Eagle Sim → Send test CoT to ATAK.** An EAGLE-TEST marker should appear at the site in Taiwan.
   If Windows asks whether Unreal Editor may use the network, allow private networks.
3. Nothing arrives? Send straight to the phone instead: in ATAK, Settings → Network Preferences →
   Manage Inputs shows its UDP input port (often 4242). Set
   `COT_TARGETS = [("<phone IP>", 4242)]` and try again. On a laptop with VPN or virtual adapters, set
   `COT_MCAST_IFACE` to the Wi-Fi adapter's IP address.
4. Check the path without a phone: `python tools/cot_listen.py` on any computer on the network prints
   every marker it sees.

## Backup plan for the live demo

- **Record a video** of a good run beforehand (Win+G, Xbox Game Bar, or OBS).
- **Pre-load the terrain** the night before: run Build once on the same machine; Cesium caches tiles.
- **ATAK without Unreal:** `python tools/headless_demo.py` flies the same mission in plain Python and
  sends the same markers (add `--to <phone IP>:4242` for unicast, `--beacon`, `--speed 6`).

## Troubleshooting

| Problem | Fix |
|---|---|
| No **Eagle Sim** menu | Edit → Plugins: enable **Python Editor Script Plugin** and **Cesium for Unreal**, restart. The Output Log line starting `[EagleSim]` says what failed |
| "No terrain under the site" | Cesium panel shows you signed in; asset 2275207 is in your ion account; the PC is online. Then Build again |
| Build waits a long time | Tiles are still streaming; it carries on after `TILE_WAIT_S` (150 s) with whatever has loaded |
| Thermal view is plain grey, no white person | Project Settings → Rendering → Custom Depth-Stencil Pass must be **Enabled with Stencil** (the project sets it; restart the editor once). Then **Rebuild materials** |
| On-screen text or labels missing or mirrored | Set `TEXT_YAW_FIX_DEG = 180.0` in `config.py`, then **Re-place forest and props** |
| Scene is dark | The Cesium sun did not spawn; the build adds a plain sun instead. Check the Output Log |
| The mission slows down when you click away | The project turns off "Use Less CPU when in Background"; check Edit → Editor Preferences → General → Performance |
| A camera view is wrong or stuck | **Views → Free camera**, then **Auto director** again |

## Tests

```
pip install pytest numpy
python -m pytest unreal-sim/tests
```

The tests fly the mission without Unreal (core logic), then run the Unreal glue code end to end
against a stand-in for Unreal's `unreal` module (`tests/fake_unreal.py`): build, terrain streaming,
forest, start, fly to the relay orbit, camera cuts and CoT over UDP. `tools/check_unreal_api.py`
checks every Unreal Python name the code uses against an Unreal Python stub.

## Files

| Path | What it is |
|---|---|
| `EagleSim/EagleSim.uproject` | The Unreal project (Python, Editor Scripting Utilities, Cesium for Unreal) |
| `EagleSim/Config/` | Renderer settings (custom depth stencil for the thermal view), editor performance |
| `EagleSim/Content/Python/init_unreal.py` | Adds the Eagle Sim menu when the editor opens |
| `EagleSim/Content/Python/eagle_sim/core/` | Engine-free mission: flight, detection, CoT, layout, settings (`config.py`) |
| `EagleSim/Content/Python/eagle_sim/ue/` | Unreal side: scene builder, thermal material, cameras, menu |
| `tests/` | Mission tests and the Unreal smoke test |
| `tools/` | `headless_demo.py`, `cot_listen.py`, `check_unreal_api.py` |

Terrain: Google Photorealistic 3D Tiles via Cesium ion. Keep the on-screen data attribution
visible (the build turns it on). Your Cesium ion token is stored in `Content/CesiumSettings/`, which
is git-ignored: never commit it.
