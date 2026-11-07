"""What the Eagle Sim menu entries do. Everything runs in the editor world: no Play button needed.

One Slate post-tick callback drives both the build (waiting for Cesium tiles) and the mission.
"""
import importlib
import math
import os
import sys
import traceback

import unreal

from ..core import cot, settings
from ..core.mission import AUTH, CONFIRM, DROP, HOLD, INGRESS, PERCH, SEARCH, TRANSIT, Mission
from . import engine as engine_mod
from . import materials, scene, util


class _State:
    def __init__(self):
        self.handle = None          # Slate tick callback handle
        self.builder = None         # scene.Builder while terrain streams
        self.mission = None
        self.engine = None
        self.sender = None
        self.cfg = None
        self.paused = False
        self.view = None
        self.auto = True            # auto director: cut cameras by mission phase
        self.game_view = True
        self.overrides = {}         # settings changed from the menu (beacon, speed)
        self.last_cut = -99.0
        self.clock = 0.0


S = _State()


def _cfg():
    return settings.load(**S.overrides)


# ------------------------------------------------------------ the tick
def _hook():
    if S.handle is None:
        S.handle = unreal.register_slate_post_tick_callback(_tick)


def _unhook():
    if S.handle is not None:
        try:
            unreal.unregister_slate_post_tick_callback(S.handle)
        except Exception:
            pass
        S.handle = None


def _tick(dt):
    try:
        dt = float(dt)
        S.clock += dt
        if S.builder is not None and S.builder.tick(dt):
            S.builder = None
        if S.mission is not None:
            if not S.paused:
                _advance(dt)
            S.engine.flush(dt)
            if S.auto and not S.paused:
                want = director_view(S.mission)
                if want != S.view and S.clock - S.last_cut > 2.5:
                    _cut(want)
        if S.builder is None and S.mission is None:
            _unhook()
    except Exception:
        util.error(traceback.format_exc())
        S.builder = None
        stop(quiet=True)
        util.flash("Eagle Sim stopped after an error. Details are in the Output Log.", 10.0)


def _slowmo(m):
    """Perch approach, perch, pellet drop and authentication play at real time."""
    if m.phase in (PERCH, DROP, AUTH):
        return True
    return m.phase == TRANSIT and getattr(m.after_transit, "__name__", "") == "land"


def _advance(dt):
    m, cfg = S.mission, S.cfg
    scale = cfg.TIME_SCALE
    if cfg.CINEMATIC_SLOWMO and _slowmo(m):
        scale = min(scale, 1.0)
    sim = min(dt, 0.25) * scale
    n = max(1, int(math.ceil(sim / 0.1 - 1e-9)))
    for _ in range(n):
        m.update(sim / n)


def director_view(m):
    """Which camera tells the story best in each phase."""
    p = m.phase
    if p in (SEARCH, CONFIRM):
        return "thermal"
    if p in (PERCH, DROP, AUTH):
        return "scene"
    if p == HOLD:
        return "overview"
    if p in (INGRESS, TRANSIT):
        nxt = getattr(m.after_transit, "__name__", "")
        return {"begin": "thermal", "land": "scene", "circle": "overview"}.get(nxt, "chase")
    return "chase"


def _cut(name):
    S.view = name
    S.last_cut = S.clock
    ls = util.level_sub()
    if name == "free":
        ls.eject_pilot_level_actor()
        return
    sc = S.engine.sc if S.engine is not None else scene.collect(_cfg())
    cam = sc.cams.get(name)
    if cam is None:
        util.warn("no %s camera in the level; run Build" % name)
        return
    ls.pilot_level_actor(cam)


# ------------------------------------------------------------ menu: run
def build_scene():
    """Open or create the level, stream Taiwan terrain, then place everything."""
    if S.builder is not None:
        util.flash("Already building; watch the Output Log.")
        return
    stop(quiet=True)
    if not scene.cesium_ready():
        util.dialog("Eagle Sim", "The Cesium for Unreal plugin is not enabled.\n\nEdit > Plugins, "
                    "search Cesium, tick Cesium for Unreal, restart the editor, then run Build again.")
        return
    cfg = _cfg()
    try:
        if not scene.open_level():
            util.dialog("Eagle Sim", "Could not open or create the level %s." % scene.LEVEL)
            return
        mats = materials.ensure()
        S.builder = scene.Builder(cfg, mats, _built)
    except Exception as exc:
        util.error(traceback.format_exc())
        util.dialog("Eagle Sim build", "Build could not start: %s" % exc)
        return
    _hook()
    util.flash("Building: streaming Taiwan terrain from Cesium ion. This takes 30-90 s.", 8.0)


def _built(ok, result):
    if ok:
        util.flash("Scene ready. Next: Eagle Sim > 2. Start mission", 12.0)
    else:
        util.error(result)
        util.dialog("Eagle Sim build", result)


def start():
    """Start (or restart) the mission on the built scene."""
    if S.builder is not None:
        util.flash("Wait for the build to finish first.")
        return
    stop(quiet=True)
    cfg = _cfg()
    sc = scene.collect(cfg)
    missing = sc.missing()
    if missing:
        util.dialog("Eagle Sim", "The scene is not built yet (missing: %s).\n\nRun Eagle Sim > "
                    "1. Build Taiwan scene first." % ", ".join(missing))
        return
    if not sc.current(cfg):
        util.flash("Settings changed since the last build: re-placing the forest and props.")
        sc = scene.place_props(cfg, materials.ensure(), sc.world, sc.ignore)
        scene.save_level()
    sc.reset_props()
    sender = cot.CotSender(cfg.COT_TARGETS, iface=cfg.COT_MCAST_IFACE)
    eng = engine_mod.UEEngine(sc, cfg, sender)
    mission = Mission(eng, sc.scenario(cfg, eng.ground), cfg)
    eng.setup_static_cams(mission.s)
    eng.flush(0.0)
    S.cfg, S.engine, S.sender, S.mission = cfg, eng, sender, mission
    S.paused, S.view, S.last_cut = False, None, -99.0
    try:
        util.level_sub().editor_set_viewport_realtime(True)
        util.level_sub().editor_set_game_view(S.game_view)
    except Exception:
        pass
    _hook()
    _cut(director_view(mission) if S.auto else "chase")
    targets = ", ".join("%s:%s" % t for t in cfg.COT_TARGETS)
    util.flash("Mission running at x%g. CoT to %s%s" % (
        cfg.TIME_SCALE, targets, "  (cooperative SWIR beacon on)" if cfg.COOPERATIVE_SIGNAL else ""), 8.0)


def pause():
    if S.mission is None:
        util.flash("No mission running.")
        return
    S.paused = not S.paused
    util.flash("Paused" if S.paused else "Resumed")


def restart():
    start()


def stop(quiet=False):
    had = S.mission is not None
    if S.sender is not None:
        S.sender.close()
    S.mission = S.engine = S.sender = None
    S.paused = False
    S.view = None
    try:
        util.level_sub().eject_pilot_level_actor()
    except Exception:
        pass
    if S.builder is None:
        _unhook()
    if had and not quiet:
        util.flash("Mission stopped.")


# ------------------------------------------------------------ menu: views
def view(name):
    """Pick a camera by hand (turns the auto director off)."""
    S.auto = False
    if S.mission is None and name != "free":
        util.flash("Showing the %s camera; it moves once a mission runs." % name)
    _cut(name)


def auto_director():
    S.auto = True
    if S.mission is not None:
        _cut(director_view(S.mission))
    util.flash("Auto director on: cameras cut by mission phase.")


def toggle_game_view():
    S.game_view = not S.game_view
    util.level_sub().editor_set_game_view(S.game_view)
    util.flash("Game view %s (G toggles it too)." % ("on" if S.game_view else "off"))


# ------------------------------------------------------------ menu: options
def toggle_beacon():
    on = not _cfg().COOPERATIVE_SIGNAL
    S.overrides["COOPERATIVE_SIGNAL"] = on
    util.flash("Cooperative SWIR beacon %s for the next start." % ("ON" if on else "OFF"))


def set_speed(x):
    S.overrides["TIME_SCALE"] = float(x)
    if S.cfg is not None:
        S.cfg.TIME_SCALE = float(x)
    util.flash("Speed x%g" % x)


def test_cot():
    """Send one marker to ATAK to check the network before the demo."""
    cfg = _cfg()
    lat, lon, hae = cfg.SITE_LAT, cfg.SITE_LON, 0.0
    try:
        sc = scene.collect(cfg)
        z = sc.terrain_z(0.0, 0.0)
        if sc.georef is not None and z is not None:
            v = sc.georef.transform_unreal_position_to_longitude_latitude_height(util.to_ue(0.0, 0.0, z))
            lat, lon, hae = v.y, v.x, v.z
    except Exception:
        pass
    payload = cot.event("EAGLE-TEST", cot.EAGLE, lat, lon, hae, callsign="EAGLE-TEST",
                        remarks="Eagle Sim network test", stale_s=300)
    sender = cot.CotSender(cfg.COT_TARGETS, iface=cfg.COT_MCAST_IFACE)
    sent = sender.send(payload)
    errors = list(sender.errors)
    sender.close()
    targets = ", ".join("%s:%s" % t for t in cfg.COT_TARGETS)
    msg = "Sent marker EAGLE-TEST to %d of %d targets (%s).\nIt should show in ATAK at %.5f, %.5f " \
          "within a few seconds." % (sent, len(cfg.COT_TARGETS), targets, lat, lon)
    if errors:
        msg += "\n\nErrors:\n" + "\n".join(errors)
    util.dialog("Eagle Sim: test CoT", msg)


def rebuild_props():
    """Re-place the forest, targets, eagle and cameras on the terrain already loaded."""
    stop(quiet=True)
    cfg = _cfg()
    sc = scene.collect(cfg)
    if sc.tileset is None:
        util.dialog("Eagle Sim", "No terrain in this level yet. Run 1. Build Taiwan scene.")
        return
    scene.place_props(cfg, materials.ensure(), sc.world, sc.ignore)
    scene.save_level()
    util.flash("Forest and props re-placed.")


def rebuild_materials():
    materials.ensure(rebuild=True)
    util.flash("Materials rebuilt.")


def open_readme():
    root = unreal.Paths.project_dir()
    for path in (os.path.join(root, "..", "README.md"), os.path.join(root, "README.md")):
        path = os.path.normpath(path)
        if os.path.exists(path):
            unreal.SystemLibrary.launch_url("file:///" + path.replace("\\", "/"))
            return
    util.flash("README.md not found next to the project.")


_RELOAD_ORDER = ("core.config", "core.settings", "core.flight", "core.detect", "core.cot",
                 "core.layout", "core.mission", "core.headless", "ue.util", "ue.materials",
                 "ue.scene", "ue.engine", "ue.app", "ue.menu")


def reload_scripts():
    """Pick up edits to the Python files without restarting the editor."""
    stop(quiet=True)
    S.builder = None
    _unhook()
    for name in _RELOAD_ORDER:
        mod = sys.modules.get("eagle_sim." + name)
        if mod is not None:
            importlib.reload(mod)
    sys.modules["eagle_sim.ue.menu"].register()
    util.flash("Eagle Sim scripts reloaded.")
