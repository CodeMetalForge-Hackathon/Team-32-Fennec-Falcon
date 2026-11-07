"""Eagle kinematics: orbits, straight legs, bank angle. Pure Python, metres and seconds.

Angles: theta is the math angle counter-clockwise from east. Orbits fly counter-clockwise.
"""
import math

G = 9.81


def orbit_point(cx, cy, radius, theta):
    return cx + radius * math.cos(theta), cy + radius * math.sin(theta)


def orbit_heading(theta):
    """Direction of travel (math angle, radians) on a counter-clockwise orbit."""
    return theta + math.pi / 2.0


def bank_deg(speed, radius):
    """Coordinated-turn bank angle for a circle of this radius."""
    if radius <= 0:
        return 0.0
    return math.degrees(math.atan(speed * speed / (G * radius)))


def angle_to(cx, cy, x, y):
    return math.atan2(y - cy, x - cx)


def distance(ax, ay, bx, by):
    return math.hypot(bx - ax, by - ay)


def step_toward(x, y, tx, ty, dist):
    """Move from (x, y) toward (tx, ty) by dist. Returns (nx, ny, arrived)."""
    d = distance(x, y, tx, ty)
    if d <= dist or d == 0:
        return tx, ty, True
    k = dist / d
    return x + (tx - x) * k, y + (ty - y) * k, False


def approach(value, target, rate, dt):
    """Move a scalar toward target at rate per second."""
    step = rate * dt
    if abs(target - value) <= step:
        return target
    return value + step if target > value else value - step


class Orbit:
    """A counter-clockwise orbit that tracks how far the bird has flown around it."""

    def __init__(self, cx, cy, radius, theta0=0.0):
        self.cx, self.cy, self.radius = cx, cy, radius
        self.theta = theta0
        self.travelled = 0.0          # radians flown on this orbit

    def advance(self, speed, dt):
        dtheta = speed * dt / self.radius
        self.theta += dtheta
        self.travelled += dtheta
        return orbit_point(self.cx, self.cy, self.radius, self.theta)

    @property
    def laps(self):
        return self.travelled / (2.0 * math.pi)

    def heading(self):
        return orbit_heading(self.theta)
