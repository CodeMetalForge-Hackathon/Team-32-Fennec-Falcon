"""Builds the Taiwan level and finds its pieces again later.

Building waits twice, because Cesium streams the terrain over the network:
  1. look straight down from 6.5 km until the site's ground answers a line trace, then move
     the georeference origin onto that ground so the numbers stay small;
  2. move to the overview pose, wait for the tiles to sharpen, then place everything.
"""
import hashlib
import math
import os

import unreal

from ..core import layout
from ..core.mission import Scenario
from . import materials, util

LEVEL = "/Game/EagleSim/Maps/Taiwan"
F_CESIUM = util.FOLDER + "/Cesium"
F_FOREST = util.FOLDER + "/Forest"
F_TARGETS = util.FOLDER + "/Targets"
F_EAGLE = util.FOLDER + "/Eagle"
F_CAMS = util.FOLDER + "/Cameras"
F_LABELS = util.FOLDER + "/Labels"

CAMERAS = ("thermal", "chase", "scene", "overview")
CESIUM_ROLES = ("tileset", "sun", "sun_fallback")
OSD_DISTANCE_CM = 60.0      # on-screen text sits this far in front of each camera

# Eagle parts in the bird's frame: forward, right, up (metres). 1.65 m span design point.
EAGLE_PARTS = [
    # shape, offset, size, (yaw, pitch, roll), material, name
    ("Sphere", (0.00, 0.00, 0.00), (0.62, 0.20, 0.19), (0, 0, 0), "Eagle", "body"),
    ("Sphere", (0.33, 0.00, 0.05), (0.15, 0.13, 0.13), (0, 0, 0), "EagleHead", "head"),
    ("Cone", (0.43, 0.00, 0.03), (0.05, 0.05, 0.07), (0, -90, 0), "Beak", "beak"),
    ("Cube", (0.02, 0.43, 0.03), (0.34, 0.74, 0.025), (0, 0, -6), "Eagle", "right wing"),
    ("Cube", (0.02, -0.43, 0.03), (0.34, 0.74, 0.025), (0, 0, 6), "Eagle", "left wing"),
    ("Cube", (-0.42, 0.00, 0.00), (0.30, 0.24, 0.02), (0, 0, 0), "Eagle", "tail"),
]


# ------------------------------------------------------------ helpers
def cesium_ready():
    return all(hasattr(unreal, n) for n in ("Cesium3DTileset", "CesiumGeoreference", "CesiumSunSky"))


def cells_center(cfg):
    cells = cfg.SEARCH_CELLS_EN
    return (sum(c[0] for c in cells) / len(cells), sum(c[1] for c in cells) / len(cells))


def signature(cfg):
    """Changes whenever a setting that moves the props changes."""
    keys = ("SITE_LON", "SITE_LAT", "OPERATOR_EN", "OPERATOR_YAW_DEG", "ANIMAL_EN",
            "SEARCH_CELLS_EN", "INGRESS_START_EN", "TREES", "TREE_RADIUS_M", "COVER_TREES",
            "SEED", "CAM_HFOV_DEG", "CAM_PIXELS_H", "CAM_PIXELS_V")
    text = repr([getattr(cfg, k) for k in keys])
    return "ES_SIG:" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def probes(cfg):
    """Points that must have terrain under them before the scene is placed."""
    pts = [("site", (0.0, 0.0)), ("person", cfg.OPERATOR_EN), ("animal", cfg.ANIMAL_EN),
           ("ingress start", cfg.INGRESS_START_EN)]
    pts += [("cell %s" % "ABC"[i % 3], c) for i, c in enumerate(cfg.SEARCH_CELLS_EN)]
    return pts


def terrain_z(world_obj, x, y, ignore=()):
    """Terrain height (metres, mission frame) under x, y. Trees do not block this trace."""
    hit = util.trace(world_obj, util.to_ue(x, y, 9000.0), util.to_ue(x, y, -9000.0),
                     util.terrain_channel(), ignore)
    return util.from_ue(hit[0])[2] if hit else None


def overview_pose(cfg, ground_fn):
    """Camera south-east of the cells, looking down at them; kept clear of any ridge."""
    cx, cy = cells_center(cfg)
    g = ground_fn(cx, cy)
    g = 0.0 if g is None else g
    a = math.radians(-50.0)
    px = cx + cfg.OVERVIEW_BACK_M * math.cos(a)
    py = cy + cfg.OVERVIEW_BACK_M * math.sin(a)
    pz = g + cfg.OVERVIEW_UP_M
    tz = ground_fn(px, py)
    if tz is not None:
        pz = max(pz, tz + 150.0)
    return (px, py, pz), (cx, cy, g)


def level_file():
    return os.path.join(unreal.Paths.project_content_dir(), "EagleSim", "Maps", "Taiwan.umap")


def open_level():
    """Make the demo level the open level, creating it the first time."""
    current = util.world().get_path_name()
    if current.startswith(LEVEL + "."):
        return True
    ls = util.level_sub()
    if os.path.exists(level_file()) or unreal.EditorAssetLibrary.does_asset_exist(LEVEL):
        return bool(ls.load_level(LEVEL))
    return bool(ls.new_level(LEVEL))


def save_level():
    try:
        return bool(util.level_sub().save_current_level())
    except Exception as exc:
        util.warn("save failed: %s" % exc)
        return False


# ------------------------------------------------------------ Cesium terrain and sun
def _first_of_class(cls):
    for a in util.actors_sub().get_all_level_actors():
        if isinstance(a, cls):
            return a
    return None


def setup_cesium(cfg):
    """Georeference at the site, Google Photorealistic 3D Tiles from Cesium ion, and a sun."""
    if not cesium_ready():
        raise RuntimeError("The Cesium for Unreal plugin is not enabled in this project.")
    world_obj = util.world()
    georef = unreal.CesiumGeoreference.get_default_georeference(world_obj)
    georef.set_origin_longitude_latitude_height(unreal.Vector(cfg.SITE_LON, cfg.SITE_LAT, 0.0))

    tilesets = [a for a in util.actors_sub().get_all_level_actors()
                if isinstance(a, unreal.Cesium3DTileset)]
    tileset = None
    for ts in tilesets:
        try:
            if int(ts.get_ion_asset_id()) == int(cfg.TILESET_ION_ASSET_ID):
                tileset = ts
        except Exception:
            pass
    if tileset is None:
        tileset = util.actors_sub().spawn_actor_from_class(unreal.Cesium3DTileset,
                                                           unreal.Vector(0, 0, 0), util.rot())
        tileset.set_tileset_source(unreal.TilesetSource.FROM_CESIUM_ION)
        tileset.set_ion_asset_id(int(cfg.TILESET_ION_ASSET_ID))
    if len(tilesets) > 1:
        util.warn("more than one Cesium tileset in the level; hide the extra ones if terrain flickers")
    if cfg.CESIUM_ION_TOKEN:
        tileset.set_ion_access_token(cfg.CESIUM_ION_TOKEN)
    # Keep tiles outside the view loaded (coarser) so terrain probes work anywhere in the area.
    for key, value in (("enable_frustum_culling", False),
                       ("enforce_culled_screen_space_error", True),
                       ("show_credits_on_screen", True)):        # Google requires visible attribution
        try:
            tileset.set_editor_property(key, value)
        except Exception as exc:
            util.warn("tileset setting %s: %s" % (key, exc))
    label = "Google Photorealistic 3D Tiles" if cfg.TILESET_ION_ASSET_ID == 2275207 else "Cesium terrain"
    util.finish(tileset, label, "tileset", F_CESIUM)

    sun = _first_of_class(unreal.CesiumSunSky)
    if sun is None:
        try:
            sun = util.actors_sub().spawn_actor_from_class(unreal.CesiumSunSky, unreal.Vector(0, 0, 0),
                                                           util.rot())
        except Exception as exc:
            sun = None
            util.warn("Cesium sun could not be spawned (%s); using a plain sun and sky" % exc)
        if sun is None:
            _basic_sky(cfg)
            return georef, tileset, None
    day, month, year = cfg.DATE_DMY
    for key, value in (("time_zone", float(cfg.LOCAL_UTC_OFFSET_H)),
                       ("solar_time", float(cfg.SOLAR_TIME_H)),
                       ("day", int(day)), ("month", int(month)), ("year", int(year)),
                       ("use_daylight_saving_time", False)):
        try:
            sun.set_editor_property(key, value)
        except Exception as exc:
            util.warn("sun setting %s: %s" % (key, exc))
    try:
        sun.update_sun()
    except Exception as exc:
        util.warn("sun update: %s" % exc)
    util.finish(sun, "Sun and sky (Taiwan, %02d:00 solar)" % int(cfg.SOLAR_TIME_H), "sun", F_CESIUM)
    return georef, tileset, sun


def _basic_sky(cfg):
    """Fallback lighting if the Cesium sun is unavailable: a sun at mid-morning height, sky and sky light."""
    subs = util.actors_sub()
    existing = [a for a in subs.get_all_level_actors() if isinstance(a, unreal.DirectionalLight)]
    if existing:
        return
    sun = subs.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 50000),
                                      util.rot(-120.0, -40.0, 0.0))
    util.finish(sun, "Sun (fallback)", "sun_fallback", F_CESIUM)
    sky = subs.spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(0, 0, 0), util.rot())
    util.finish(sky, "Sky atmosphere (fallback)", "sun_fallback", F_CESIUM)
    light = subs.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 50000), util.rot())
    try:
        light.light_component.set_editor_property("real_time_capture", True)
    except Exception:
        pass
    util.finish(light, "Sky light (fallback)", "sun_fallback", F_CESIUM)


# ------------------------------------------------------------ build state machine
class Builder:
    """Streams the terrain, then places the scene. tick(dt) every editor frame until it returns True."""

    def __init__(self, cfg, mats, on_done):
        self.cfg, self.mats, self.on_done = cfg, mats, on_done
        self.world = util.world()
        self.georef, self.tileset, self.sun = setup_cesium(cfg)
        self.ignore = [self.sun] if self.sun is not None else []
        self.stage = "coarse"
        self.t = self.stage_t = self.stable = 0.0
        self.next_note = 0.0
        try:
            util.level_sub().eject_pilot_level_actor()
            util.level_sub().editor_set_viewport_realtime(True)
        except Exception:
            pass
        cx, cy = cells_center(cfg)
        util.editor_sub().set_level_viewport_camera_info(util.to_ue(cx, cy, 6500.0),
                                                         util.rot(0.0, -89.0, 0.0))

    def ground(self, x, y):
        return terrain_z(self.world, x, y, self.ignore)

    def tick(self, dt):
        self.t += dt
        self.stage_t += dt
        try:
            progress = float(self.tileset.get_load_progress())
        except Exception:
            progress = 100.0
        self.stable = self.stable + dt if progress >= 99.0 else 0.0
        if self.t >= self.next_note:
            self.next_note = self.t + 3.0
            util.flash("Streaming Taiwan terrain (%s pass): %.0f%%" % (self.stage, progress), 3.5,
                       "EagleSimBuild")
        settled = self.stage_t > 3.0 and self.stable > 1.5
        late = self.stage_t > self.cfg.TILE_WAIT_S
        try:
            if self.stage == "coarse":
                if settled or late:
                    g = self.ground(0.0, 0.0)
                    if g is not None:
                        self._recenter(g)
                        self._overview()
                        self.stage, self.stage_t, self.stable = "fine", 0.0, 0.0
                    elif late:
                        return self._fail(
                            "No terrain under the site after %d s.\n\nCheck: the Cesium panel shows you "
                            "signed in to Cesium ion; Google Photorealistic 3D Tiles (asset %d) is in "
                            "your ion assets; the laptop is online. Then run Build again."
                            % (self.cfg.TILE_WAIT_S, self.cfg.TILESET_ION_ASSET_ID))
                return False
            if settled or late:
                missing = [name for name, (x, y) in probes(self.cfg) if self.ground(x, y) is None]
                if missing and not late:
                    return False
                if missing:
                    util.warn("no terrain yet under: %s (placing anyway)" % ", ".join(missing))
                return self._place()
            return False
        except Exception as exc:
            return self._fail("Build stopped: %s" % exc)

    def _recenter(self, g):
        """Move the georeference origin onto the site's ground so heights stay small."""
        try:
            lla = self.georef.transform_unreal_position_to_longitude_latitude_height(
                util.to_ue(0.0, 0.0, g))
            self.georef.set_origin_longitude_latitude_height(
                unreal.Vector(self.cfg.SITE_LON, self.cfg.SITE_LAT, lla.z))
            util.log("site ground is %.0f m above the ellipsoid; origin moved there" % lla.z)
        except Exception as exc:
            util.warn("origin not moved (%s); heights stay relative to the ellipsoid" % exc)

    def _overview(self):
        cam, look = overview_pose(self.cfg, self.ground)
        util.editor_sub().set_level_viewport_camera_info(util.to_ue(*cam), util.look_rot(cam, look))

    def _place(self):
        try:
            sc = place_props(self.cfg, self.mats, self.world, self.ignore)
            save_level()
            self.on_done(True, sc)
        except Exception as exc:
            return self._fail("Placing the scene failed: %s" % exc)
        return True

    def _fail(self, message):
        try:
            self.on_done(False, message)
        except Exception as exc:
            util.error("build callback: %s" % exc)
        return True


# ------------------------------------------------------------ props
def _is_prop(role):
    return role not in CESIUM_ROLES and role != "other"


def place_props(cfg, mats, world_obj=None, ignore=()):
    """(Re)place forest, person, animal, eagle, cameras and labels on the terrain."""
    world_obj = world_obj or util.world()
    groups = util.demo_actors()
    util.destroy([a for role, acts in groups.items() if _is_prop(role) for a in acts])
    cache = {}

    def ground(x, y):
        key = (round(x, 2), round(y, 2))
        if key not in cache:
            cache[key] = terrain_z(world_obj, x, y, ignore)
        return cache[key]

    site_g = ground(0.0, 0.0)
    site_g = 0.0 if site_g is None else site_g
    trees = layout.make_trees(cfg)
    planted = 0
    with unreal.ScopedSlowTask(len(trees) + 5, "Eagle Sim: planting the forest") as task:
        task.make_dialog(True)
        for i, tree in enumerate(trees):
            task.enter_progress_frame(1)
            if task.should_cancel():
                raise RuntimeError("cancelled")
            g = ground(tree.x, tree.y)
            if g is None:
                continue
            _plant(i, tree, g, mats)
            planted += 1
        task.enter_progress_frame(1, "Placing the unequipped person and the muntjac")
        _place_targets(cfg, mats, ground, site_g)
        task.enter_progress_frame(1, "Building the eagle")
        _build_eagle(cfg, mats, ground, site_g)
        task.enter_progress_frame(1, "Cameras and labels")
        for key in CAMERAS:
            _camera(cfg, key, mats)
        task.enter_progress_frame(1, "Labels")
        for i, (x, y) in enumerate(cfg.SEARCH_CELLS_EN):
            g = ground(x, y)
            g = site_g if g is None else g
            util.spawn_text("CELL %s" % "ABC"[i % 3], util.to_ue(x, y, g + 30.0), 800.0,
                            (255, 200, 60), "Cell %s label" % "ABC"[i % 3], "cell_label", F_LABELS)
    try:
        util.actors_sub().select_nothing()
    except Exception:
        pass
    util.log("planted %d of %d trees; scene placed" % (planted, len(trees)))
    if planted < len(trees):
        util.warn("%d trees had no terrain under them" % (len(trees) - planted))
    return collect(cfg)


def _plant(i, tree, g, mats):
    name = ("Cover tree %03d" if tree.cover else "Tree %03d") % i
    lowest = min(b[2] for b in tree.blobs)
    trunk_h = max(3.0, lowest + 1.0)
    util.spawn_mesh("Cylinder", util.to_ue(tree.x, tree.y, g - 1.0 + trunk_h / 2.0),
                    (2 * tree.trunk_r, 2 * tree.trunk_r, trunk_h), mats["Bark"], name + " trunk",
                    "tree", F_FOREST, profile="UI")
    leaf = mats["Leaf%d" % (1 + i % 3)]
    for k, (dx, dy, dz, r) in enumerate(tree.blobs):
        util.spawn_mesh("Sphere", util.to_ue(tree.x + dx, tree.y + dy, g + dz), (2 * r, 2 * r, 2 * r),
                        leaf, "%s crown %d" % (name, k), "tree", F_FOREST, profile="UI")


def _place_targets(cfg, mats, ground, site_g):
    ox, oy = cfg.OPERATOR_EN
    og = ground(ox, oy)
    og = site_g if og is None else og
    yaw = math.radians(cfg.OPERATOR_YAW_DEG)
    ux, uy = math.cos(yaw), math.sin(yaw)
    lying = util.rot(-cfg.OPERATOR_YAW_DEG, 90.0, 0.0)     # cylinder axis along the body

    def at(s, h):
        return util.to_ue(ox + s * ux, oy + s * uy, og + h)

    for shape, loc, size, r, mat, label in (
            ("Cylinder", at(0.15, 0.20), (0.42, 0.42, 0.95), lying, "Camo", "IP-1 torso"),
            ("Cylinder", at(-0.62, 0.14), (0.30, 0.30, 0.85), lying, "Camo", "IP-1 legs"),
            ("Sphere", at(0.82, 0.15), (0.24, 0.24, 0.24), util.rot(), "Skin", "IP-1 head")):
        util.spawn_mesh(shape, loc, size, mats[mat], label, "operator", F_TARGETS, rotation=r, stencil=1)

    ax, ay = cfg.ANIMAL_EN
    ag = ground(ax, ay)
    ag = site_g if ag is None else ag
    util.spawn_mesh("Sphere", util.to_ue(ax, ay, ag + 0.55), (0.85, 0.32, 0.40), mats["Muntjac"],
                    "Muntjac body", "animal", F_TARGETS, stencil=2)
    util.spawn_mesh("Sphere", util.to_ue(ax + 0.47, ay, ag + 0.80), (0.22, 0.14, 0.16), mats["Muntjac"],
                    "Muntjac head", "animal", F_TARGETS, stencil=2)
    for n, (dx, dy) in enumerate(((0.28, 0.09), (0.28, -0.09), (-0.28, 0.09), (-0.28, -0.09))):
        util.spawn_mesh("Cylinder", util.to_ue(ax + dx, ay + dy, ag + 0.21), (0.06, 0.06, 0.42),
                        mats["Muntjac"], "Muntjac leg %d" % n, "animal", F_TARGETS, stencil=2,
                        shadows=False)



def _build_eagle(cfg, mats, ground, site_g):
    sx, sy = cfg.INGRESS_START_EN
    g = ground(sx, sy)
    g = site_g if g is None else g
    root_loc = util.to_ue(sx, sy, g + cfg.INGRESS_AGL_M)
    root = util.spawn_empty(root_loc, "EAGLE-1", "eagle", F_EAGLE)
    root.set_editor_property("tags", [util.TAG, util.ROLE_PREFIX + "eagle", signature(cfg)])
    for shape, off, size, (yaw, pitch, roll), mat, name in EAGLE_PARTS:
        loc = unreal.Vector(root_loc.x + off[0] * 100.0, root_loc.y + off[1] * 100.0,
                            root_loc.z + off[2] * 100.0)
        part = util.spawn_mesh(shape, loc, size, mats[mat], "EAGLE-1 " + name, "eagle_part", F_EAGLE,
                               rotation=util.rot(yaw, pitch, roll))
        util.attach(part, root)
    util.spawn_text(cfg.EAGLE_CALLSIGN, unreal.Vector(root_loc.x, root_loc.y, root_loc.z + 300.0), 300.0,
                    (255, 230, 120), "EAGLE-1 label", "eagle_label", F_LABELS)


def _camera(cfg, key, mats):
    specs = {
        "thermal": ("EAGLE-1 thermal camera", cfg.CAM_HFOV_DEG, cfg.CAM_PIXELS_H / float(cfg.CAM_PIXELS_V)),
        "chase": ("Chase camera", 60.0, None),
        "scene": ("Perch camera", 85.0, None),
        "overview": ("Overview camera", 55.0, None),
    }
    label, fov, aspect = specs[key]
    cam = util.actors_sub().spawn_actor_from_class(unreal.CameraActor, util.to_ue(0.0, 0.0, 300.0),
                                                   util.rot())
    try:
        cam.root_component.set_mobility(unreal.ComponentMobility.MOVABLE)
    except Exception:
        pass
    comp = cam.camera_component
    comp.set_field_of_view(fov)
    comp.set_constraint_aspect_ratio(aspect is not None)
    if aspect is not None:
        comp.set_aspect_ratio(aspect)
    if key == "thermal":
        materials.thermal_settings(comp, mats["Thermal"])
    util.finish(cam, label, "cam_" + key, F_CAMS)
    _osd(cam, key, fov, aspect or 1.9, cfg.TEXT_YAW_FIX_DEG)
    return cam


def _osd(cam, key, hfov, aspect, yaw_fix=0.0):
    """Three lines of on-screen text (status, two caption lines) fixed in front of a camera.

    A TextRender faces its own +X, so the text is turned 180 degrees to face back at the camera.
    """
    d = OSD_DISTANCE_CM
    face = util.rot(180.0 + yaw_fix, 0.0, 0.0)
    half_w = d * math.tan(math.radians(hfov) / 2.0)
    half_h = half_w / aspect
    size = 0.044 * half_h                      # text about 2.2% of the view height
    step = 1.35 * size
    rgb = (255, 255, 255) if key == "thermal" else (255, 236, 140)
    rows = [0.92 * half_h, -0.62 * half_h, -0.62 * half_h - step]
    snap = unreal.AttachmentRule.SNAP_TO_TARGET
    for i, z in enumerate(rows):
        t = util.spawn_text("", cam.get_actor_location(), size, rgb, "OSD %s %d" % (key, i),
                            "osd_%s_%d" % (key, i), F_CAMS, align="left")
        t.attach_to_actor(cam, "", snap, snap, snap, False)
        t.set_actor_relative_location(unreal.Vector(d, -0.94 * half_w, z), False, True)
        t.set_actor_relative_rotation(face, False, True)
    if key == "thermal":
        t = util.spawn_text("+", cam.get_actor_location(), size * 2.2, rgb, "OSD thermal reticle",
                            "osd_%s_r" % key, F_CAMS)
        t.attach_to_actor(cam, "", snap, snap, snap, False)
        t.set_actor_relative_location(unreal.Vector(d, 0.0, 0.0), False, True)
        t.set_actor_relative_rotation(face, False, True)


# ------------------------------------------------------------ finding the scene again
class Scene:
    """Handles to everything the mission drives, found by tag (works after an editor restart)."""

    def __init__(self, cfg, groups):
        self.cfg = cfg
        self.world = util.world()
        self.georef = None
        if cesium_ready():
            try:
                self.georef = unreal.CesiumGeoreference.get_default_georeference(self.world)
            except Exception as exc:
                util.warn("georeference: %s" % exc)

        def first(role):
            acts = groups.get(role) or []
            return acts[0] if acts else None

        self.tileset = first("tileset")
        self.sun = first("sun")
        self.ignore = [self.sun] if self.sun is not None else []
        self.cams = {k: first("cam_" + k) for k in CAMERAS}
        self.osd = {}
        for role, acts in groups.items():
            if role.startswith("osd_"):
                _, cam, line = role.split("_", 2)
                self.osd[(cam, line)] = acts[0]
        self.eagle = first("eagle")
        self.eagle_parts = groups.get("eagle_part", [])
        self.eagle_label = first("eagle_label")
        self.operator = groups.get("operator", [])
        self.animal = groups.get("animal", [])
        self.cell_labels = groups.get("cell_label", [])
        self.dynamic_labels = groups.get("label", [])
        self.trees = layout.make_trees(cfg)
        self.tree_count = len(groups.get("tree", []))
        self.sig = None
        if self.eagle is not None:
            for t in self.eagle.get_editor_property("tags"):
                if str(t).startswith("ES_SIG:"):
                    self.sig = str(t)

    def missing(self):
        need = {"terrain tileset": self.tileset, "eagle": self.eagle, "person": self.operator}
        need.update({"%s camera" % k: v for k, v in self.cams.items()})
        return [name for name, v in need.items() if not v]

    def current(self, cfg):
        """True when the props were placed with the settings in cfg."""
        return self.sig == signature(cfg) and self.tree_count > 0

    def terrain_z(self, x, y):
        return terrain_z(self.world, x, y, self.ignore)

    def scenario(self, cfg, ground_fn):
        ox, oy = cfg.OPERATOR_EN
        og = ground_fn(ox, oy)
        ax, ay = cfg.ANIMAL_EN
        ag = ground_fn(ax, ay)
        return Scenario(
            operator_center=(ox, oy, og),
            operator_points=layout.operator_points(cfg, og),
            animal_center=(ax, ay, ag),
            animal_points=layout.animal_points(cfg, ag),
            cells=list(cfg.SEARCH_CELLS_EN),
            perches=layout.perches(self.trees, ground_fn),
            start=cfg.INGRESS_START_EN,
        )

    def reset_props(self):
        """Remove labels from the last run."""
        util.destroy(self.dynamic_labels)
        self.dynamic_labels = []


def collect(cfg):
    return Scene(cfg, util.demo_actors())
