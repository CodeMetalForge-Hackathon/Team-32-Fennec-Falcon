"""ue_import_scene.py - place the Eagle simulator's exported scene in an Unreal Engine 5 level.

Not yet run inside the Unreal Editor. test_ue_import.py runs it against a stand-in for the
`unreal` module, which checks the parsing, the axis conversion and the actor list only.

Run inside the Unreal Editor:
  1. Edit > Plugins: enable "Python Editor Script Plugin", then restart.
  2. In the simulator, press "Export scene for Unreal" and save the JSON as eagle_scene.json
     (or try sample_scene.json from this folder).
  3. Open your jungle level (for example Epic's free Electric Dreams sample from Fab).
  4. Output Log > Python:  py "C:/path/ue_import_scene.py" "C:/path/eagle_scene.json"
     (or set SCENE_JSON below and use Tools > Execute Python Script).

What it does
  * Places one actor per exported object: the Eagle, the operator, patrols and troops, the
    outpost and its direction-finding mast, the relay bird, the parachute, locals and animals.
    Each starts as a grey engine shape. Map your own assets (MetaHumans, a bird mesh, outpost
    props) in ASSETS and they are used instead.
  * Points the level's Directional Light away from the exported sun and sets the Exponential
    Height Fog density. At night (sun below the horizon) it leaves the light alone: set your
    level's moonlight by hand.
  * Moves the editor viewport to the simulator's camera.
  * Tags everything it places "eagle_sim" and deletes the previous import first, so you can
    re-export from the simulator and run it again.

Coordinates: the simulator exports metres with three.js axes (+Y up, north = -Z, east = +X).
Unreal uses centimetres with X north, Y east, Z up:  X = -z*100, Y = x*100, Z = y*100.
Unreal yaw = the exported compass heading_deg (0 = north, 90 = east). If your level's valley
sits somewhere else, set ORIGIN_OFFSET_CM.
"""
import json
import sys

import unreal

SCENE_JSON = r"C:/temp/eagle_scene.json"   # used when no path is passed on the command line
TAG = "eagle_sim"
ORIGIN_OFFSET_CM = (0.0, 0.0, 0.0)          # (X, Y, Z) added to every position
FOG_SCALE = 5.0                             # three.js FogExp2 density (per metre) -> Unreal Fog Density
FOG_MAX = 0.05                              # top of Unreal's usual Fog Density range

# kind -> your asset (a Blueprint class or a Static Mesh). Unlisted kinds use STAND_INS.
ASSETS = {
    # "operator": "/Game/MetaHumans/Pilot/BP_Pilot.BP_Pilot",
    # "eagle_perched": "/Game/Birds/BP_EagleDrone.BP_EagleDrone",   # Blueprint around ../assets/eagle_drone_hawkEagle.glb
}

SHAPE = "/Engine/BasicShapes/{0}.{0}"        # engine basic shapes are 1 m across, pivot at the centre
# kind -> (shape, size in metres (x, y, z), pitch in degrees, anchor). The anchor is where the
# exported point sits on the object: "base" (feet or ground), "center" or "top".
STAND_INS = {
    "eagle_perched": ("Sphere",   (0.4, 0.4, 0.7),   0.0,   "center"),   # exported at the body centre, 0.26 m above the branch (the .glb origin too)
    "eagle_flying":  ("Sphere",   (0.7, 1.8, 0.3),   0.0,   "center"),
    "relay_bird":    ("Sphere",   (0.8, 2.0, 0.3),   0.0,   "center"),
    "operator":      ("Cylinder", (0.5, 0.5, 1.8),   90.0,  "base"),    # replaced by POSTURE
    "patrol":        ("Cylinder", (0.5, 0.5, 1.75),  0.0,   "base"),
    "troop":         ("Cylinder", (0.5, 0.5, 1.75),  0.0,   "base"),
    "local":         ("Cylinder", (0.5, 0.5, 1.65),  0.0,   "base"),
    "outpost":       ("Cube",     (10.0, 10.0, 2.5), 0.0,   "base"),
    "df_mast":       ("Cylinder", (0.4, 0.4, 16.6),  0.0,   "top"),
    "parachute_lkl": ("Cone",     (7.0, 7.0, 2.0),   180.0, "center"),
    "muntjac":       ("Cube",     (0.9, 0.3, 0.6),   0.0,   "base"),
    "macaque":       ("Sphere",   (0.5, 0.4, 0.5),   0.0,   "base"),
    "wild_boar":     ("Cube",     (1.25, 0.5, 0.7),  0.0,   "base"),
    "sambar":        ("Cube",     (1.9, 0.6, 1.2),   0.0,   "base"),
    "default":       ("Cube",     (1.0, 1.0, 1.0),   0.0,   "center"),
}
POSTURE = {   # operator: size and pitch by posture (lying = cylinder on its side)
    "lying":    ((0.5, 0.5, 1.8), 90.0),
    "sitting":  ((0.5, 0.5, 1.0), 0.0),
    "standing": ((0.5, 0.5, 1.8), 0.0),
}

actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def to_ue(x, y, z):
    """Simulator metres (three.js axes) -> Unreal centimetres (X north, Y east, Z up)."""
    ox, oy, oz = ORIGIN_OFFSET_CM
    return unreal.Vector(-float(z) * 100.0 + ox, float(x) * 100.0 + oy, float(y) * 100.0 + oz)


def heading_of(obj):
    """Compass heading in degrees, which is Unreal yaw."""
    if "heading_deg" in obj:
        return float(obj["heading_deg"]) % 360.0
    # older exports only had the three.js yaw: +z (south) turned by yaw -> heading 180 - yaw
    return (180.0 - float(obj.get("yaw_deg", 0.0))) % 360.0


def stand_in(obj):
    kind = obj.get("kind", "default")
    shape, size, pitch, anchor = STAND_INS.get(kind, STAND_INS["default"])
    if kind == "operator":
        size, pitch = POSTURE.get(obj.get("posture"), POSTURE["lying"])
    return shape, size, pitch, anchor


def anchor_offset_cm(size, pitch, anchor):
    """Vertical shift from the exported point to the shape's centre."""
    vertical = size[0] if abs(abs(pitch) - 90.0) < 1e-6 else size[2]
    half = vertical * 50.0
    return {"base": half, "top": -half}.get(anchor, 0.0)


def clear_previous():
    old = [a for a in actors.get_all_level_actors() if a.actor_has_tag(TAG)]
    for a in old:
        actors.destroy_actor(a)
    return len(old)


def spawn(obj):
    kind = obj.get("kind", "default")
    loc = to_ue(obj.get("x", 0.0), obj.get("y", 0.0), obj.get("z", 0.0))
    yaw = heading_of(obj)
    path = ASSETS.get(kind)
    if path:
        rot = unreal.Rotator(roll=0.0, pitch=0.0, yaw=yaw)
        cls = unreal.EditorAssetLibrary.load_blueprint_class(path)   # None if it is not a Blueprint
        if cls is not None:
            actor = actors.spawn_actor_from_class(cls, loc, rot)
        else:
            actor = actors.spawn_actor_from_class(unreal.StaticMeshActor, loc, rot)
            actor.static_mesh_component.set_static_mesh(unreal.EditorAssetLibrary.load_asset(path))
    else:
        shape, size, pitch, anchor = stand_in(obj)
        loc = unreal.Vector(loc.x, loc.y, loc.z + anchor_offset_cm(size, pitch, anchor))
        actor = actors.spawn_actor_from_class(unreal.StaticMeshActor, loc,
                                              unreal.Rotator(roll=0.0, pitch=pitch, yaw=yaw))
        actor.static_mesh_component.set_static_mesh(unreal.EditorAssetLibrary.load_asset(SHAPE.format(shape)))
        actor.set_actor_scale3d(unreal.Vector(*size))
    actor.set_actor_label("EagleSim_" + str(obj.get("id", kind)))
    actor.set_editor_property("tags", [unreal.Name(TAG), unreal.Name(kind)])
    return actor


def set_sun_and_fog(scene):
    sun = scene.get("sun") or {}
    fog = scene.get("fog")
    elevation = float(sun.get("elevation_deg", 45.0))
    if sun and elevation <= 0.0:
        unreal.log_warning(f"Sun is {elevation:.0f} deg below the horizon at hour {scene.get('hour')}: "
                           "Directional Light left as it is; set your level's moonlight by hand.")
    for a in actors.get_all_level_actors():
        if sun and elevation > 0.0 and isinstance(a, unreal.DirectionalLight):
            # light travels away from the sun: compass bearing azimuth + 180, tilted down by the elevation
            yaw = (float(sun.get("azimuth_deg", 0.0)) + 180.0) % 360.0
            a.set_actor_rotation(unreal.Rotator(roll=0.0, pitch=-elevation, yaw=yaw), False)
        if fog is not None and isinstance(a, unreal.ExponentialHeightFog):
            density = fog.get("density") if isinstance(fog, dict) else fog
            try:
                a.get_editor_property("component").set_fog_density(min(FOG_MAX, float(density) * FOG_SCALE))
            except Exception as exc:  # noqa: BLE001 - property names differ between engine versions
                unreal.log_warning(f"fog not set: {exc}")


def set_camera(scene):
    cam = scene.get("camera") or {}
    if "position" not in cam:
        return
    p = cam["position"]
    t = cam.get("target", p)
    loc = to_ue(*p)
    rot = unreal.MathLibrary.find_look_at_rotation(loc, to_ue(*t))
    try:
        unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(loc, rot)
    except Exception:  # noqa: BLE001 - older editors
        unreal.EditorLevelLibrary.set_level_viewport_camera_info(loc, rot)


def main(path):
    with open(path, "r", encoding="utf-8") as fh:
        scene = json.load(fh)
    if scene.get("units", "m") not in ("m", "metres", "meters"):
        unreal.log_warning("scene units are not metres; positions may be off")
    removed = clear_previous()
    made = [spawn(o) for o in scene.get("actors", [])]
    set_sun_and_fog(scene)
    set_camera(scene)
    model = scene.get("model") or {}
    unreal.log(f"Eagle scene: removed {removed}, placed {len(made)} actors | hour {scene.get('hour')}, "
               f"weather {scene.get('weather')} | model: {model.get('status')} {model.get('decision')}, "
               f"output {model.get('chosen_output')}, error {model.get('error_m')} m")
    return made


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else SCENE_JSON)
