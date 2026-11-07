"""A small stand-in for Unreal's `unreal` Python module, enough to run eagle_sim.ue without Unreal.

It is not Unreal. Terrain is a smooth slope 1,500 m above the ellipsoid; collision is that terrain
plus a sphere for every Sphere mesh actor whose collision profile blocks the trace channel; Cesium
tiles "stream in" after a few progress polls. Signatures follow the UE 5.6 Python API (checked
separately against a UE stub by tools/check_unreal_api.py).
"""
import math
import os
import tempfile

import numpy as np

LOG = []            # (level, text)
DIALOGS = []        # (title, text)
SCREEN = []         # print_string messages
LAUNCHED = []


# ---------------------------------------------------------------- value types
class Name(str):
    pass


class Text(str):
    pass


class Vector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __add__(self, o):
        return Vector(self.x + o.x, self.y + o.y, self.z + o.z)

    def __repr__(self):
        return "Vector(%.1f, %.1f, %.1f)" % (self.x, self.y, self.z)


class Rotator:
    def __init__(self, roll=0.0, pitch=0.0, yaw=0.0):
        self.roll, self.pitch, self.yaw = float(roll), float(pitch), float(yaw)


class LinearColor:
    def __init__(self, r=0.0, g=0.0, b=0.0, a=0.0):
        self.r, self.g, self.b, self.a = r, g, b, a


class Color:
    def __init__(self, b=0, g=0, r=0, a=0):
        self.r, self.g, self.b, self.a = r, g, b, a


def _enum(name, *members):
    return type(name, (), {m: "%s.%s" % (name, m) for m in members})


TraceTypeQuery = _enum("TraceTypeQuery", "TRACE_TYPE_QUERY1", "TRACE_TYPE_QUERY2")
DrawDebugTrace = _enum("DrawDebugTrace", "NONE", "FOR_ONE_FRAME", "FOR_DURATION", "PERSISTENT")
ComponentMobility = _enum("ComponentMobility", "STATIC", "STATIONARY", "MOVABLE")
CollisionEnabled = _enum("CollisionEnabled", "NO_COLLISION", "QUERY_ONLY", "PHYSICS_ONLY",
                         "QUERY_AND_PHYSICS")
AttachmentRule = _enum("AttachmentRule", "KEEP_RELATIVE", "KEEP_WORLD", "SNAP_TO_TARGET")
AppMsgType = _enum("AppMsgType", "OK", "YES_NO", "OK_CANCEL")
MaterialDomain = _enum("MaterialDomain", "MD_SURFACE", "MD_POST_PROCESS")
BlendableLocation = _enum("BlendableLocation", "BL_SCENE_COLOR_AFTER_TONEMAPPING",
                          "BL_SCENE_COLOR_BEFORE_BLOOM")
SceneTextureId = _enum("SceneTextureId", "PPI_SCENE_COLOR", "PPI_SCENE_DEPTH", "PPI_CUSTOM_DEPTH",
                       "PPI_POST_PROCESS_INPUT0", "PPI_CUSTOM_STENCIL")
CustomMaterialOutputType = _enum("CustomMaterialOutputType", "CMOT_FLOAT1", "CMOT_FLOAT3")
MaterialProperty = _enum("MaterialProperty", "MP_EMISSIVE_COLOR", "MP_BASE_COLOR", "MP_ROUGHNESS")
HorizTextAligment = _enum("HorizTextAligment", "EHTA_LEFT", "EHTA_CENTER", "EHTA_RIGHT")
VerticalTextAligment = _enum("VerticalTextAligment", "EVRTA_TEXT_TOP", "EVRTA_TEXT_CENTER",
                             "EVRTA_TEXT_BOTTOM", "EVRTA_QUAD_TOP")
ToolMenuStringCommandType = _enum("ToolMenuStringCommandType", "COMMAND", "PYTHON", "CUSTOM")
MultiBlockType = _enum("MultiBlockType", "NONE", "MENU_ENTRY", "SEPARATOR")
TilesetSource = _enum("TilesetSource", "FROM_CESIUM_ION", "FROM_URL")


class _Struct:
    def __init__(self, **kw):
        self.__dict__["_p"] = dict(kw)

    def set_editor_property(self, k, v):
        self._p[k] = v

    def get_editor_property(self, k):
        return self._p.get(k)

    def __getattr__(self, k):
        try:
            return self.__dict__["_p"][k]
        except KeyError:
            raise AttributeError(k)


class PostProcessSettings(_Struct):
    pass


class WeightedBlendable(_Struct):
    def __init__(self, weight=0.0, object=None):
        super().__init__(weight=weight, object=object)


class WeightedBlendables(_Struct):
    def __init__(self, array=None):
        super().__init__(array=list(array or []))


class CustomInput(_Struct):
    def __init__(self, input_name="None"):
        super().__init__(input_name=input_name)


# ---------------------------------------------------------------- objects
class _Obj:
    _n = 0

    def __init__(self, name=None):
        _Obj._n += 1
        self._name = name or "%s_%d" % (type(self).__name__, _Obj._n)
        self._props = {}

    def get_name(self):
        return self._name

    def get_path_name(self):
        return "/Temp/" + self._name

    def set_editor_property(self, k, v):
        self._props[k] = v

    def get_editor_property(self, k):
        if k in self._props:
            return self._props[k]
        raise AttributeError("%s has no property %s" % (type(self).__name__, k))


class StaticMesh(_Obj):
    pass


class SceneComponent(_Obj):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.visible = True
        self.mobility = ComponentMobility.STATIC

    def set_mobility(self, new_mobility):
        self.mobility = new_mobility

    def set_visibility(self, new_visibility, propagate_to_children=False):
        self.visible = bool(new_visibility)


class PrimitiveComponent(SceneComponent):
    def __init__(self, owner):
        super().__init__(owner)
        self.profile = "BlockAll"
        self.collision = CollisionEnabled.QUERY_AND_PHYSICS
        self.custom_depth = False
        self.stencil = 0
        self.cast_shadow = True
        self.materials = {}

    def set_collision_profile_name(self, collision_profile_name, update_overlaps=True):
        self.profile = str(collision_profile_name)

    def set_collision_enabled(self, new_type):
        self.collision = new_type

    def set_render_custom_depth(self, value):
        self.custom_depth = bool(value)

    def set_custom_depth_stencil_value(self, value):
        self.stencil = int(value)

    def set_cast_shadow(self, new_cast_shadow):
        self.cast_shadow = bool(new_cast_shadow)

    def set_material(self, element_index, material):
        self.materials[element_index] = material


class StaticMeshComponent(PrimitiveComponent):
    def __init__(self, owner):
        super().__init__(owner)
        self.mesh = None

    def set_static_mesh(self, new_mesh):
        self.mesh = new_mesh
        return True


class TextRenderComponent(PrimitiveComponent):
    def __init__(self, owner):
        super().__init__(owner)
        self.text = ""
        self.world_size = 26.0

    def set_text(self, value):
        self.text = str(value)

    def set_world_size(self, value):
        self.world_size = float(value)

    def set_text_render_color(self, value):
        assert isinstance(value, Color)
        self.color = value

    def set_horizontal_alignment(self, value):
        self.h_align = value

    def set_vertical_alignment(self, value):
        self.v_align = value


class CameraComponent(SceneComponent):
    def __init__(self, owner):
        super().__init__(owner)
        self.fov = 90.0
        self.aspect = 1.777778
        self.constrain = True
        self.pp_weight = 1.0
        self._props["post_process_settings"] = PostProcessSettings()

    def set_field_of_view(self, field_of_view):
        self.fov = float(field_of_view)

    def set_constraint_aspect_ratio(self, constrain_aspect_ratio):
        self.constrain = bool(constrain_aspect_ratio)

    def set_aspect_ratio(self, aspect_ratio):
        self.aspect = float(aspect_ratio)

    def set_post_process_blend_weight(self, post_process_blend_weight):
        self.pp_weight = float(post_process_blend_weight)


class Actor(_Obj):
    def __init__(self, world, location, rotation):
        super().__init__()
        self.world = world
        self.loc = location
        self.rot = rotation
        self.scale = Vector(1, 1, 1)
        self.label = self._name
        self.folder = ""
        self.parent = None
        self.rel_loc, self.rel_rot = Vector(), Rotator()
        self.hidden_in_game = False
        self._props["tags"] = []
        self.root_component = SceneComponent(self)
        self._props["root_component"] = self.root_component

    def get_actor_location(self):
        return self.loc

    def get_actor_rotation(self):
        return self.rot

    def set_actor_location_and_rotation(self, new_location, new_rotation, sweep, teleport):
        assert isinstance(new_location, Vector) and isinstance(new_rotation, Rotator)
        self.loc, self.rot = new_location, new_rotation
        return None

    def set_actor_scale3d(self, new_scale3d):
        self.scale = new_scale3d
        self.world.version += 1

    def set_actor_label(self, new_actor_label, mark_dirty=True):
        self.label = new_actor_label

    def get_actor_label(self, create_if_none=True):
        return self.label

    def set_folder_path(self, new_folder_path):
        self.folder = str(new_folder_path)

    def actor_has_tag(self, tag):
        return str(tag) in [str(t) for t in self._props["tags"]]

    def attach_to_actor(self, parent_actor, socket_name, location_rule, rotation_rule, scale_rule,
                        weld_simulated_bodies=True):
        self.parent = parent_actor
        return True

    def set_actor_relative_location(self, new_relative_location, sweep, teleport):
        self.rel_loc = new_relative_location

    def set_actor_relative_rotation(self, new_relative_rotation, sweep, teleport):
        self.rel_rot = new_relative_rotation

    def set_actor_hidden_in_game(self, new_hidden):
        self.hidden_in_game = bool(new_hidden)

    def set_is_temporarily_hidden_in_editor(self, is_hidden):
        self.hidden_in_editor = bool(is_hidden)


class StaticMeshActor(Actor):
    def __init__(self, world, location, rotation):
        super().__init__(world, location, rotation)
        self.static_mesh_component = StaticMeshComponent(self)
        self.root_component = self.static_mesh_component
        self._props["root_component"] = self.root_component


class CameraActor(Actor):
    def __init__(self, world, location, rotation):
        super().__init__(world, location, rotation)
        self.camera_component = CameraComponent(self)


class TextRenderActor(Actor):
    def __init__(self, world, location, rotation):
        super().__init__(world, location, rotation)
        self.text_render = TextRenderComponent(self)
        self.root_component = self.text_render
        self._props["root_component"] = self.root_component


# ---------------------------------------------------------------- Cesium
def ground_hae(x_m, y_m):
    """Fake terrain height above the ellipsoid, metres, at x east / y north of the site."""
    return 1500.0 + 0.05 * x_m + 0.02 * y_m + 3.0 * math.sin(x_m / 40.0) * math.cos(y_m / 55.0)


class CesiumGeoreference(Actor):
    def __init__(self, world, location=None, rotation=None):
        super().__init__(world, location or Vector(), rotation or Rotator())
        self.origin = (0.0, 0.0, 0.0)

    @classmethod
    def get_default_georeference(cls, world_context_object):
        w = _ED.world
        if w.georef is None:
            w.georef = cls(w)
            w.actors.append(w.georef)
        return w.georef

    def set_origin_longitude_latitude_height(self, target_longitude_latitude_height):
        v = target_longitude_latitude_height
        self.origin = (v.x, v.y, v.z)
        self.world.version += 1

    def transform_unreal_position_to_longitude_latitude_height(self, unreal_position):
        lon0, lat0, h0 = self.origin
        x, y, z = unreal_position.x / 100.0, -unreal_position.y / 100.0, unreal_position.z / 100.0
        lat = lat0 + y / 111320.0
        lon = lon0 + x / (111320.0 * math.cos(math.radians(lat0)))
        return Vector(lon, lat, h0 + z)


class Cesium3DTileset(Actor):
    def __init__(self, world, location, rotation):
        super().__init__(world, location, rotation)
        self.progress = 0.0
        self.asset_id = 0

    def set_tileset_source(self, in_source):
        self.source = in_source

    def set_ion_asset_id(self, in_asset_id):
        assert isinstance(in_asset_id, int)
        self.asset_id = in_asset_id

    def get_ion_asset_id(self):
        return self.asset_id

    def set_ion_access_token(self, in_access_token):
        self.token = in_access_token

    def get_load_progress(self):
        self.progress = min(100.0, self.progress + 7.0)
        if self.progress >= 60.0:
            self.world.tiles_loaded = True
        return self.progress


class CesiumSunSky(Actor):
    def update_sun(self):
        self.updated = True


# ---------------------------------------------------------------- world and physics
class World(_Obj):
    def __init__(self, path):
        super().__init__(path.split("/")[-1])
        self.path = path
        self.actors = []
        self.georef = None
        self.tiles_loaded = False
        self.version = 0
        self._spheres = None

    def get_path_name(self):
        return "%s.%s" % (self.path, self.path.split("/")[-1])

    def ground_z_cm(self, x_cm, y_cm):
        h0 = self.georef.origin[2] if self.georef is not None else 0.0
        return (ground_hae(x_cm / 100.0, -y_cm / 100.0) - h0) * 100.0

    def spheres(self):
        if self._spheres is None or self._spheres[0] != self.version:
            rows, actors = [], []
            for a in self.actors:
                if not isinstance(a, StaticMeshActor):
                    continue
                c = a.static_mesh_component
                if c.mesh is None or c.mesh.get_name() != "Sphere" or c.profile == "NoCollision":
                    continue
                rows.append((a.loc.x, a.loc.y, a.loc.z, 50.0 * a.scale.x))
                actors.append((a, c.profile))
            arr = np.array(rows, dtype=float).reshape(-1, 4)
            self._spheres = (self.version, arr, actors)
        return self._spheres[1], self._spheres[2]


class HitResult:
    def __init__(self, start, end, t, loc, actor):
        d = math.dist((start.x, start.y, start.z), (end.x, end.y, end.z)) * t
        n = Vector(0, 0, 1)
        self._t = (True, False, t, d, loc, loc, n, n, None, actor, None, "None", "None", 0, 0, 0, start, end)

    def to_tuple(self):
        return self._t


class SystemLibrary:
    traces = 0

    @classmethod
    def line_trace_single(cls, world_context_object, start, end, trace_channel, trace_complex,
                          actors_to_ignore, draw_debug_type, ignore_self=True, trace_color=None,
                          trace_hit_color=None, draw_time=5.0):
        cls.traces += 1
        w = _ED.world
        best_t, best_actor = None, None
        s = np.array([start.x, start.y, start.z])
        e = np.array([end.x, end.y, end.z])
        d = e - s
        if w.tiles_loaded:
            if abs(d[0]) < 1e-6 and abs(d[1]) < 1e-6:
                gz = w.ground_z_cm(s[0], s[1])
                if (s[2] - gz) * (e[2] - gz) <= 0 and abs(d[2]) > 0:
                    best_t = (gz - s[2]) / d[2]
            else:
                ts = np.linspace(0.0, 1.0, max(2, int(np.linalg.norm(d) / 50.0) + 2))
                px, py, pz = s[0] + d[0] * ts, s[1] + d[1] * ts, s[2] + d[2] * ts
                h0 = w.georef.origin[2] if w.georef is not None else 0.0
                gz = (1500.0 + 0.05 * px / 100 + 0.02 * (-py / 100)
                      + 3.0 * np.sin(px / 4000.0) * np.cos(-py / 5500.0) - h0) * 100.0
                below = np.nonzero(pz <= gz)[0]
                if len(below):
                    best_t = float(ts[below[0]])
        blocks = ("BlockAll", "UI") if trace_channel == TraceTypeQuery.TRACE_TYPE_QUERY1 else ("BlockAll",)
        arr, actors = w.spheres()
        if len(arr):
            ignore = set(id(a) for a in (actors_to_ignore or []))
            f = s[None, :] - arr[:, :3]
            a2 = float(d @ d)
            b = 2.0 * (f @ d)
            c = np.einsum("ij,ij->i", f, f) - arr[:, 3] ** 2
            disc = b * b - 4 * a2 * c
            ok = disc >= 0
            if ok.any():
                sq = np.sqrt(np.where(ok, disc, 0.0))
                t1 = (-b - sq) / (2 * a2)
                t2 = (-b + sq) / (2 * a2)
                t = np.where(t1 > 0, t1, t2)
                cand = ok & (t > 0) & (t < 1)
                for i in np.nonzero(cand)[0]:
                    actor, profile = actors[i]
                    if profile not in blocks or id(actor) in ignore:
                        continue
                    if best_t is None or t[i] < best_t:
                        best_t, best_actor = float(t[i]), actor
        if best_t is None:
            return None
        p = s + d * best_t
        return HitResult(start, end, best_t, Vector(*p), best_actor)

    @classmethod
    def print_string(cls, world_context_object, string="Hello", print_to_screen=True, print_to_log=True,
                     text_color=None, duration=2.0, key="None"):
        SCREEN.append(string)

    @classmethod
    def launch_url(cls, url):
        LAUNCHED.append(url)


# ---------------------------------------------------------------- editor
class _Editor:
    def __init__(self):
        self.levels = {}
        self.world = World("/Temp/Untitled_1")
        self.cam = (Vector(), Rotator())
        self.pilot = None
        self.game_view = False
        self.saved = 0


_ED = _Editor()


def reset():
    global _ED
    _ED = _Editor()
    del LOG[:], DIALOGS[:], SCREEN[:]
    _CALLBACKS.clear()


class EditorActorSubsystem:
    def spawn_actor_from_class(self, actor_class, location, rotation=None, transient=False):
        a = actor_class(_ED.world, location, rotation or Rotator())
        _ED.world.actors.append(a)
        _ED.world.version += 1
        return a

    def spawn_actor_from_object(self, object_to_use, location, rotation=None, transient=False):
        assert isinstance(object_to_use, StaticMesh)
        a = self.spawn_actor_from_class(StaticMeshActor, location, rotation)
        a.static_mesh_component.set_static_mesh(object_to_use)
        return a

    def get_all_level_actors(self):
        return list(_ED.world.actors)

    def destroy_actors(self, actors_to_destroy):
        gone = set(id(a) for a in actors_to_destroy)
        _ED.world.actors = [a for a in _ED.world.actors if id(a) not in gone]
        _ED.world.version += 1
        return True

    def destroy_actor(self, actor_to_destroy):
        return self.destroy_actors([actor_to_destroy])

    def select_nothing(self):
        pass


class LevelEditorSubsystem:
    def new_level(self, asset_path, is_partitioned_world=False):
        _ED.world = World(asset_path)
        _ED.levels[asset_path] = _ED.world
        return True

    def load_level(self, asset_path):
        if asset_path not in _ED.levels:
            return False
        _ED.world = _ED.levels[asset_path]
        return True

    def save_current_level(self):
        _ED.saved += 1
        return True

    def pilot_level_actor(self, actor_to_pilot, viewport_config_key="None"):
        assert isinstance(actor_to_pilot, Actor)
        _ED.pilot = actor_to_pilot

    def eject_pilot_level_actor(self, viewport_config_key="None"):
        _ED.pilot = None

    def get_pilot_level_actor(self, viewport_config_key="None"):
        return _ED.pilot

    def editor_set_game_view(self, game_view, viewport_config_key="None"):
        _ED.game_view = bool(game_view)

    def editor_set_viewport_realtime(self, realtime, viewport_config_key="None"):
        pass


class UnrealEditorSubsystem:
    def get_editor_world(self):
        return _ED.world

    def get_level_viewport_camera_info(self):
        if _ED.pilot is not None:
            return (_ED.pilot.loc, _ED.pilot.rot)
        return _ED.cam

    def set_level_viewport_camera_info(self, camera_location, camera_rotation):
        _ED.cam = (camera_location, camera_rotation)


_SUBS = {}


def get_editor_subsystem(subsystem):
    if subsystem not in _SUBS:
        _SUBS[subsystem] = subsystem()
    return _SUBS[subsystem]


# ---------------------------------------------------------------- assets and materials
class Material(_Obj):
    def __init__(self, name=None):
        super().__init__(name)
        self.expressions = []
        self.wired = {}


class MaterialInstanceConstant(_Obj):
    def __init__(self, name=None):
        super().__init__(name)
        self.parent, self.vectors, self.scalars = None, {}, {}


class MaterialFactoryNew(_Obj):
    pass


class MaterialInstanceConstantFactoryNew(_Obj):
    pass


class MaterialExpression(_Obj):
    OUTPUTS = ("",)
    INPUTS = ()

    def input_names(self):
        return self.INPUTS


class MaterialExpressionCustom(MaterialExpression):
    def input_names(self):
        return tuple(str(i.input_name) for i in self._props.get("inputs", []))


class MaterialExpressionSceneTexture(MaterialExpression):
    OUTPUTS = ("", "Color", "Size", "InvSize")


class MaterialExpressionVectorParameter(MaterialExpression):
    pass


class MaterialExpressionScalarParameter(MaterialExpression):
    pass


class MaterialExpressionMultiply(MaterialExpression):
    INPUTS = ("A", "B")


class MaterialExpressionTextureCoordinate(MaterialExpression):
    pass


class MaterialExpressionTime(MaterialExpression):
    pass


class EditorAssetLibrary:
    assets = {}
    dirs = set()

    @classmethod
    def does_asset_exist(cls, asset_path):
        return asset_path in cls.assets

    @classmethod
    def load_asset(cls, asset_path):
        if asset_path.startswith("/Engine/BasicShapes/"):
            return StaticMesh(asset_path.split(".")[-1])
        return cls.assets.get(asset_path)

    @classmethod
    def save_asset(cls, asset_to_save, only_if_is_dirty=True):
        assert asset_to_save in cls.assets, asset_to_save
        return True

    @classmethod
    def make_directory(cls, directory_path):
        cls.dirs.add(directory_path)
        return True

    @classmethod
    def does_directory_exist(cls, directory_path):
        return directory_path in cls.dirs


class AssetTools:
    def create_asset(self, asset_name, package_path, asset_class, factory, calling_context="None"):
        a = asset_class(asset_name)
        EditorAssetLibrary.assets["%s/%s" % (package_path, asset_name)] = a
        return a


class AssetToolsHelpers:
    @classmethod
    def get_asset_tools(cls):
        return AssetTools()


class MaterialEditingLibrary:
    @classmethod
    def create_material_expression(cls, material, expression_class, node_pos_x=0, node_pos_y=0):
        e = expression_class()
        material.expressions.append(e)
        return e

    @classmethod
    def connect_material_expressions(cls, from_expression, from_output_name, to_expression, to_input_name):
        return from_output_name in from_expression.OUTPUTS and to_input_name in to_expression.input_names()

    @classmethod
    def connect_material_property(cls, from_expression, from_output_name, property_):
        return True

    @classmethod
    def recompile_material(cls, material):
        material.compiled = True

    @classmethod
    def delete_all_material_expressions(cls, material):
        material.expressions = []

    @classmethod
    def set_material_instance_parent(cls, instance, new_parent):
        instance.parent = new_parent

    @classmethod
    def set_material_instance_vector_parameter_value(cls, instance, parameter_name, value, association=None):
        assert isinstance(value, LinearColor)
        instance.vectors[str(parameter_name)] = value
        return True

    @classmethod
    def set_material_instance_scalar_parameter_value(cls, instance, parameter_name, value, association=None):
        instance.scalars[str(parameter_name)] = float(value)
        return True

    @classmethod
    def update_material_instance(cls, instance):
        pass


# ---------------------------------------------------------------- menus
class ToolMenuEntry:
    def __init__(self, name="None", owner=None, type=None, **kw):
        self.name, self.type = name, type
        self.label = self.tip = self.command = None

    def set_label(self, label):
        self.label = str(label)

    def set_tool_tip(self, tool_tip):
        self.tip = str(tool_tip)

    def set_string_command(self, type, custom_type, string):
        self.command = (type, string)


class ToolMenu(_Obj):
    def __init__(self, name):
        super().__init__(name)
        self.sections = {}

    def add_sub_menu(self, owner, section_name, name, label, tool_tip=""):
        full = "%s.%s" % (self.get_name(), name)
        menu = ToolMenus.get().menus.setdefault(full, ToolMenu(full))
        return menu

    def add_section(self, section_name, label="", insert_name="None", insert_type=None, alignment=None):
        self.sections.setdefault(section_name, {})

    def add_menu_entry(self, section_name, args):
        self.sections.setdefault(section_name, {})[args.name] = args


class ToolMenus:
    _inst = None

    def __init__(self):
        self.menus = {"LevelEditor.MainMenu": ToolMenu("LevelEditor.MainMenu")}

    @classmethod
    def get(cls):
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    def find_menu(self, name):
        return self.menus.get(name)

    def refresh_all_widgets(self):
        pass


# ---------------------------------------------------------------- misc
class EditorDialog:
    @classmethod
    def show_message(cls, title, message, message_type, default_value=None, message_category=None):
        DIALOGS.append((str(title), str(message)))


class ScopedSlowTask:
    def __init__(self, work, desc="", enabled=True):
        self.work = work

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def make_dialog(self, can_cancel=False, allow_in_pie=False):
        pass

    def enter_progress_frame(self, work=1.0, desc=""):
        pass

    def should_cancel(self):
        return False


class ScopedEditorTransaction(ScopedSlowTask):
    def __init__(self, desc):
        super().__init__(0)


class Paths:
    @classmethod
    def project_content_dir(cls):
        return os.path.join(tempfile.gettempdir(), "eaglesim_fake", "Content")

    @classmethod
    def project_dir(cls):
        return os.path.join(tempfile.gettempdir(), "eaglesim_fake")


def log(arg):
    LOG.append(("info", str(arg)))


def log_warning(arg):
    LOG.append(("warning", str(arg)))


def log_error(arg):
    LOG.append(("error", str(arg)))


_CALLBACKS = {}


def register_slate_post_tick_callback(callable):
    handle = object()
    _CALLBACKS[handle] = callable
    return handle


def unregister_slate_post_tick_callback(handle):
    _CALLBACKS.pop(handle, None)


def tick(dt):
    """Test helper: one editor frame."""
    for fn in list(_CALLBACKS.values()):
        fn(dt)
    return len(_CALLBACKS)
