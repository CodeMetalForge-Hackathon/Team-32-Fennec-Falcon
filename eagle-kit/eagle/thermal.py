"""Thermal camera: frame sources and a warm-body detector that reports floor positions.

Sources (all return ``(t, frame, radiometric)``; ``frame`` is (H, W) float):

* :class:`SyntheticThermal` - ray-traced test scenes: people as warm ellipsoids lying or
  sitting on the floor at known ENU positions, warm decoys, small hot spots, noise.
* :class:`MLX90640Source`   - Melexis MLX90640 (32x24, deg C) on the Pi's I2C via
  ``adafruit_mlx90640`` (API as in Adafruit's ``mlx90640_simpletest.py``).
* :class:`UvcY16Source`     - PureThermal / FLIR Lepton over USB (UVC Y16, radiometric
  "TLinear" mode: raw = centi-Kelvin) through OpenCV.
* :class:`Gray8Source`      - any 8-bit camera through OpenCV (relative intensity, no deg C).

Detector (:class:`ThermalDetector`), per frame:

1. robust background: quadratic surface fitted to the frame with iterative outlier
   rejection (people and hot spots are masked out of the fit), plus a MAD noise estimate;
2. warm mask: ``frame - background >= delta_c`` AND (radiometric sources only) the pixel is
   inside the absolute window ``abs_window_c`` (default 24..40 deg C: skin/clothing, not a
   heater or a hot drink);
3. cleanup: 3x3 closing (fills holes) and removal of components under ``min_pixels``;
4. connected components (scipy.ndimage when installed, else a numpy label propagation);
5. per blob: Delta-T-weighted centroid -> ray -> plane ``floor_z + target_height_m``
   (:mod:`eagle.camgeo`), floor footprint = sum of the pixels' floor areas;
6. physical-size gate: footprint ``min_area_m2 .. max_area_m2`` and cross-range width
   ``<= max_width_m`` (a person is ~0.3-1 m^2 seen from above; a mug or a lamp is not);
7. covariance from pixel noise, mount pose errors, posture height and body extent;
8. :class:`BlobTracker`: nearest-neighbour track ids across frames.

Known bias: the centroid of an *upright* (sitting) person sits ~0.3 m above the default
target plane, which shifts the floor point away from the camera by ~0.3 m / tan(depression)
(~1 m at 10 m range from 3 m up). ``sigma_target_height_m`` puts that into the covariance.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from .camgeo import Camera


class ThermalSourceError(RuntimeError):
    pass


# ---- synthetic scene ------------------------------------------------------------------------
POSTURES = {                      # semi-axes (along body, across, vertical) m, centre height m
    "lying": ((0.85, 0.23, 0.12), 0.12),
    "sitting": ((0.28, 0.25, 0.45), 0.45),
    "curled": ((0.45, 0.30, 0.18), 0.18),
    "hotspot": ((0.08, 0.08, 0.08), 0.08),
}


@dataclass
class WarmBody:
    """A warm object for :class:`SyntheticThermal`: an ellipsoid standing on the floor."""
    xy: tuple
    posture: str = "lying"          # lying | sitting | curled | hotspot (or give `axes`)
    yaw_deg: float = 0.0            # body long axis, math degrees (CCW from east)
    temp_c: float = 31.0            # apparent surface temperature (clothed person ~28-33)
    axes: tuple | None = None       # override semi-axes (m)
    z_c: float | None = None        # override centre height above the floor (m)
    label: str = ""

    def geometry(self) -> tuple:
        axes, zc = POSTURES[self.posture] if self.posture in POSTURES else POSTURES["lying"]
        return np.asarray(self.axes or axes, float), float(self.z_c if self.z_c is not None else zc)


def person(xy, posture="lying", yaw_deg=0.0, temp_c=31.0, label=""):
    return WarmBody(tuple(xy), posture, yaw_deg, temp_c, label=label)


def warm_decoy(xy, yaw_deg=0.0, temp_c=29.0, label="decoy"):
    """Jacket stuffed with hand warmers: person-sized, warm, never breathes."""
    return WarmBody(tuple(xy), "lying", yaw_deg, temp_c, axes=(0.45, 0.25, 0.10), z_c=0.10, label=label)


def hot_spot(xy, temp_c=55.0, radius=0.08, label="hotspot"):
    """Small hot object (mug, lamp, pipe joint)."""
    return WarmBody(tuple(xy), "hotspot", 0.0, temp_c, axes=(radius, radius, radius), z_c=radius, label=label)


def _ray_ellipsoids(origin, dirs, bodies, floor_z):
    """Nearest hit of rays (N, 3) from ``origin``: (t (N,), index (N,) or -1, cosine (N,))."""
    n = dirs.shape[0]
    t_best = np.full(n, np.inf)
    idx = np.full(n, -1)
    cos_best = np.ones(n)
    for k, b in enumerate(bodies):
        axes, zc = b.geometry()
        a = np.radians(b.yaw_deg)
        R = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
        c = np.array([b.xy[0], b.xy[1], floor_z + zc])
        o = (R.T @ (origin - c)) / axes
        d = (dirs @ R) / axes
        A = np.sum(d * d, axis=1)
        B = 2.0 * d @ o
        C = o @ o - 1.0
        disc = B * B - 4 * A * C
        hit = disc >= 0
        t = np.full(n, np.inf)
        t[hit] = (-B[hit] - np.sqrt(disc[hit])) / (2 * A[hit])
        t[t <= 0] = np.inf
        better = t < t_best
        if np.any(better):
            p = o + t[better, None] * d[better]              # hit point in the unit-sphere frame
            nrm = (p / axes) @ R.T
            nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)
            cos_best[better] = np.abs(np.sum(nrm * dirs[better], axis=1))
            t_best[better] = t[better]
            idx[better] = k
    return t_best, idx, cos_best


class SyntheticThermal:
    """Renders deg C frames of a floor scene seen by ``camera`` (supersampled, with noise).

    Floor = ``background_c`` (+ ``gradient_c_per_m`` x distance from the camera); rays above
    the horizon see ``wall_c``. Bodies show ``temp_c`` falling by ``edge_cooling_c`` at grazing
    angles (clothing edges look cooler). Pixel = mean of ``ss x ss`` sub-rays (mixed pixels
    at long range, like a real low-resolution sensor), plus Gaussian ``noise_c``.
    """

    def __init__(self, camera: Camera, bodies=(), *, background_c: float = 20.0, wall_c: float = 18.0,
                 gradient_c_per_m: float = 0.0, noise_c: float = 0.15, edge_cooling_c: float = 1.5,
                 ss: int = 3, floor_z: float = 0.0, seed: int = 0, fps: float = 4.0):
        self.cam, self.bodies = camera, list(bodies)
        self.bg, self.wall, self.grad = background_c, wall_c, gradient_c_per_m
        self.noise, self.edge, self.ss, self.floor_z = noise_c, edge_cooling_c, int(ss), floor_z
        self.rng = np.random.default_rng(seed)
        self.dt, self.t = 1.0 / fps, 0.0
        o = (np.arange(self.ss) + 0.5) / self.ss - 0.5
        vv, uu = np.mgrid[0:camera.height, 0:camera.width]
        self._u = (uu[..., None, None] + o[None, None, None, :]).repeat(self.ss, axis=2)
        self._v = (vv[..., None, None] + o[None, None, :, None]).repeat(self.ss, axis=3)
        self._rays = camera.pixel_to_ray(self._u, self._v).reshape(-1, 3)

    def render(self) -> np.ndarray:
        cam = self.cam
        rays = self._rays
        temp = np.full(rays.shape[0], self.wall, dtype=float)
        down = rays[:, 2] < -1e-9
        t_floor = np.full(rays.shape[0], np.inf)
        t_floor[down] = (self.floor_z - cam.position[2]) / rays[down, 2]
        p = cam.position + np.where(np.isfinite(t_floor), t_floor, 0.0)[:, None] * rays
        dist = np.hypot(p[:, 0] - cam.position[0], p[:, 1] - cam.position[1])
        temp[down] = self.bg + self.grad * dist[down]
        if self.bodies:
            t_b, idx, cosang = _ray_ellipsoids(cam.position, rays, self.bodies, self.floor_z)
            hit = (idx >= 0) & (t_b < t_floor)
            tb = np.array([b.temp_c for b in self.bodies])
            temp[hit] = tb[idx[hit]] - self.edge * (1.0 - cosang[hit])
        frame = temp.reshape(cam.height, cam.width, self.ss * self.ss).mean(axis=2)
        return frame + self.rng.normal(0.0, self.noise, frame.shape)

    def read(self) -> tuple:
        self.t += self.dt
        return self.t, self.render(), True

    def close(self) -> None:
        pass


# ---- hardware sources -----------------------------------------------------------------------
class MLX90640Source:
    """Melexis MLX90640 on I2C (Raspberry Pi: SDA = GPIO2 / pin 3, SCL = GPIO3 / pin 5, 3V3, GND).

    Needs ``pip install adafruit-circuitpython-mlx90640`` (pulls in Adafruit Blinka) and I2C
    enabled (``sudo raspi-config`` -> Interface Options -> I2C). 32x24 frame in deg C. Use
    ``flip_h``/``flip_v`` to match how the sensor is mounted (check with a hand in one corner).
    """

    RATES = {0.5: "REFRESH_0_5_HZ", 1: "REFRESH_1_HZ", 2: "REFRESH_2_HZ", 4: "REFRESH_4_HZ",
             8: "REFRESH_8_HZ", 16: "REFRESH_16_HZ", 32: "REFRESH_32_HZ", 64: "REFRESH_64_HZ"}

    def __init__(self, refresh_hz: float = 2, i2c_hz: int = 800000, flip_h: bool = False, flip_v: bool = False):
        try:
            import adafruit_mlx90640  # type: ignore
            import board  # type: ignore
            import busio  # type: ignore
        except ImportError as e:
            raise ThermalSourceError("MLX90640 needs: pip install adafruit-circuitpython-mlx90640 "
                                     "(and I2C enabled with raspi-config)") from e
        i2c = busio.I2C(board.SCL, board.SDA, frequency=int(i2c_hz))
        self.mlx = adafruit_mlx90640.MLX90640(i2c)
        name = self.RATES.get(refresh_hz, "REFRESH_2_HZ")
        self.mlx.refresh_rate = getattr(adafruit_mlx90640.RefreshRate, name,
                                        adafruit_mlx90640.RefreshRate.REFRESH_2_HZ)
        self.buf = [0.0] * 768
        self.flip_h, self.flip_v = flip_h, flip_v

    def read(self) -> tuple:
        for _ in range(5):
            try:
                self.mlx.getFrame(self.buf)
                break
            except ValueError:            # Adafruit's example: these happen, retry
                continue
        else:
            raise ThermalSourceError("MLX90640: 5 failed reads in a row")
        f = np.asarray(self.buf, dtype=float).reshape(24, 32)
        f = f[:, ::-1] if self.flip_h else f
        f = f[::-1, :] if self.flip_v else f
        return time.time(), f, True

    def close(self) -> None:
        pass


def _open_cv_capture(device):
    try:
        import cv2  # type: ignore
    except ImportError as e:
        raise ThermalSourceError("pip install opencv-python (or apt install python3-opencv)") from e
    dev = int(device) if str(device).isdigit() else device
    cap = cv2.VideoCapture(dev, cv2.CAP_V4L2) if hasattr(cv2, "CAP_V4L2") and isinstance(dev, int) else None
    if cap is None or not cap.isOpened():
        cap = cv2.VideoCapture(dev)
    if not cap.isOpened():
        raise ThermalSourceError(f"cannot open camera {device!r} (ls /dev/video*; v4l2-ctl --list-devices)")
    return cv2, cap


class UvcY16Source:
    """PureThermal board + FLIR Lepton over USB (UVC). Requests the Y16 pixel format with RGB
    conversion off; in radiometric TLinear mode every pixel is centi-Kelvin
    (deg C = raw / 100 - 273.15). If the frames look like 8-bit AGC images the camera is not
    in Y16/TLinear mode: use :class:`Gray8Source` or configure the board (GetThermal)."""

    def __init__(self, device=0, width: int = 160, height: int = 120):
        self.cv2, self.cap = _open_cv_capture(device)
        cv2 = self.cv2
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc("Y", "1", "6", " "))
        self.cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.w, self.h = int(width), int(height)

    def read(self) -> tuple:
        ok, img = self.cap.read()
        if not ok or img is None:
            raise ThermalSourceError("UVC read failed")
        a = np.asarray(img)
        if a.dtype == np.uint8 and a.size == self.w * self.h * 2:      # raw buffer from some builds
            a = a.reshape(-1).view("<u2").reshape(self.h, self.w)
        if a.dtype != np.uint16:
            raise ThermalSourceError(f"expected 16-bit Y16 frames, got {a.dtype} {a.shape}: not radiometric")
        return time.time(), a.astype(float) / 100.0 - 273.15, True

    def close(self) -> None:
        self.cap.release()


class Gray8Source:
    """Any 8-bit camera via OpenCV (an AGC thermal stream, or NoIR at night). Relative units:
    the detector then uses ``delta_c`` in grey levels and skips the absolute window."""

    def __init__(self, device=0):
        self.cv2, self.cap = _open_cv_capture(device)

    def read(self) -> tuple:
        ok, img = self.cap.read()
        if not ok or img is None:
            raise ThermalSourceError("camera read failed")
        a = np.asarray(img)
        if a.ndim == 3:
            a = a.mean(axis=2)
        return time.time(), a.astype(float), False

    def close(self) -> None:
        self.cap.release()


def make_thermal_source(tcfg: dict, camera: Camera | None = None, bodies=None, seed: int = 0):
    """Source named in ``life.thermal.source``: synthetic | mlx90640 | uvc_y16 | gray8."""
    kind = tcfg.get("source", "synthetic")
    if kind == "synthetic":
        s = tcfg.get("synthetic", {})
        if bodies is None:
            bodies = [person(b["xy"], b.get("posture", "lying"), b.get("yaw_deg", 0.0), b.get("temp_c", 31.0),
                             b.get("label", "")) for b in s.get("bodies", [])]
        return SyntheticThermal(camera, bodies, background_c=float(s.get("background_c", 20.0)),
                                noise_c=float(s.get("noise_c", 0.15)), seed=seed)
    if kind == "mlx90640":
        m = tcfg.get("mlx90640", {})
        return MLX90640Source(m.get("refresh_hz", 2), m.get("i2c_hz", 800000), m.get("flip_h", False),
                              m.get("flip_v", False))
    if kind == "uvc_y16":
        u = tcfg.get("uvc", {})
        return UvcY16Source(u.get("device", 0), u.get("width", 160), u.get("height", 120))
    if kind == "gray8":
        return Gray8Source(tcfg.get("uvc", {}).get("device", 0))
    raise ThermalSourceError(f"unknown thermal source {kind!r}")


# ---- image helpers (numpy only; scipy used when present) -----------------------------------------
def robust_background(frame, iters: int = 4, k: float = 3.0) -> tuple:
    """(background (H, W), noise sigma): quadratic surface fit with outlier rejection."""
    f = np.asarray(frame, dtype=float)
    H, W = f.shape
    yy, xx = np.mgrid[0:H, 0:W]
    x = (xx.ravel() / max(W - 1, 1)) * 2 - 1
    y = (yy.ravel() / max(H - 1, 1)) * 2 - 1
    A = np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=1)
    z = f.ravel()
    keep = z <= np.percentile(z, 70)                 # start from the cooler majority
    sigma = 1.0
    for _ in range(iters):
        coef, *_ = np.linalg.lstsq(A[keep], z[keep], rcond=None)
        r = z - A @ coef
        med = np.median(r[keep])
        sigma = max(1.4826 * np.median(np.abs(r[keep] - med)), 1e-3)
        keep = np.abs(r - med) < k * sigma
    return (A @ coef).reshape(H, W), float(sigma)


def _shift(mask, dy, dx):
    out = np.zeros_like(mask)
    H, W = mask.shape
    out[max(dy, 0):H + min(dy, 0), max(dx, 0):W + min(dx, 0)] = \
        mask[max(-dy, 0):H + min(-dy, 0), max(-dx, 0):W + min(-dx, 0)]
    return out


def dilate(mask):
    out = mask.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            out |= _shift(mask, dy, dx)
    return out


def erode(mask):
    out = mask.copy()
    pad = np.pad(mask, 1, mode="edge")               # do not eat blobs touching the border
    H, W = mask.shape
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            out &= pad[1 + dy:1 + dy + H, 1 + dx:1 + dx + W]
    return out


def label_components(mask) -> tuple:
    """8-connected labels (0 = background) -> (labels, n)."""
    mask = np.asarray(mask, dtype=bool)
    try:
        from scipy import ndimage  # type: ignore
        lab, n = ndimage.label(mask, structure=np.ones((3, 3), int))
        return lab, int(n)
    except ImportError:
        pass
    H, W = mask.shape
    big = H * W + 1
    lab = np.where(mask, np.arange(1, H * W + 1).reshape(H, W), big)
    while True:                                      # propagate the minimum label to neighbours
        pad = np.pad(lab, 1, constant_values=big)
        m = lab.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                m = np.minimum(m, pad[1 + dy:1 + dy + H, 1 + dx:1 + dx + W])
        m = np.where(mask, m, big)
        if np.array_equal(m, lab):
            break
        lab = m
    uniq = np.unique(lab[mask])
    out = np.zeros((H, W), dtype=int)
    if uniq.size:
        out[mask] = np.searchsorted(uniq, lab[mask]) + 1
    return out, int(uniq.size)


# ---- detector -----------------------------------------------------------------------------------
@dataclass
class HeatBlob:
    xy: np.ndarray                    # ENU floor position of the warm body (m)
    cov: np.ndarray                   # 2x2 ENU covariance (m^2)
    u: float                          # centroid pixel
    v: float
    n_px: int
    area_m2: float                    # floor footprint at the target plane
    width_m: float                    # cross-range extent
    peak: float                       # deg C (or grey level for relative sources)
    mean: float
    delta: float                      # mean - background
    accepted: bool = True
    reason: str = ""
    track_id: int | None = None
    t: float = 0.0

    def __str__(self) -> str:
        tid = f"#{self.track_id} " if self.track_id is not None else ""
        return (f"{tid}E {self.xy[0]:6.2f} N {self.xy[1]:6.2f}  {self.n_px:4d} px  {self.area_m2:4.2f} m^2  "
                f"peak {self.peak:5.1f}  dT {self.delta:4.1f}" + (f"  [{self.reason}]" if self.reason else ""))


class ThermalDetector:
    """Warm bodies in a thermal frame -> floor positions (see module docstring)."""

    def __init__(self, camera: Camera, *, delta_c: float = 3.0, abs_window_c=(24.0, 40.0),
                 min_pixels: int = 2, min_area_m2: float = 0.12, max_area_m2: float = 3.0,
                 max_width_m: float = 2.2, floor_z: float = 0.0, target_height_m: float = 0.15,
                 sigma_px: float = 0.5, sigma_heading_deg: float = 1.0, sigma_tilt_deg: float = 1.0,
                 sigma_mount_height_m: float = 0.05, sigma_target_height_m: float = 0.1,
                 sigma_body_m: float = 0.25, close: bool = True):
        self.cam = camera
        self.delta, self.window = float(delta_c), (tuple(abs_window_c) if abs_window_c else None)
        self.min_pixels, self.min_area, self.max_area = int(min_pixels), float(min_area_m2), float(max_area_m2)
        self.max_width = float(max_width_m)
        self.floor_z, self.target_h = float(floor_z), float(target_height_m)
        self.sig = dict(sigma_px=sigma_px, sigma_heading_deg=sigma_heading_deg, sigma_tilt_deg=sigma_tilt_deg,
                        sigma_height_m=float(np.hypot(sigma_mount_height_m, sigma_target_height_m)),
                        sigma_extra_m=sigma_body_m)
        self.close = close
        self.rejected: list = []
        self.last_background = None
        self.last_noise = None
        z = self.floor_z + self.target_h
        vv, uu = np.mgrid[0:camera.height, 0:camera.width]
        self._ground = camera.pixel_to_ground(uu, vv, z)[..., :2]          # per-pixel floor point
        self._area = camera.pixel_ground_area(uu, vv, z)

    @classmethod
    def from_config(cls, camera: Camera, d: dict, floor_z: float = 0.0) -> "ThermalDetector":
        import inspect
        keys = set(inspect.signature(cls.__init__).parameters) - {"self", "camera", "floor_z"}
        kw = {k: (tuple(v) if isinstance(v, list) else v) for k, v in (d or {}).items() if k in keys}
        return cls(camera, floor_z=floor_z, **kw)

    def detect(self, frame, radiometric: bool = True, t: float = 0.0) -> list:
        """-> accepted :class:`HeatBlob` s (rejected ones are kept in ``self.rejected``)."""
        f = np.asarray(frame, dtype=float)
        if f.shape != (self.cam.height, self.cam.width):
            raise ValueError(f"frame {f.shape} does not match the camera {self.cam.height}x{self.cam.width}")
        bg, sigma = robust_background(f)
        self.last_background, self.last_noise = bg, sigma
        d = f - bg
        mask = d >= max(self.delta, 4.0 * sigma)
        if radiometric and self.window:
            mask &= (f >= self.window[0]) & (f <= self.window[1])
        if self.close:
            mask = erode(dilate(mask))
        lab, n = label_components(mask)
        out, self.rejected = [], []
        for k in range(1, n + 1):
            rr, cc = np.nonzero(lab == k)
            if rr.size < self.min_pixels:
                continue
            w = np.maximum(d[rr, cc], 1e-3)
            u, v = float(np.sum(w * cc) / w.sum()), float(np.sum(w * rr) / w.sum())
            g = self._ground[rr, cc]
            ok = np.all(np.isfinite(g), axis=1)
            area = float(np.nansum(self._area[rr, cc]))
            p = self.cam.pixel_to_ground(u, v, self.floor_z + self.target_h)
            blob = HeatBlob(np.full(2, np.nan), np.full((2, 2), np.nan), u, v, int(rr.size), area, 0.0,
                            float(f[rr, cc].max()), float(f[rr, cc].mean()), float(d[rr, cc].mean()), t=t)
            if not np.all(np.isfinite(p)) or not ok.any():
                blob.accepted, blob.reason = False, "above the horizon"
                self.rejected.append(blob)
                continue
            blob.xy = p[:2]
            rng_dir = blob.xy - self.cam.position[:2]
            rng_dir = rng_dir / max(np.linalg.norm(rng_dir), 1e-9)
            cross = g[ok] @ np.array([-rng_dir[1], rng_dir[0]])
            blob.width_m = float(cross.max() - cross.min()) if cross.size else 0.0
            blob.cov = self.cam.ground_covariance(u, v, self.floor_z + self.target_h, **self.sig)
            if area < self.min_area:
                blob.accepted, blob.reason = False, f"too small ({area:.2f} m^2)"
            elif area > self.max_area:
                blob.accepted, blob.reason = False, f"too large ({area:.2f} m^2)"
            elif blob.width_m > self.max_width:
                blob.accepted, blob.reason = False, f"too wide ({blob.width_m:.1f} m)"
            (out if blob.accepted else self.rejected).append(blob)
        return out


class BlobTracker:
    """Greedy nearest-neighbour track ids for heat blobs across frames."""

    def __init__(self, max_dist_m: float = 1.0, max_missed: int = 8):
        self.max_dist, self.max_missed = float(max_dist_m), int(max_missed)
        self.tracks: dict = {}             # id -> [xy, missed]
        self.next_id = 1

    def update(self, blobs: list) -> list:
        pairs = sorted(((float(np.linalg.norm(b.xy - tr[0])), i, tid)
                        for i, b in enumerate(blobs) for tid, tr in self.tracks.items()), key=lambda p: p[0])
        used_b, used_t = set(), set()
        for dist, i, tid in pairs:
            if dist > self.max_dist or i in used_b or tid in used_t:
                continue
            blobs[i].track_id = tid
            self.tracks[tid] = [blobs[i].xy.copy(), 0]
            used_b.add(i)
            used_t.add(tid)
        for i, b in enumerate(blobs):
            if i not in used_b:
                b.track_id = self.next_id
                self.tracks[self.next_id] = [b.xy.copy(), 0]
                used_t.add(self.next_id)
                self.next_id += 1
        for tid in list(self.tracks):
            if tid not in used_t:
                self.tracks[tid][1] += 1
                if self.tracks[tid][1] > self.max_missed:
                    del self.tracks[tid]
        return blobs
