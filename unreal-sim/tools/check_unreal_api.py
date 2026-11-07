"""Check every Unreal Python name the Eagle Sim code uses against a UE Python stub.

The editor writes a stub of its whole Python API to <Project>/Intermediate/PythonStub/unreal.py
when Python developer mode is on (Edit > Editor Preferences > Python). The `unreal-stub` package
on PyPI carries one for UE 5.6. Cesium for Unreal names are not in a stock stub, so they are
checked against the list below, taken from the Cesium for Unreal v2.x headers.

    python tools/check_unreal_api.py path/to/unreal.py
"""
import ast
import builtins
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "EagleSim", "Content", "Python")

CESIUM = {
    # classes and enums
    "CesiumGeoreference", "Cesium3DTileset", "CesiumSunSky", "TilesetSource", "FROM_CESIUM_ION",
    # functions (UFUNCTIONs in CesiumGeoreference.h, Cesium3DTileset.h, CesiumSunSky.h)
    "get_default_georeference", "set_origin_longitude_latitude_height",
    "transform_unreal_position_to_longitude_latitude_height", "set_tileset_source",
    "set_ion_asset_id", "get_ion_asset_id", "set_ion_access_token", "get_load_progress", "update_sun",
    # properties (UPROPERTY BlueprintReadWrite)
    "enable_frustum_culling", "enforce_culled_screen_space_error", "show_credits_on_screen",
    "time_zone", "solar_time", "day", "month", "year", "use_daylight_saving_time",
}

PY_METHODS = set(dir(str)) | set(dir(list)) | set(dir(dict)) | set(dir(set)) | set(dir(tuple))
PY_METHODS |= {"reload", "dist", "monotonic", "hexdigest", "encode", "exists", "join", "normpath",
               "sha1", "format_exc", "tan", "radians", "degrees", "atan2", "hypot", "cos", "sin",
               "sqrt", "ceil", "exp", "now", "socket", "setsockopt", "sendto", "close", "inet_aton"}


def stub_names(path):
    src = open(path, encoding="utf-8-sig").read()
    classes = {}
    current = None
    for line in src.splitlines():
        m = re.match(r"^class (\w+)\(", line)
        if m:
            current = classes.setdefault(m.group(1), set())
            continue
        m = re.match(r"^def (\w+)\(", line)
        if m:
            classes.setdefault("__module__", set()).add(m.group(1))
            current = None
            continue
        if current is None:
            continue
        for pat in (r"^    def (\w+)\(", r"^    (\w+): ", r"^\s+- ``(\w+)``"):
            m = re.match(pat, line)
            if m:
                current.add(m.group(1))
    members = set()
    for names in classes.values():
        members |= names
    return classes, members


def own_names():
    names = set()
    for root, _, files in os.walk(os.path.join(PKG, "eagle_sim")):
        for f in files:
            if f.endswith(".py"):
                tree = ast.parse(open(os.path.join(root, f), encoding="utf-8").read())
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                        names.add(node.name)
                    elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
                        names.add(node.attr)
    return names


def ue_files():
    out = [os.path.join(PKG, "init_unreal.py")]
    ue = os.path.join(PKG, "eagle_sim", "ue")
    out += [os.path.join(ue, f) for f in sorted(os.listdir(ue)) if f.endswith(".py")]
    return out


def main(stub):
    classes, members = stub_names(stub)
    mine = own_names()
    problems, checked = [], 0
    for path in ue_files():
        tree = ast.parse(open(path, encoding="utf-8").read())
        rel = os.path.relpath(path, PKG)
        for node in ast.walk(tree):
            # unreal.X and unreal.X.Y
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "unreal":
                checked += 1
                name = node.attr
                ok = name in classes or name in classes.get("__module__", set()) or name in CESIUM
                if not ok:
                    problems.append("%s:%d unreal.%s" % (rel, node.lineno, name))
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)
                    and isinstance(node.value.value, ast.Name) and node.value.value.id == "unreal"):
                checked += 1
                cls, name = node.value.attr, node.attr
                ok = name in classes.get(cls, set()) or name in CESIUM
                if not ok:
                    problems.append("%s:%d unreal.%s.%s" % (rel, node.lineno, cls, name))
            # method calls on Unreal objects: must exist somewhere in the API
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                name = node.func.attr
                recv = node.func.value
                if isinstance(recv, ast.Name) and recv.id in ("unreal", "util", "scene", "materials",
                                                              "engine_mod", "cot", "settings", "layout",
                                                              "math", "os", "sys", "importlib", "traceback",
                                                              "hashlib", "flight", "detect", "mel", "S"):
                    continue
                if name in mine or name in PY_METHODS or name in dir(builtins):
                    continue
                checked += 1
                if name not in members and name not in CESIUM:
                    problems.append("%s:%d .%s()" % (rel, node.lineno, name))
                # property names passed as strings
                if name in ("set_editor_property", "get_editor_property") and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        checked += 1
                        prop = arg.value
                        if prop not in members and prop not in CESIUM:
                            problems.append("%s:%d property %r" % (rel, node.lineno, prop))
                if name == "set_editor_property" and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.JoinedStr) or isinstance(arg, ast.BinOp):
                        continue
    # properties built from strings in loops ("override_" + key)
    for key in ("motion_blur_amount", "bloom_intensity", "vignette_intensity"):
        for prop in (key, "override_" + key):
            checked += 1
            if prop not in classes.get("PostProcessSettings", set()):
                problems.append("PostProcessSettings.%s" % prop)
    print("checked %d references against %s" % (checked, stub))
    for p in problems:
        print("  NOT FOUND: " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
