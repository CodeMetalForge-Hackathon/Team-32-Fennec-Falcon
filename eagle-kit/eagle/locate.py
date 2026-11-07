"""Turn bearings into positions.

World frame: local ENU metres (x = east, y = north, z = up) around a surveyed origin.
World angles use the same math convention as :mod:`eagle.doa` (az counter-clockwise from
+x = east, el above horizontal). Compass bearings (clockwise from true north) are only used
at the edges: config ``heading_deg`` and log output (:func:`compass_bearing`).

* :func:`body_to_world`  array (body) az/el -> world az/el using heading/pitch/roll
* :func:`fox_fix`        single perch over a flat floor: range = h / tan(depression)
* :func:`dem_fix`        single perch over terrain: ray-march a numpy DEM + bisection
* :func:`fuse_bearings`  2+ perches: weighted least-squares crossing + 95 % ellipse
* :func:`range_error`    dr = h * dtheta / sin^2(theta)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from .doa import unit_vector, vector_to_az_el, wrap180

CHI2_2DOF_95 = 5.991464547107979  # -2 ln(0.05)


# ---- frames ---------------------------------------------------------------------------
def heading_to_yaw(heading_deg: float) -> float:
    """Compass heading (deg clockwise from north) -> math yaw (deg CCW from east)."""
    return (90.0 - heading_deg) % 360.0


def compass_bearing(az_world_deg) -> float:
    """World math azimuth -> compass bearing (deg clockwise from true north)."""
    return (90.0 - np.asarray(az_world_deg, dtype=float)) % 360.0


def rotation_matrix(yaw_deg: float = 0.0, pitch_deg: float = 0.0, roll_deg: float = 0.0) -> np.ndarray:
    """Body -> world rotation. yaw: CCW about up; pitch +: array +x tilted up;
    roll +: array +y (left) side tilted up."""
    y, p, r = np.radians([yaw_deg, -pitch_deg, roll_deg])
    Rz = np.array([[np.cos(y), -np.sin(y), 0], [np.sin(y), np.cos(y), 0], [0, 0, 1]])
    Ry = np.array([[np.cos(p), 0, np.sin(p)], [0, 1, 0], [-np.sin(p), 0, np.cos(p)]])
    Rx = np.array([[1, 0, 0], [0, np.cos(r), -np.sin(r)], [0, np.sin(r), np.cos(r)]])
    return Rz @ Ry @ Rx


def body_to_world(az_deg, el_deg, *, heading_deg: float | None = None, yaw_deg: float = 0.0,
                  pitch_deg: float = 0.0, roll_deg: float = 0.0, az_offset_deg: float = 0.0):
    """Array-frame direction -> world (ENU) az/el.

    ``heading_deg``: compass heading of the array's 0 deg mark (overrides ``yaw_deg``).
    ``az_offset_deg``: body azimuth reported by the array for a source exactly on the 0 deg
    mark (from the clap test); subtracted first.
    """
    yaw = heading_to_yaw(heading_deg) if heading_deg is not None else yaw_deg
    u = unit_vector(np.asarray(az_deg, dtype=float) - az_offset_deg, el_deg)
    return vector_to_az_el(u @ rotation_matrix(yaw, pitch_deg, roll_deg).T)


# ---- single perch, flat floor -------------------------------------------------------------
@dataclass
class FoxFix:
    xyz: np.ndarray           # estimated source position (ENU m)
    ground_range: float       # horizontal distance perch -> source (m)
    slant_range: float
    depression_deg: float     # angle below horizontal
    height: float             # perch height above the source plane (m)
    az_deg: float             # world azimuth used


def fox_fix(az_deg: float, el_deg: float, perch_xyz, *, floor_z: float = 0.0,
            source_height: float = 0.0, min_depression_deg: float = 2.0) -> FoxFix:
    """Position on a flat floor at ``floor_z`` from a world az/el seen from ``perch_xyz``.

    range = h / tan(depression) with h = perch_z - (floor_z + source_height). Refuses
    (ValueError) elevations above -``min_depression_deg``: near the horizon the range
    blows up (dr = h dtheta / sin^2 theta).
    """
    p = np.asarray(perch_xyz, dtype=float)
    dep = -float(el_deg)
    if dep < min_depression_deg:
        raise ValueError(f"elevation {el_deg:.1f} deg is above -{min_depression_deg} deg: no reliable floor fix")
    h = p[2] - (floor_z + source_height)
    if h <= 0:
        raise ValueError("perch must be above the source plane")
    r = h / np.tan(np.radians(dep))
    a = np.radians(az_deg)
    xyz = np.array([p[0] + r * np.cos(a), p[1] + r * np.sin(a), floor_z + source_height])
    return FoxFix(xyz, float(r), float(np.hypot(r, h)), dep, float(h), float(az_deg))


def range_error(h: float, depression_deg: float, dtheta_deg: float) -> float:
    """Ground-range error from an elevation error: dr = h * dtheta / sin^2(theta)."""
    th = np.radians(depression_deg)
    return float(h * np.radians(dtheta_deg) / np.sin(th) ** 2)


def fox_fix_covariance(fix: FoxFix, sigma_az_deg: float, sigma_el_deg: float) -> np.ndarray:
    """2x2 ENU covariance of a fox_fix from 1-sigma angle errors (linearised)."""
    s_r = range_error(fix.height, fix.depression_deg, sigma_el_deg)       # along the bearing
    s_c = fix.ground_range * np.radians(sigma_az_deg)                     # across the bearing
    a = np.radians(fix.az_deg)
    R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    return R @ np.diag([s_r ** 2, s_c ** 2]) @ R.T


def error_ellipse(cov, p: float = 0.95) -> tuple:
    """(semi-major, semi-minor, angle_deg CCW from east) of the ``p`` confidence ellipse."""
    k2 = -2.0 * np.log(1.0 - p)
    vals, vecs = np.linalg.eigh(np.asarray(cov, dtype=float))
    vals = np.maximum(vals, 0.0)
    major = vecs[:, 1]
    return (float(np.sqrt(k2 * vals[1])), float(np.sqrt(k2 * vals[0])),
            float(np.degrees(np.arctan2(major[1], major[0])) % 180.0))


def circular_error(cov) -> float:
    """CoT ``ce``: 1-sigma error, taken conservatively along the worst axis (m)."""
    return float(np.sqrt(max(np.linalg.eigvalsh(np.asarray(cov, dtype=float)).max(), 0.0)))


# ---- single perch, terrain ------------------------------------------------------------------
def _spacing(spacing) -> tuple:
    if np.ndim(spacing) == 0:
        return float(spacing), float(spacing)
    return float(spacing[0]), float(spacing[1])


def dem_height(dem, origin, spacing, x: float, y: float) -> float:
    """Bilinear ground height at (x, y); NaN outside the grid.

    DEM layout: ``dem[i, j]`` is the height at x = origin[0] + j*dx, y = origin[1] + i*dy
    (rows go north, columns go east), same vertical datum as the perch z.
    """
    dem = np.asarray(dem, dtype=float)
    dx, dy = _spacing(spacing)
    fx, fy = (x - origin[0]) / dx, (y - origin[1]) / dy
    nr, nc = dem.shape
    if not (0.0 <= fx <= nc - 1 and 0.0 <= fy <= nr - 1):
        return float("nan")
    j0, i0 = min(int(fx), nc - 2), min(int(fy), nr - 2)
    tx, ty = fx - j0, fy - i0
    return float((1 - tx) * (1 - ty) * dem[i0, j0] + tx * (1 - ty) * dem[i0, j0 + 1]
                 + (1 - tx) * ty * dem[i0 + 1, j0] + tx * ty * dem[i0 + 1, j0 + 1])


def dem_fix(perch_xyz, direction, dem, origin, spacing, *, source_height: float = 0.0,
            max_range: float | None = None, step: float | None = None, tol: float = 0.01):
    """First intersection of the ray perch + t*direction with the terrain (+source_height).

    ``direction``: a 3-vector in ENU, or an (az_deg, el_deg) pair in world angles.
    Marches in steps of half a cell, then bisects to ``tol`` metres. Returns an (3,) array
    or None if the ray leaves the DEM / exceeds ``max_range`` without hitting the ground.
    """
    p0 = np.asarray(perch_xyz, dtype=float)
    d = np.asarray(direction, dtype=float)
    if d.size == 2:
        d = unit_vector(d[0], d[1])
    d = d / np.linalg.norm(d)
    dx, dy = _spacing(spacing)
    nr, nc = np.asarray(dem).shape
    g0 = dem_height(dem, origin, spacing, p0[0], p0[1])
    if np.isnan(g0):
        raise ValueError("perch is outside the DEM")
    if p0[2] < g0 + source_height:
        raise ValueError("perch is below the terrain")
    step = step or 0.5 * min(dx, dy)
    max_range = max_range or float(np.hypot(nc * dx, nr * dy) * 1.5)

    def f(t):
        q = p0 + t * d
        g = dem_height(dem, origin, spacing, q[0], q[1])
        return q[2] - (g + source_height)

    t_prev, t = 0.0, step
    while t <= max_range:
        ft = f(t)
        if np.isnan(ft):
            return None
        if ft <= 0.0:
            lo, hi = t_prev, t
            while hi - lo > tol:
                mid = 0.5 * (lo + hi)
                if f(mid) > 0.0:
                    lo = mid
                else:
                    hi = mid
            return p0 + 0.5 * (lo + hi) * d
        t_prev, t = t, t + step
    return None


# ---- several perches ---------------------------------------------------------------------
@dataclass
class Bearing:
    x: float
    y: float
    az_deg: float            # world math azimuth (CCW from east)
    sigma_deg: float = 2.0   # 1-sigma bearing error


@dataclass
class BearingFix:
    xy: np.ndarray
    cov: np.ndarray
    ellipse95: tuple                       # (a, b, angle_deg)
    residuals_deg: np.ndarray
    ranges: np.ndarray
    ok: bool
    message: str = ""
    weights: np.ndarray = field(default=None, repr=False)


def fuse_bearings(bearings: Sequence, iterations: int = 5) -> BearingFix:
    """Weighted least-squares crossing of 2+ bearing lines (2-D).

    Each line i passes through p_i with direction u_i; its normal is n_i. The estimate
    minimises sum_i w_i (n_i . (x - p_i))^2 with w_i = 1 / (r_i * sigma_i)^2, i.e. the
    angular error converted to metres at the current range r_i (iterated). The covariance
    is (sum w_i n_i n_i^T)^-1 and the 95 % ellipse uses chi2(2) = 5.99.
    """
    B = [b if isinstance(b, Bearing) else Bearing(*b) for b in bearings]
    if len(B) < 2:
        raise ValueError("need at least two bearings")
    P = np.array([[b.x, b.y] for b in B], dtype=float)
    a = np.radians([b.az_deg for b in B])
    U = np.stack([np.cos(a), np.sin(a)], axis=1)
    N = np.stack([-np.sin(a), np.cos(a)], axis=1)
    sig = np.radians([max(b.sigma_deg, 1e-3) for b in B])
    w = 1.0 / sig ** 2
    x = None
    for _ in range(max(1, iterations) + 1):
        A = np.einsum("i,ij,ik->jk", w, N, N)
        if np.linalg.cond(A) > 1e10:
            return BearingFix(np.full(2, np.nan), np.full((2, 2), np.nan), (np.nan,) * 3,
                              np.full(len(B), np.nan), np.full(len(B), np.nan), False,
                              "bearings are (nearly) parallel")
        b = np.einsum("i,ij,ik,ik->j", w, N, N, P)
        x = np.linalg.solve(A, b)
        r = np.maximum(np.linalg.norm(x[None, :] - P, axis=1), 1.0)
        w = 1.0 / (sig * r) ** 2
    A = np.einsum("i,ij,ik->jk", w, N, N)
    cov = np.linalg.inv(A)
    v = x[None, :] - P
    resid = wrap180(np.degrees(np.arctan2(v[:, 1], v[:, 0]) - a))
    ahead = np.einsum("ij,ij->i", v, U) > 0
    ok = bool(np.all(ahead))
    msg = "" if ok else "crossing lies behind at least one perch"
    return BearingFix(x, cov, error_ellipse(cov), resid, np.linalg.norm(v, axis=1), ok, msg, w)
