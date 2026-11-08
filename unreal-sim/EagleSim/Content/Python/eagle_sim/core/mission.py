"""The Eagle sensor-only search mission as a state machine. Pure Python, metres, simulated seconds.

Phases: INGRESS -> SEARCH (cells from the evasion plan) -> CONFIRM (optical-sectioning orbit)
-> PERCH (observe using bird sensors) -> HOLD (relay orbit).
The person carries no technology. Human detection does not verify identity.

The mission never touches Unreal directly. It talks to an `engine` object that provides:
  ground(x, y) -> z or None         visible(cam, point, key) -> bool
  set_eagle(x, y, z, heading_rad, bank_deg, pitch_deg)
  aim(cam, look_at)                 label(key, text, xyz)
  to_lla(x, y, z) -> (lat, lon, hae)
  send(payload_bytes)               log(text)         status(text)
"""
import math
import random

from . import config as C
from . import cot, detect, flight

INGRESS, TRANSIT, SEARCH, CONFIRM, PERCH, HOLD = (
    "INGRESS", "TRANSIT", "SEARCH", "CONFIRM", "PERCH", "HOLD")


class Scenario:
    """Where things are, in metres (x east, y north, z up). Built by the scene or a test."""

    def __init__(self, operator_center, operator_points, animal_center, animal_points,
                 cells, perches, start):
        self.operator_center = operator_center      # (x, y, z) on the ground
        self.operator_points = operator_points      # head, torso, legs: ray targets
        self.animal_center = animal_center
        self.animal_points = animal_points
        self.cells = cells                          # [(x, y), ...]
        self.perches = perches                      # [(x, y, z_top), ...] cover-tree tops
        self.start = start                          # (x, y)


class Mission:
    def __init__(self, engine, scenario, cfg=C):
        self.e = engine
        self.s = scenario
        self.cfg = cfg
        self.rng = random.Random(cfg.SEED)
        self.t = 0.0
        self.phase = INGRESS
        self.done = False
        self.v = cfg.SPEED_MS
        sx, sy = scenario.start
        g0 = self._ground(sx, sy)
        self.x, self.y, self.z = sx, sy, g0 + cfg.INGRESS_AGL_M
        self.heading = 0.0
        self.bank = 0.0
        self.pitch = 0.0
        self.look_at = (scenario.cells[0][0], scenario.cells[0][1], g0)
        self.cell = 0
        self.orbit = None
        self.agl = cfg.INGRESS_AGL_M
        self.z_abs = None
        self.transit_to = None
        self.after_transit = None
        self.glimpses = detect.GlimpseTracker(cfg.CUE_WINDOW_S)
        self.views = None
        self.cue = None
        self.found_animal = False
        self.events = []                 # (t, phase, text) for tests and the log
        self.cot_sent = []               # (type, callsign) for tests
        self._cot_timer = 0.0
        self._phase_t0 = 0.0
        self._perch = None
        self._dispose_said = False
        self.ifov_ok_range = detect.max_detect_range(cfg.TARGET_M, cfg.CAM_HFOV_DEG,
                                                     cfg.CAM_PIXELS_H, cfg.MIN_PIXELS)
        self.vfov = cfg.CAM_HFOV_DEG * cfg.CAM_PIXELS_V / float(cfg.CAM_PIXELS_H)
        self._say("Eagle launched 20 km offshore; ingress to the evasion-plan cells at %d m AGL"
                  % cfg.INGRESS_AGL_M)
        self._goto_cell(0)

    # ------------------------------------------------------------ helpers
    def _ground(self, x, y):
        g = self.e.ground(x, y)
        if g is None:
            g = getattr(self, "_last_ground", 0.0)
        self._last_ground = g
        return g

    def _say(self, text):
        self.events.append((round(self.t, 2), self.phase, text))
        self.e.log("[t+%6.1fs %-7s] %s" % (self.t, self.phase, text))

    def _set_phase(self, phase):
        self.phase = phase
        self._phase_t0 = self.t

    def _cam(self):
        return (self.x, self.y, self.z - 0.6)

    def _send(self, cot_type, uid, callsign, xyz, ce, remarks):
        lat, lon, hae = self.e.to_lla(*xyz)
        self.e.send(cot.event(uid, cot_type, lat, lon, hae, ce=ce, callsign=callsign,
                              remarks=remarks))
        self.cot_sent.append((cot_type, callsign))

    def _sees(self, points, key, hfov, vfov, max_range):
        cam = self._cam()
        for p in points:
            dist = math.dist(cam, p)
            if dist > max_range:
                continue
            if not detect.in_frame(cam, self.look_at, p, hfov, vfov):
                continue
            if self.e.visible(cam, p, key):
                return True
        return False

    def _goto(self, x, y, agl, then, z_abs=None):
        """Fly a straight leg. agl = height above ground to hold, or z_abs = exact end height."""
        self.transit_to = (x, y)
        self.agl = agl
        self.z_abs = z_abs
        self.after_transit = then
        self._set_phase(TRANSIT)

    def _goto_cell(self, i):
        self.cell = i % len(self.s.cells)
        cx, cy = self.s.cells[self.cell]
        th = flight.angle_to(cx, cy, self.x, self.y)
        ex, ey = flight.orbit_point(cx, cy, self.cfg.SEARCH_RADIUS_M, th)
        self.look_at = (cx, cy, self._ground(cx, cy))

        def start_search():
            self.orbit = flight.Orbit(cx, cy, self.cfg.SEARCH_RADIUS_M, th)
            self._set_phase(SEARCH)
            self._say("Cell %s: soaring circles r=%d m at %d m AGL, thermal cam on the center"
                      % ("ABC"[self.cell % 3], self.cfg.SEARCH_RADIUS_M, self.cfg.SEARCH_AGL_M))

        agl = self.cfg.SEARCH_AGL_M if self.phase != INGRESS else self.cfg.INGRESS_AGL_M
        if self.phase == INGRESS:
            self.transit_to, self.after_transit = (ex, ey), start_search
            self.agl = agl
        else:
            self._goto(ex, ey, self.cfg.SEARCH_AGL_M, start_search)

    # ------------------------------------------------------------ main update
    def update(self, dt):
        if self.done:
            return
        self.t += dt
        px, py, pz = self.x, self.y, self.z
        if self.phase in (INGRESS, TRANSIT):
            self._fly_leg(dt)
        elif self.phase == SEARCH:
            self._search(dt)
        elif self.phase == CONFIRM:
            self._confirm(dt)
        elif self.phase == PERCH:
            self._perched(dt)
        elif self.phase == HOLD:
            self._hold(dt)
        # attitude from motion
        dx, dy, dz = self.x - px, self.y - py, self.z - pz
        if abs(dx) + abs(dy) > 1e-6:
            self.heading = math.atan2(dy, dx)
        hs = math.hypot(dx, dy)
        self.pitch = math.degrees(math.atan2(dz, hs)) if hs > 1e-6 else 0.0
        self.e.set_eagle(self.x, self.y, self.z, self.heading, self.bank, self.pitch)
        self.e.aim(self._cam(), self.look_at)
        self._cot_timer -= dt
        if self._cot_timer <= 0:
            self._cot_timer = self.cfg.COT_PERIOD_S * self.cfg.TIME_SCALE
            self._send(cot.EAGLE, "EAGLE-1", self.cfg.EAGLE_CALLSIGN, (self.x, self.y, self.z), 5.0,
                       "Hawk-eagle drone, phase %s" % self.phase)
        self.e.status(self.hud())

    def _climb_to(self, agl, dt):
        target = self._ground(self.x, self.y) + agl
        rate = 3.0 if target > self.z else 4.0
        self.z = flight.approach(self.z, target, rate, dt)

    def _fly_leg(self, dt):
        tx, ty = self.transit_to
        remaining = math.hypot(tx - self.x, ty - self.y)
        speed = self.v
        if self.z_abs is not None:
            # slow down so the descent to the perch finishes as the bird arrives
            t_need = abs(self.z_abs - self.z) / 4.0
            if t_need > 0:
                speed = min(self.v, max(1.5, remaining / t_need))
        self.x, self.y, arrived = flight.step_toward(self.x, self.y, tx, ty, speed * dt)
        if self.z_abs is not None:
            self.z = flight.approach(self.z, self.z_abs, 4.0, dt)
        else:
            agl = self.agl
            if self.phase == INGRESS and remaining < 350:
                agl = self.cfg.SEARCH_AGL_M
            self._climb_to(agl, dt)
        self.bank = 0.0
        if arrived:
            then, self.after_transit = self.after_transit, None
            self.z_abs = None
            then()

    # ------------------------------------------------------------ search
    def _search(self, dt):
        o = self.orbit
        self.x, self.y = o.advance(self.v, dt)
        self.bank = flight.bank_deg(self.v, o.radius)
        self._climb_to(self.cfg.SEARCH_AGL_M, dt)
        self.look_at = (o.cx, o.cy, self._ground(o.cx, o.cy))
        hf, vf, rng = self.cfg.CAM_HFOV_DEG, self.vfov, self.ifov_ok_range

        if not self.found_animal and self._sees(self.s.animal_points, "animal", hf, vf, rng):
            self.found_animal = True
            self._say("Warm target ~0.9 m long, low, four-legged: muntjac. Logged, not a cue")
            self.e.label("animal", "MUNTJAC (rejected)", self.s.animal_center)

        if self._sees(self.s.operator_points, "operator", hf, vf, rng) and self.glimpses.add(self.t):
            n = self.glimpses.count(self.t)
            self._say("Thermal glimpse through a canopy gap (%d/%d)" % (n, self.cfg.CUE_GLIMPSES))
            if n >= self.cfg.CUE_GLIMPSES:
                self._start_confirm(self.s.operator_center, err_m=self.rng.uniform(5.0, 12.0))
                return

        if o.laps >= self.cfg.SEARCH_LAPS:
            self._say("Cell %s clear after %.2f laps" % ("ABC"[self.cell % 3], o.laps))
            self._goto_cell(self.cell + 1)

    def _start_confirm(self, center, err_m):
        a = self.rng.uniform(0, 2 * math.pi)
        cx, cy = center[0] + err_m * math.cos(a), center[1] + err_m * math.sin(a)
        self.cue = (cx, cy, self._ground(cx, cy))
        self._send(cot.UNKNOWN_GROUND, "CUE-1", "CUE", self.cue, err_m * 2,
                   "Warm target cue from Eagle, unconfirmed")
        self.views = detect.ViewAccumulator(self.cfg.AZ_BINS)
        th = flight.angle_to(cx, cy, self.x, self.y)
        ex, ey = flight.orbit_point(cx, cy, self.cfg.CONFIRM_RADIUS_M, th)
        self.look_at = self.cue

        def begin():
            self.orbit = flight.Orbit(cx, cy, self.cfg.CONFIRM_RADIUS_M, th)
            self._set_phase(CONFIRM)
            self._say("Confirm orbit r=%d m at %d m AGL: stacking views from every side"
                      % (self.cfg.CONFIRM_RADIUS_M, self.cfg.CONFIRM_AGL_M))

        self._goto(ex, ey, self.cfg.CONFIRM_AGL_M, begin)
        self._say("Cue %.0f m from the true spot; descending to confirm" % err_m)

    # ------------------------------------------------------------ confirm (optical sectioning)
    def _confirm(self, dt):
        o = self.orbit
        self.x, self.y = o.advance(self.v, dt)
        self.bank = flight.bank_deg(self.v, o.radius)
        self._climb_to(self.cfg.CONFIRM_AGL_M, dt)
        self.look_at = self.cue
        if self._sees(self.s.operator_points, "operator", self.cfg.CAM_HFOV_DEG, self.vfov,
                      self.ifov_ok_range):
            az = detect.azimuth_deg(self.s.operator_center[:2], (self.x, self.y))
            self.views.add(az)
        if self.views.confirmed(self.cfg.CONFIRM_VIEWS, self.cfg.CONFIRM_BINS):
            oc = self.s.operator_center
            self._say("CONFIRMED: %d views over %.0f deg fuse to a person ~1.8 m long, ~34 C"
                      % (self.views.views, self.views.spread_deg))
            self._send(cot.UNKNOWN_GROUND, "IP-1", self.cfg.PERSON_CALLSIGN + " (identity unverified)",
                       oc, 3.0, "Probable isolated person, detected by bird thermal sectioning; identity unverified")
            self.e.label("operator", "IP-1 PERSON DETECTED · ID UNVERIFIED", oc)
            self._begin_perch()
        elif o.laps >= self.cfg.CONFIRM_MAX_LAPS:
            self._say("Not confirmed after %.1f laps (%d views): back to the search" %
                      (o.laps, self.views.views))
            self._goto_cell(self.cell + 1)

    # ------------------------------------------------------------ perch and observe
    def _begin_perch(self):
        oc = self.s.operator_center
        perch = min(self.s.perches, key=lambda p: math.dist(p[:2], oc[:2]))
        self._perch = perch

        def land():
            self.x, self.y = perch[0], perch[1]
            self.z = perch[2] + 0.4
            self.bank = 0.0
            self.look_at = oc
            self._set_phase(PERCH)
            self._say("Perched on a cover-tree top %.0f m from IP-1, motor off" %
                      math.dist(perch[:2], oc[:2]))

        self._goto(perch[0], perch[1], None, land, z_abs=perch[2] + 0.4)

    def _perched(self, dt):
        if self.t - self._phase_t0 > 5.0:
            oc = self.s.operator_center
            self._say("Bird-only observation complete: person detected; identity UNVERIFIED. "
                      "No carried equipment or cooperative response assumed")
            self._send(cot.UNKNOWN_GROUND, "IP-1", self.cfg.PERSON_CALLSIGN + " (identity unverified)",
                       oc, 3.0, "Bird sensor observation; identity unverified; no equipment carried")
            self.e.label("operator", "IP-1 PERSON DETECTED · ID UNVERIFIED", oc)
            self._begin_hold()

    def _begin_hold(self):
        oc = self.s.operator_center
        th = flight.angle_to(oc[0], oc[1], self.x, self.y)

        def circle():
            self.orbit = flight.Orbit(oc[0], oc[1], self.cfg.HOLD_RADIUS_M, th)
            self._set_phase(HOLD)
            self._say("Mission complete: Eagle holds a relay orbit over IP-1")

        ex, ey = flight.orbit_point(oc[0], oc[1], self.cfg.HOLD_RADIUS_M, th)
        self._goto(ex, ey, self.cfg.HOLD_AGL_M, circle)

    def _hold(self, dt):
        o = self.orbit
        self.x, self.y = o.advance(self.v, dt)
        self.bank = flight.bank_deg(self.v, o.radius)
        self._climb_to(self.cfg.HOLD_AGL_M, dt)
        self.look_at = self.s.operator_center
        if not self._dispose_said and self.t - self._phase_t0 > 20.0:
            self._dispose_said = True
            self._say("One-way bird: relays until ~10 Wh reserve, hands off to a relief bird, "
                      "flies 5+ km away, zeroizes and dives into deep water or a ravine")

    # ------------------------------------------------------------ HUD
    def hud(self):
        base = "EAGLE-1  %s  t+%ds  alt %d m" % (self.phase, int(self.t),
                                                  int(self.z - self._ground(self.x, self.y)))
        if self.phase == SEARCH and self.orbit:
            return base + "  cell %s  lap %.2f" % ("ABC"[self.cell % 3], self.orbit.laps)
        if self.phase == CONFIRM and self.views:
            return base + "  views %d  spread %d deg" % (self.views.views, self.views.spread_deg)
        return base
