# Simulator v2 to Unreal Engine

`ue_import_scene.py` places the simulator v2 scene in an Unreal Engine 5 level. It has not been run
in the Unreal Editor yet. `python test_ue_import.py` runs it against a stand-in for the `unreal`
module, including the real export in `sample_scene.json` (the default night scene, 25 actors). That
checks the parsing and the coordinate maths only.

This is separate from the [Unreal live thermal demo](../../../unreal-sim/README.md), which builds
its own scene over real terrain.

1. Install Unreal Engine 5.8 (the version the live thermal demo targets) and open a jungle level,
   for example Epic's free Electric Dreams sample from Fab. Electric Dreams recommends an RTX 3080 (10 GB) and 64 GB
   of RAM.
2. Edit > Plugins: enable **Python Editor Script Plugin**, then restart.
3. In the simulator, press **Export to Unreal**, copy the JSON and save it as `eagle_scene.json`.
4. In the Output Log (Python mode), run:
   `py "C:/path/ue_import_scene.py" "C:/path/eagle_scene.json"`

What the script does:
- Tags everything it places `eagle_sim`. Run it again to replace the previous import.
- Uses grey engine shapes until you map your own assets (MetaHumans, a bird mesh, outpost props)
  in the `ASSETS` dictionary at the top.
- Converts the axes: the simulator uses metres with north = -Z and east = +X. Unreal uses
  centimetres, so X = -z·100 (north), Y = x·100 (east), Z = y·100, and yaw = the exported compass
  heading.
- Sets the sun and fog. The Directional Light is pointed away from the exported sun. At night the
  script leaves the light alone, so set your level's moonlight by hand. Fog density is the
  simulator's value × 5, capped at 0.05; tune `FOG_SCALE` by eye.

**The Eagle model:** `../assets/eagle_drone_hawkEagle.glb` (also `_serpentEagle`, `_airframe`) is the
simulator's bird as a skinned mesh with clips `flap`, `glide`, `soar`, `perch_idle`, `fold`, `unfold`,
`land` and `takeoff` (metres, +Y up, origin at the body centre when perched). Drag it into the Content
Browser to get a Skeletal Mesh, Skeleton and Animation Sequences, wrap it in a Blueprint that plays
`perch_idle` (perched) or `flap` (flying), and map `eagle_perched` and `eagle_flying` to that Blueprint
in `ASSETS`. Details: [eagle-drone.md](../../../docs/eagle-drone.md#gltf-for-unreal-and-blender).

**Live link (next step):** the Remote Control API plugin accepts HTTP on port 30010
(`PUT /remote/object/property`, `PUT /remote/object/call`) and WebSocket on port 30020. A local
bridge can push time of day, weather and positions into the running level. Epic recommends using
it only on a LAN or VPN.

**Thermal view:** give the operator, soldiers and animals custom-stencil values. A post-process
material maps each value to the temperature from `../model.js` and applies an ironbow ramp. Render
the Eagle cam through a Scene Capture at 256x192.
