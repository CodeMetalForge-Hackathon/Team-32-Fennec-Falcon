"""Pinhole camera on the perch: pixel <-> ray <-> flat floor (ENU).

Conventions
-----------
* World frame: the kit's local ENU metres (x = east, y = north, z = up), see :mod:`eagle.locate`.
* Pixel coordinates ``(u, v)``: ``u`` = column (to the right), ``v`` = row (down). The centre
  of pixel ``img[row, col]`` is at ``(u, v) = (col, row)``; default principal point is the
  image centre ``((W - 1) / 2, (H - 1) / 2)``.
* Camera frame (OpenCV): x right, y down, z forward along the optical axis.
* Mount pose: ``position`` = ENU of the lens (z = height above the ENU origin, so with
  ``site.floor_z = 0`` it is the height above the floor), ``heading_deg`` = compass heading of
  the optical axis (degrees clockwise from TRUE north), ``tilt_down_deg`` = optical axis
  angle below horizontal, ``roll_deg`` = rotation about the optical axis, positive = the
  image's right side tilted down (camera turned clockwise as seen from behind it).

A ray through a pixel meets the plane ``z = ground_z`` at ``t = (ground_z - cz) / dz`` (only
rays pointing down, ``dz < 0``, hit it). The inverse (:meth:`Camera.project`) is used by the
synthetic thermal renderer and by tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


def _pose_matrix(heading_deg: float, tilt_down_deg: float, roll_deg: float) -> np.ndarray:
    """Camera -> world rotation; columns are the camera's right, down and forward axes in ENU."""
    a = np.radians(90.0 - heading_deg)            # compass heading -> math azimuth (CCW from east)
    t = np.radians(tilt_down_deg)
    fwd = np.array([np.cos(t) * np.cos(a), np.cos(t) * np.sin(a), -np.sin(t)])
    right = np.array([np.sin(a), -np.cos(a), 0.0])           # horizontal, 90 deg clockwise of fwd
    down = np.cross(fwd, right)                               # completes x right, y down, z fwd
    r = np.radians(roll_deg)
    right_r = np.cos(r) * right + np.sin(r) * down
    down_r = -np.sin(r) * right + np.cos(r) * down
    return np.stack([right_r, down_r, fwd], axis=1)


@dataclass
class Camera:
    """Pinhole camera with a mount pose in the ENU frame (see module docstring)."""
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    position: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0, 3.0]))
    heading_deg: float = 0.0
    tilt_down_deg: float = 35.0
    roll_deg: float = 0.0

    def __post_init__(self):
        self.position = np.asarray(self.position, dtype=float).reshape(3)
        self.R = _pose_matrix(self.heading_deg, self.tilt_down_deg, self.roll_deg)

    # ---- constructors ------------------------------------------------------------------
    @classmethod
    def from_fov(cls, hfov_deg: float, width: int, height: int, *, position=(0.0, 0.0, 3.0),
                 heading_deg: float = 0.0, tilt_down_deg: float = 35.0, roll_deg: float = 0.0,
                 vfov_deg: float | None = None) -> "Camera":
        """Square pixels from the horizontal FOV (``vfov_deg`` given: fy from it instead)."""
        fx = (width / 2.0) / np.tan(np.radians(hfov_deg) / 2.0)
        fy = fx if vfov_deg is None else (height / 2.0) / np.tan(np.radians(vfov_deg) / 2.0)
        return cls(int(width), int(height), float(fx), float(fy), (width - 1) / 2.0, (height - 1) / 2.0,
                   np.asarray(position, float), float(heading_deg), float(tilt_down_deg), float(roll_deg))

    @classmethod
    def from_config(cls, c: dict, position=None) -> "Camera":
        """From a ``life.thermal.camera``-style dict: hfov_deg, width, height, [vfov_deg],
        mount: {enu_m, heading_deg, tilt_down_deg, roll_deg}. ``position`` overrides enu_m."""
        m = c.get("mount", {})
        pos = position if position is not None else m.get("enu_m", (0.0, 0.0, 3.0))
        return cls.from_fov(float(c.get("hfov_deg", 57.0)), int(c.get("width", 160)), int(c.get("height", 120)),
                            position=pos, heading_deg=float(m.get("heading_deg", 0.0)),
                            tilt_down_deg=float(m.get("tilt_down_deg", 35.0)),
                            roll_deg=float(m.get("roll_deg", 0.0)), vfov_deg=c.get("vfov_deg"))

    @property
    def hfov_deg(self) -> float:
        return float(np.degrees(2 * np.arctan(self.width / 2.0 / self.fx)))

    @property
    def vfov_deg(self) -> float:
        return float(np.degrees(2 * np.arctan(self.height / 2.0 / self.fy)))

    def scaled(self, width: int, height: int) -> "Camera":
        """Same lens and pose at another resolution (e.g. detector boxes on a resized image)."""
        sx, sy = width / self.width, height / self.height
        return Camera(int(width), int(height), self.fx * sx, self.fy * sy, (self.cx + 0.5) * sx - 0.5,
                      (self.cy + 0.5) * sy - 0.5, self.position.copy(), self.heading_deg,
                      self.tilt_down_deg, self.roll_deg)

    # ---- pixel -> world ------------------------------------------------------------------
    def pixel_to_ray(self, u, v) -> np.ndarray:
        """Unit ENU direction(s) through pixel(s); shape (..., 3)."""
        u = np.asarray(u, dtype=float)
        v = np.asarray(v, dtype=float)
        d = np.stack([(u - self.cx) / self.fx, (v - self.cy) / self.fy, np.ones_like(u)], axis=-1)
        w = d @ self.R.T
        return w / np.linalg.norm(w, axis=-1, keepdims=True)

    def ray_to_ground(self, rays, ground_z: float = 0.0) -> np.ndarray:
        """Intersect rays from the lens with the plane z = ground_z; NaN where they miss."""
        rays = np.asarray(rays, dtype=float)
        dz = rays[..., 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (ground_z - self.position[2]) / dz
        ok = (dz < -1e-9) & (t > 0)
        pts = self.position + np.where(ok, t, np.nan)[..., None] * rays
        return pts

    def pixel_to_ground(self, u, v, ground_z: float = 0.0) -> np.ndarray:
        """ENU point(s) where pixel ray(s) meet the floor plane z = ground_z; (..., 3)."""
        return self.ray_to_ground(self.pixel_to_ray(u, v), ground_z)

    # ---- world -> pixel ------------------------------------------------------------------
    def project(self, points) -> tuple:
        """ENU point(s) -> (u, v, in_front). Points behind the lens get NaN pixels."""
        p = np.asarray(points, dtype=float)
        c = (p - self.position) @ self.R              # world -> camera: R^T (p - C)
        z = c[..., 2]
        front = z > 1e-9
        with np.errstate(divide="ignore", invalid="ignore"):
            u = np.where(front, self.fx * c[..., 0] / z + self.cx, np.nan)
            v = np.where(front, self.fy * c[..., 1] / z + self.cy, np.nan)
        return u, v, front

    def in_image(self, u, v, margin: float = 0.0):
        u, v = np.asarray(u, float), np.asarray(v, float)
        return ((u >= -0.5 + margin) & (u <= self.width - 0.5 - margin)
                & (v >= -0.5 + margin) & (v <= self.height - 0.5 - margin))

    # ---- footprints and uncertainty --------------------------------------------------------
    def pixel_ground_area(self, u, v, ground_z: float = 0.0) -> np.ndarray:
        """Floor area (m^2) covered by the pixel(s) centred at (u, v): shoelace over the 4
        projected corners. NaN where a corner misses the floor."""
        u = np.asarray(u, float)
        v = np.asarray(v, float)
        corners = [self.pixel_to_ground(u + du, v + dv, ground_z)[..., :2]
                   for du, dv in ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5))]
        x = np.stack([c[..., 0] for c in corners], axis=-1)
        y = np.stack([c[..., 1] for c in corners], axis=-1)
        return 0.5 * np.abs(np.sum(x * np.roll(y, -1, axis=-1) - np.roll(x, -1, axis=-1) * y, axis=-1))

    def ground_jacobian(self, u: float, v: float, ground_z: float = 0.0, eps: float = 0.25) -> np.ndarray:
        """2x2 d(east, north)/d(u, v) at a pixel (central differences)."""
        pu = self.pixel_to_ground([u - eps, u + eps], [v, v], ground_z)[:, :2]
        pv = self.pixel_to_ground([u, u], [v - eps, v + eps], ground_z)[:, :2]
        return np.stack([(pu[1] - pu[0]) / (2 * eps), (pv[1] - pv[0]) / (2 * eps)], axis=1)

    def ground_covariance(self, u: float, v: float, ground_z: float = 0.0, *, sigma_px: float = 0.5,
                          sigma_heading_deg: float = 1.0, sigma_tilt_deg: float = 1.0,
                          sigma_height_m: float = 0.05, sigma_extra_m: float = 0.0) -> np.ndarray:
        """2x2 ENU covariance of the floor point seen at pixel (u, v).

        Terms: pixel centroid noise (``sigma_px``, through the Jacobian); heading error
        (cross-range r * dpsi); tilt error (along-range h * dtheta / sin^2 theta, as
        :func:`eagle.locate.range_error`); mount height error (along-range r * dh / h);
        ``sigma_extra_m`` isotropic (e.g. body extent / posture height uncertainty).
        """
        p = self.pixel_to_ground(u, v, ground_z)
        if not np.all(np.isfinite(p)):
            return np.full((2, 2), np.nan)
        J = self.ground_jacobian(u, v, ground_z)
        cov = J @ (np.eye(2) * sigma_px ** 2) @ J.T
        d = p[:2] - self.position[:2]
        r = float(np.hypot(*d))
        h = float(self.position[2] - ground_z)
        if r > 1e-6:
            dep = np.arctan2(h, r)
            s_cross = r * np.radians(sigma_heading_deg)
            s_along = np.hypot(h * np.radians(sigma_tilt_deg) / np.sin(dep) ** 2, r * sigma_height_m / max(h, 1e-6))
            a = np.arctan2(d[1], d[0])
            Rm = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
            cov = cov + Rm @ np.diag([s_along ** 2, s_cross ** 2]) @ Rm.T
        return cov + np.eye(2) * sigma_extra_m ** 2
