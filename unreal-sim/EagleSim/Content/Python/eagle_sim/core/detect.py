"""Thermal detection geometry. Pure Python, metres.

A glimpse needs three things at once: the target is inside the camera frame, it covers at
least MIN_PIXELS across its 0.45 m torso, and a ray from the camera reaches it without
hitting the canopy (the engine answers that last part with a line trace).
Confirming uses airborne optical sectioning: enough visible views spread around the target.
"""
import math


def ifov_rad(hfov_deg, pixels):
    return math.radians(hfov_deg) / float(pixels)


def pixels_on_target(dist_m, target_m, hfov_deg, pixels):
    return target_m / (max(dist_m, 1e-6) * ifov_rad(hfov_deg, pixels))


def max_detect_range(target_m, hfov_deg, pixels, min_px):
    return target_m / (min_px * ifov_rad(hfov_deg, pixels))


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n) if n > 0 else (0.0, 0.0, 0.0)


def in_frame(cam, look_at, point, hfov_deg, vfov_deg):
    """True if point is inside a camera at cam aimed at look_at (z up, no roll)."""
    fwd = _norm(_sub(look_at, cam))
    world_up = (0.0, 0.0, 1.0)
    right = _cross(fwd, world_up)
    if _dot(right, right) < 1e-9:              # looking straight down
        right = (1.0, 0.0, 0.0)
    right = _norm(right)
    up = _norm(_cross(right, fwd))
    d = _sub(point, cam)
    z = _dot(d, fwd)
    if z <= 0:
        return False
    x = _dot(d, right)
    y = _dot(d, up)
    return (abs(math.degrees(math.atan2(x, z))) <= hfov_deg / 2.0 and
            abs(math.degrees(math.atan2(y, z))) <= vfov_deg / 2.0)


def azimuth_deg(from_xy, to_xy):
    return math.degrees(math.atan2(to_xy[1] - from_xy[1], to_xy[0] - from_xy[0])) % 360.0


class GlimpseTracker:
    """Counts glimpses inside a sliding time window."""

    def __init__(self, window_s, min_gap_s=1.5):
        self.window_s = window_s
        self.min_gap_s = min_gap_s       # one glimpse per look, not one per frame
        self.times = []

    def add(self, t):
        """Record a glimpse. Returns False if it is too close to the last one to count."""
        if self.times and t - self.times[-1] < self.min_gap_s:
            return False
        self.times.append(t)
        self.prune(t)
        return True

    def prune(self, t):
        self.times = [x for x in self.times if t - x <= self.window_s]

    def count(self, t):
        self.prune(t)
        return len(self.times)


class ViewAccumulator:
    """Visible views binned by azimuth around the target (airborne optical sectioning)."""

    def __init__(self, bins):
        self.bins = bins
        self.views = 0
        self.hit_bins = set()
        self.last_bin = None

    def add(self, az_deg):
        b = int(az_deg // (360.0 / self.bins)) % self.bins
        # count one view per bin visit so a bird lingering at one angle does not inflate it
        if b != self.last_bin or len(self.hit_bins) == 0:
            self.views += 1
        self.hit_bins.add(b)
        self.last_bin = b

    @property
    def spread_deg(self):
        return len(self.hit_bins) * (360.0 / self.bins)

    def confirmed(self, need_views, need_bins):
        return self.views >= need_views and len(self.hit_bins) >= need_bins
