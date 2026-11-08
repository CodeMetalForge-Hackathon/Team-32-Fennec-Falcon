"""Connects the engine-free mission (eagle_sim.core.mission) to the Unreal scene.

The mission calls the interface methods many times per editor frame (it sub-steps), so they
only record state or trace rays. flush() then moves the actors once per frame.
"""
import math

from . import scene as scene_mod
from . import util

CAPTION_WIDTH = 64

# label key: (minimum lift in metres, lift as a multiple of the label height)
LIFTS = {"operator": (22.0, 2.2), "animal": (14.0, 1.4), "cue": (18.0, 1.2),
         "eagle": (2.0, 1.3), "cell": (30.0, 1.0)}


def wrap(text, width=CAPTION_WIDTH, lines=2):
    out, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > width:
            out.append(cur)
            cur = word
        else:
            cur = (cur + " " + word).strip()
    if cur:
        out.append(cur)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1][: width - 1] + "…"
    return out + [""] * (lines - len(out))


def label_rgb(text):
    t = text.upper()
    if "UNVERIFIED" in t or t.startswith("CUE"):
        return (255, 190, 40)
    if "MUNTJAC" in t:
        return (210, 210, 210)
    return (255, 255, 255)


class UEEngine:
    """The mission's engine interface over the Unreal scene (see eagle_sim.core.mission)."""

    def __init__(self, sc, cfg, sender):
        self.sc, self.cfg, self.sender = sc, cfg, sender
        self.world = sc.world
        self.clock = 0.0
        self._ground = {}
        z0 = sc.terrain_z(0.0, 0.0)
        self.fallback_z = 0.0 if z0 is None else z0
        self.eagle_state = None
        self.cam = None
        self.look = None
        self.hud = ""
        self.caption = ""
        self.captions = []
        self.labels = {}                # key -> {"actor", "base", "kind", "text"}
        self._sizes = {}
        self._osd_text = {}
        self._chase = None
        self._fixed = []
        for a in sc.cell_labels:
            x, y, _ = util.from_ue(a.get_actor_location())
            self._fixed.append((a, (x, y, self.ground(x, y))))

    # ------------------------------------------------------------ mission interface
    def ground(self, x, y):
        key = (int(round(x / 2.0)), int(round(y / 2.0)))
        hit = self._ground.get(key)
        if hit is not None and self.clock - hit[1] < self.cfg.GROUND_TTL_S:
            return hit[0]
        z = self.sc.terrain_z(x, y)
        if z is None:
            return hit[0] if hit is not None else self.fallback_z
        self._ground[key] = (z, self.clock)
        return z

    def visible(self, cam, p, key):
        d = math.dist(cam, p)
        if d < 0.5:
            return True
        hit = util.trace(self.world, util.to_ue(*cam), util.to_ue(*p), util.visibility_channel(),
                         self.sc.ignore)
        if hit is None:
            return True
        return math.dist(cam, util.from_ue(hit[0])) >= d - 0.75

    def set_eagle(self, x, y, z, heading, bank, pitch):
        self.eagle_state = (x, y, z, heading, bank, pitch)

    def aim(self, cam, look_at):
        self.cam, self.look = cam, look_at

    def label(self, key, text, xyz):
        entry = self.labels.get(key)
        if not text:
            if entry is not None:
                util.set_visible(entry["actor"], False)
                entry["text"] = ""
            return
        if entry is None:
            actor = util.spawn_text(text, util.to_ue(*xyz), 300.0, label_rgb(text), "Label " + key,
                                    "label", scene_mod.F_LABELS)
            self.sc.dynamic_labels.append(actor)
            self.labels[key] = {"actor": actor, "base": tuple(xyz), "kind": key, "text": text}
            return
        entry["base"] = tuple(xyz)
        if entry["text"] != text:
            entry["text"] = text
            tr = entry["actor"].text_render
            tr.set_text(text)
            tr.set_text_render_color(util.color(*label_rgb(text)))
            util.set_visible(entry["actor"], True)

    def to_lla(self, x, y, z):
        if self.sc.georef is not None:
            try:
                v = self.sc.georef.transform_unreal_position_to_longitude_latitude_height(
                    util.to_ue(x, y, z))
                return v.y, v.x, v.z
            except Exception:
                pass
        lat = self.cfg.SITE_LAT + y / 111320.0
        lon = self.cfg.SITE_LON + x / (111320.0 * math.cos(math.radians(self.cfg.SITE_LAT)))
        return lat, lon, z

    def send(self, payload):
        if self.sender is not None:
            self.sender.send(payload)

    def log(self, text):
        util.log(text)
        self.caption = text.split("] ", 1)[-1]
        self.captions.append(text)

    def status(self, text):
        self.hud = text

    # ------------------------------------------------------------ cameras that stay put
    def setup_static_cams(self, scenario):
        """Overview camera over the cells; perch camera with a clear view of the person and the perch."""
        cams = self.sc.cams
        if cams.get("overview") is not None:
            pos, look = scene_mod.overview_pose(self.cfg, self.ground)
            util.place(cams["overview"], util.to_ue(*pos), util.look_rot(pos, look))
        if cams.get("scene") is None or not scenario.perches:
            return
        oc = scenario.operator_center
        perch = min(scenario.perches, key=lambda p: math.dist(p[:2], oc[:2]))
        above_perch = (perch[0], perch[1], perch[2] + 1.5)
        person = (oc[0], oc[1], oc[2] + 0.4)
        away = math.atan2(oc[1] - perch[1], oc[0] - perch[0])
        best = None
        for k in range(16):
            a = away + (k + 1) // 2 * (math.pi / 8.0) * (1 if k % 2 else -1)
            x, y = oc[0] + 20.0 * math.cos(a), oc[1] + 20.0 * math.sin(a)
            p = (x, y, self.ground(x, y) + 5.0)
            if self.visible(p, person, "scene") and self.visible(p, above_perch, "scene"):
                best = p
                break
        if best is None:
            x, y = oc[0] + 20.0 * math.cos(away), oc[1] + 20.0 * math.sin(away)
            best = (x, y, self.ground(x, y) + 24.0)
        mid = ((oc[0] + perch[0]) / 2.0, (oc[1] + perch[1]) / 2.0, (oc[2] + perch[2]) / 2.0)
        util.place(cams["scene"], util.to_ue(*best), util.look_rot(best, mid))

    # ------------------------------------------------------------ once per editor frame
    def flush(self, real_dt):
        self.clock += real_dt
        sc = self.sc
        if self.eagle_state is not None and sc.eagle is not None:
            x, y, z, heading, bank, pitch = self.eagle_state
            util.place(sc.eagle, util.to_ue(x, y, z),
                       util.rot(util.yaw_from_heading(heading), pitch, -bank))  # orbits turn left
            self._chase_cam(x, y, z, heading, real_dt)
        if self.cam is not None and self.look is not None and sc.cams.get("thermal") is not None:
            util.place(sc.cams["thermal"], util.to_ue(*self.cam), util.look_rot(self.cam, self.look))
        self._labels(self._viewer())
        self._osd()

    def _viewer(self):
        try:
            info = util.editor_sub().get_level_viewport_camera_info()
        except Exception:
            info = None
        if info:
            return util.from_ue(info[0])
        if self.eagle_state is not None:
            return self.eagle_state[:3]
        return (0.0, 0.0, self.fallback_z + 500.0)

    def _chase_cam(self, x, y, z, heading, real_dt):
        cam = self.sc.cams.get("chase")
        if cam is None:
            return
        fx, fy = math.cos(heading), math.sin(heading)
        tx, ty = x - fx * self.cfg.CHASE_BACK_M, y - fy * self.cfg.CHASE_BACK_M
        target = (tx, ty, max(z + self.cfg.CHASE_UP_M, self.ground(tx, ty) + 3.0))
        if self._chase is None or math.dist(self._chase, target) > 200.0:
            self._chase = target
        else:
            a = 1.0 - math.exp(-max(real_dt, 0.0) * 2.5)
            self._chase = tuple(c + (t - c) * a for c, t in zip(self._chase, target))
        look = (x + fx * 6.0, y + fy * 6.0, z)
        util.place(cam, util.to_ue(*self._chase), util.look_rot(self._chase, look))

    def _place_label(self, actor, base, kind, viewer):
        dist = max(1.0, math.dist(base, viewer))
        size = min(80.0, max(0.3, dist * self.cfg.LABEL_SCALE))
        lift_min, lift_k = LIFTS.get(kind, (15.0, 1.5))
        pos = (base[0], base[1], base[2] + lift_min + lift_k * size)
        old = self._sizes.get(actor.get_name())
        if old is None or abs(old - size) > 0.04 * old:
            self._sizes[actor.get_name()] = size
            actor.text_render.set_world_size(size * 100.0)
        util.place(actor, util.to_ue(*pos), util.look_rot(pos, viewer, self.cfg.TEXT_YAW_FIX_DEG))

    def _labels(self, viewer):
        for entry in self.labels.values():
            if entry["text"]:
                self._place_label(entry["actor"], entry["base"], entry["kind"], viewer)
        for actor, base in self._fixed:
            self._place_label(actor, base, "cell", viewer)
        if self.sc.eagle_label is not None and self.eagle_state is not None:
            self._place_label(self.sc.eagle_label, self.eagle_state[:3], "eagle", viewer)

    def _osd(self):
        line1, line2 = wrap(self.caption)
        texts = {"0": self.hud, "1": line1, "2": line2}
        for (cam, line), actor in self.sc.osd.items():
            if line not in texts:
                continue
            text = texts[line]
            if self._osd_text.get((cam, line)) != text:
                self._osd_text[(cam, line)] = text
                actor.text_render.set_text(text)
