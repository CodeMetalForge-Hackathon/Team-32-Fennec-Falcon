"""The mission without Unreal: a simple slope for the ground and canopy spheres for occlusion.

The tests use it, and tools/headless_demo.py uses it to drive ATAK on its own if Unreal
cannot run at the venue.
"""
import math
import time

from . import layout
from .mission import HOLD, Mission, Scenario


def ground(x, y):
    """A gentle forested slope about 1,000 m up."""
    return 1000.0 + 0.05 * x + 0.02 * y + 3.0 * math.sin(x / 40.0) * math.cos(y / 55.0)


class HeadlessEngine:
    """Implements the mission's engine interface with simple geometry: canopy spheres block rays."""

    def __init__(self, cfg, sender=None, echo=False):
        self.cfg = cfg
        self.sender = sender
        self.echo = echo
        self.trees = layout.make_trees(cfg)
        self.spheres = []
        for t in self.trees:
            g = ground(t.x, t.y)
            for dx, dy, dz, r in t.blobs:
                self.spheres.append((t.x + dx, t.y + dy, g + dz, r))
        self.logs, self.sent, self.labels = [], [], {}
        self.beacon_states, self.pellet_fracs = [], []
        self.eagle = None
        self.status_text = ""

    def ground(self, x, y):
        return ground(x, y)

    def visible(self, cam, p, key):
        ax, ay, az = cam
        dx, dy, dz = p[0] - ax, p[1] - ay, p[2] - az
        seg2 = dx * dx + dy * dy + dz * dz
        for sx, sy, sz, r in self.spheres:
            fx, fy, fz = ax - sx, ay - sy, az - sz
            b = 2 * (fx * dx + fy * dy + fz * dz)
            c = fx * fx + fy * fy + fz * fz - r * r
            disc = b * b - 4 * seg2 * c
            if disc < 0:
                continue
            sq = math.sqrt(disc)
            for t in ((-b - sq) / (2 * seg2), (-b + sq) / (2 * seg2)):
                if 0.0 < t < 0.999:
                    return False
        return True

    def set_eagle(self, x, y, z, heading, bank, pitch):
        self.eagle = (x, y, z, heading, bank, pitch)

    def aim(self, cam, look_at):
        pass

    def set_beacon(self, on):
        self.beacon_states.append(on)

    def pellet(self, start, end, frac):
        self.pellet_fracs.append(frac)

    def label(self, key, text, xyz):
        self.labels[key] = text

    def to_lla(self, x, y, z):
        lat = self.cfg.SITE_LAT + y / 111320.0
        lon = self.cfg.SITE_LON + x / (111320.0 * math.cos(math.radians(self.cfg.SITE_LAT)))
        return lat, lon, z

    def send(self, payload):
        self.sent.append(payload)
        if self.sender is not None:
            self.sender.send(payload)

    def log(self, text):
        self.logs.append(text)
        if self.echo:
            print(text, flush=True)

    def status(self, text):
        self.status_text = text


def scenario_for(cfg, engine):
    ox, oy = cfg.OPERATOR_EN
    og = engine.ground(ox, oy)
    ax, ay = cfg.ANIMAL_EN
    ag = engine.ground(ax, ay)
    return Scenario(
        operator_center=(ox, oy, og),
        operator_points=layout.operator_points(cfg, og),
        animal_center=(ax, ay, ag),
        animal_points=layout.animal_points(cfg, ag),
        cells=list(cfg.SEARCH_CELLS_EN),
        perches=layout.perches(engine.trees, engine.ground),
        start=cfg.INGRESS_START_EN,
    )


def run(cfg, engine, dt=0.1, max_s=3000.0, realtime=False, hold_s=0.0):
    """Run until the bird has held over the person for hold_s simulated seconds."""
    m = Mission(engine, scenario_for(cfg, engine), cfg)
    hold_t0 = None
    while m.t < max_s:
        m.update(dt)
        if realtime:
            time.sleep(dt / max(cfg.TIME_SCALE, 1e-6))
        if m.phase == HOLD:
            hold_t0 = m.t if hold_t0 is None else hold_t0
            if m.t - hold_t0 >= hold_s:
                break
    return m
