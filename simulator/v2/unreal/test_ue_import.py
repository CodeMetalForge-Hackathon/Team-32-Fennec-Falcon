"""Run ue_import_scene.py against a stand-in for Unreal's `unreal` module.

    python test_ue_import.py

This checks the JSON parsing, the axis conversion, anchors, headings, sun, fog, camera and
re-import. It cannot show that the script works inside the Unreal Editor.
"""
import importlib
import json
import math
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "sample_scene.json")

# A scene in the simulator's export format (see exportScene() in the simulator page).
SCENE = {
    "units": "m", "hour": 14, "weather": "clear",
    "sun": {"azimuth_deg": 135, "elevation_deg": 40.0},
    "fog": {"type": "exp2_height", "density": 0.004, "color": "#8fa3a8", "thicker_below_m": 60},
    "camera": {"position": [0.0, 50.0, 100.0], "target": [0.0, 50.0, 0.0], "fov_deg": 50},
    "model": {"status": "REPORTED", "decision": "TRANSMIT", "chosen_output": "moveAway", "error_m": 10.1},
    "actors": [
        {"id": "eagle-1", "kind": "eagle_perched", "x": 5.0, "y": 108.0, "z": -12.0, "yaw_deg": 0, "heading_deg": 90, "perch": "subcanopy", "perch_h_m": 8},
        {"id": "operator", "kind": "operator", "x": 10.0, "y": 100.0, "z": -20.0, "yaw_deg": 0, "heading_deg": 180, "posture": "lying", "state": "injured", "carries": "nothing"},
        {"id": "patrol-a-1", "kind": "patrol", "x": 200.0, "y": 90.0, "z": 40.0, "yaw_deg": 0, "heading_deg": 270},
        {"id": "df-mast", "kind": "df_mast", "x": 300.0, "y": 160.0, "z": -250.0, "yaw_deg": 0, "heading_deg": 0},
        {"id": "relay", "kind": "relay_bird", "x": 0.0, "y": 400.0, "z": 0.0, "yaw_deg": 0, "heading_deg": 45, "altitude_m": 300},
        {"id": "mystery", "kind": "unknown_thing", "x": 1.0, "y": 2.0, "z": 3.0, "yaw_deg": 90},
    ],
}


def fake_unreal():
    u = types.ModuleType("unreal")
    u.warnings, u.logs = [], []

    class Vector:
        def __init__(self, x=0.0, y=0.0, z=0.0):
            self.x, self.y, self.z = float(x), float(y), float(z)

    class Rotator:
        def __init__(self, roll=0.0, pitch=0.0, yaw=0.0):
            self.roll, self.pitch, self.yaw = float(roll), float(pitch), float(yaw)

    class Name(str):
        pass

    class Actor:
        def __init__(self, loc=None, rot=None):
            self.location, self.rotation, self.scale, self.label, self.props = loc, rot, None, None, {}

        def actor_has_tag(self, tag):
            return str(tag) in [str(t) for t in self.props.get("tags", [])]

        def set_editor_property(self, key, value):
            self.props[key] = value

        def get_editor_property(self, key):
            return self.props[key]

        def set_actor_label(self, label):
            self.label = label

        def set_actor_scale3d(self, v):
            self.scale = v

        def set_actor_rotation(self, rot, teleport):
            self.rotation = rot

    class MeshComponent:
        mesh = None

        def set_static_mesh(self, mesh):
            self.mesh = mesh
            return True

    class StaticMeshActor(Actor):
        def __init__(self, *a):
            super().__init__(*a)
            self.static_mesh_component = MeshComponent()

    class DirectionalLight(Actor):
        pass

    class FogComponent:
        density = None

        def set_fog_density(self, d):
            self.density = d

    class ExponentialHeightFog(Actor):
        def __init__(self, *a):
            super().__init__(*a)
            self.props["component"] = FogComponent()

    class EditorActorSubsystem:
        def __init__(self):
            self.level = [DirectionalLight(), ExponentialHeightFog()]

        def get_all_level_actors(self):
            return list(self.level)

        def spawn_actor_from_class(self, cls, loc, rot):
            a = cls(loc, rot)
            self.level.append(a)
            return a

        def destroy_actor(self, a):
            self.level.remove(a)
            return True

    class UnrealEditorSubsystem:
        camera = None

        def set_level_viewport_camera_info(self, loc, rot):
            self.camera = (loc, rot)

    subsystems = {}

    class EditorAssetLibrary:
        @staticmethod
        def load_asset(path):
            return path

        @staticmethod
        def load_blueprint_class(path):
            return None

    class MathLibrary:
        @staticmethod
        def find_look_at_rotation(a, b):
            dx, dy, dz = b.x - a.x, b.y - a.y, b.z - a.z
            return Rotator(0.0, math.degrees(math.atan2(dz, math.hypot(dx, dy))), math.degrees(math.atan2(dy, dx)))

    for k, v in dict(Vector=Vector, Rotator=Rotator, Name=Name, StaticMeshActor=StaticMeshActor,
                     DirectionalLight=DirectionalLight, ExponentialHeightFog=ExponentialHeightFog,
                     EditorActorSubsystem=EditorActorSubsystem, UnrealEditorSubsystem=UnrealEditorSubsystem,
                     EditorAssetLibrary=EditorAssetLibrary, MathLibrary=MathLibrary).items():
        setattr(u, k, v)
    u.get_editor_subsystem = lambda cls: subsystems.setdefault(cls, cls())
    u.log = lambda msg: u.logs.append(msg)
    u.log_warning = lambda msg: u.warnings.append(msg)
    return u


def load(scene):
    """Fresh fake engine + fresh import of the script; returns (module, unreal, json path)."""
    u = fake_unreal()
    sys.modules["unreal"] = u
    sys.path.insert(0, HERE)
    sys.modules.pop("ue_import_scene", None)
    mod = importlib.import_module("ue_import_scene")
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(scene, fh)
    return mod, u, path


def by_label(u, label):
    return next(a for a in u.get_editor_subsystem(u.EditorActorSubsystem).level if a.label == label)


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol


def test_places_every_actor_with_tags():
    mod, u, path = load(SCENE)
    made = mod.main(path)
    assert len(made) == len(SCENE["actors"])
    for a, o in zip(made, SCENE["actors"]):
        assert a.actor_has_tag("eagle_sim") and a.actor_has_tag(o["kind"])
        assert a.label == "EagleSim_" + o["id"]
    print("ok places every actor, tagged, labelled")


def test_axes_and_anchors():
    mod, u, path = load(SCENE)
    mod.main(path)
    op = by_label(u, "EagleSim_operator")   # lying: cylinder on its side, 0.5 m tall -> centre 25 cm up
    assert (op.location.x, op.location.y, op.location.z) == (2000.0, 1000.0, 10025.0)
    assert close(op.rotation.pitch, 90.0) and close(op.rotation.yaw, 180.0)
    mast = by_label(u, "EagleSim_df-mast")  # exported point is the top of a 16.6 m mast
    assert close(mast.location.z, 16000.0 - 830.0) and close(mast.location.x, 25000.0) and close(mast.location.y, 30000.0)
    relay = by_label(u, "EagleSim_relay")   # centre anchor, heading 45 -> yaw 45
    assert close(relay.location.z, 40000.0) and close(relay.rotation.yaw, 45.0)
    odd = by_label(u, "EagleSim_mystery")   # unknown kind -> default cube; no heading -> 180 - yaw
    assert odd.static_mesh_component.mesh.endswith("Cube.Cube") and close(odd.rotation.yaw, 90.0)
    # due north in three.js (-z) is +X in Unreal; due east (+x) is +Y
    n, e = mod.to_ue(0, 0, -1), mod.to_ue(1, 0, 0)
    assert (n.x, n.y, e.x, e.y) == (100.0, 0.0, 0.0, 100.0)
    print("ok axes: north -> +X, east -> +Y; anchors base/top/center; headings")


def test_sun_fog_camera():
    mod, u, path = load(SCENE)
    mod.main(path)
    level = u.get_editor_subsystem(u.EditorActorSubsystem).level
    light = next(a for a in level if isinstance(a, u.DirectionalLight))
    assert close(light.rotation.pitch, -40.0) and close(light.rotation.yaw, 315.0)   # sun in the SE shines toward the NW
    fog = next(a for a in level if isinstance(a, u.ExponentialHeightFog))
    assert close(fog.props["component"].density, 0.02)
    loc, rot = u.get_editor_subsystem(u.UnrealEditorSubsystem).camera
    assert close(loc.x, -10000.0) and close(rot.yaw, 0.0) and close(rot.pitch, 0.0)  # camera south of target, looking north
    print("ok sun from behind the light, fog scaled, viewport camera")


def test_night_leaves_light_alone():
    night = dict(SCENE, hour=2, sun={"azimuth_deg": 20, "elevation_deg": -50.0})
    mod, u, path = load(night)
    mod.main(path)
    light = next(a for a in u.get_editor_subsystem(u.EditorActorSubsystem).level if isinstance(a, u.DirectionalLight))
    assert light.rotation is None and any("below the horizon" in w for w in u.warnings)
    print("ok night: light untouched, warning given")


def test_reimport_replaces():
    mod, u, path = load(SCENE)
    mod.main(path)
    mod.main(path)
    level = u.get_editor_subsystem(u.EditorActorSubsystem).level
    assert len(level) == 2 + len(SCENE["actors"]) and "removed 6" in u.logs[-1]
    print("ok re-import deletes the previous copies")


def test_sample_scene_if_present():
    if not os.path.exists(SAMPLE):
        print("skip sample_scene.json not found")
        return
    with open(SAMPLE, encoding="utf-8") as fh:
        scene = json.load(fh)
    mod, u, path = load(scene)
    made = mod.main(path)
    kinds = sorted({o["kind"] for o in scene["actors"]})
    unknown = [k for k in kinds if k not in mod.STAND_INS]
    assert len(made) == len(scene["actors"]) and not unknown, unknown
    print(f"ok sample_scene.json: {len(made)} actors, kinds {', '.join(kinds)}")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("all passed")
