"""Person boxes from any detector (Hailo examples, Ultralytics, ...) -> floor positions.

Feed one JSON object per line (a file the detector appends to, or a pipe)::

    {"t": 12.3, "boxes": [[x1, y1, x2, y2, score], ...], "w": 640, "h": 480}

``w``/``h`` (optional) are the image size the boxes refer to (default: the camera's). Each box
becomes a ``PERSON`` detection for :mod:`eagle.fusion` (treated like HEAT):

* upright person (box taller than ``upright_aspect`` x its width): the bottom-centre pixel
  (the feet) is intersected with the floor;
* lying person: the box centre is intersected with the plane ``floor + lying_height_m``.
The covariance comes from :meth:`eagle.camgeo.Camera.ground_covariance` with a pixel error
of a fraction of the box size, plus ``sigma_body_m``. Boxes clipped by the image edge, or
whose ray does not reach the floor, are skipped.
"""
from __future__ import annotations

import json
import os

import numpy as np

from .camgeo import Camera


def parse_boxes_line(line: str):
    """-> (t, [[x1, y1, x2, y2, score], ...], (w, h) or None), or None if not a box line."""
    try:
        d = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict) or "boxes" not in d:
        return None
    boxes = [list(map(float, b[:5])) + ([1.0] if len(b) == 4 else []) for b in d["boxes"] if len(b) >= 4]
    size = (int(d["w"]), int(d["h"])) if "w" in d and "h" in d else None
    return float(d.get("t", 0.0)), boxes, size


def box_to_ground(box, camera: Camera, *, posture: str = "auto", floor_z: float = 0.0,
                  lying_height_m: float = 0.15, upright_aspect: float = 1.3, sigma_frac: float = 0.1,
                  sigma_body_m: float = 0.3):
    """-> (xy (2,), cov (2, 2), posture used) or None."""
    x1, y1, x2, y2 = box[:4]
    w, h = max(x2 - x1, 1e-6), max(y2 - y1, 1e-6)
    if posture == "auto":
        posture = "upright" if h > upright_aspect * w else "lying"
    if posture == "upright":
        u, v, z, sp = 0.5 * (x1 + x2), y2, floor_z, max(1.0, sigma_frac * w)
    else:
        u, v, z, sp = 0.5 * (x1 + x2), 0.5 * (y1 + y2), floor_z + lying_height_m, max(1.0, sigma_frac * max(w, h))
    p = camera.pixel_to_ground(u, v, z)
    if not np.all(np.isfinite(p)):
        return None
    cov = camera.ground_covariance(u, v, z, sigma_px=sp, sigma_extra_m=sigma_body_m)
    return p[:2], cov, posture


def boxes_to_detections(boxes, camera: Camera, t: float = 0.0, *, size=None, min_score: float = 0.4,
                        posture: str = "auto", floor_z: float = 0.0, edge_px: float = 2.0, **kw) -> list:
    """Boxes (in an image of ``size`` = (w, h), default the camera's) -> [LifeDetection PERSON]."""
    from .fusion import LifeDetection
    cam = camera if size is None or tuple(size) == (camera.width, camera.height) else camera.scaled(*size)
    out = []
    for b in boxes:
        if len(b) >= 5 and b[4] < min_score:
            continue
        if posture == "auto" and b[3] >= cam.height - edge_px:
            continue                                  # feet cut off by the image edge: unreliable
        r = box_to_ground(b, cam, posture=posture, floor_z=floor_z, **kw)
        if r is None:
            continue
        xy, cov, used = r
        out.append(LifeDetection("PERSON", t, xy=xy, cov=cov, source=f"vision:{used}"))
    return out


class VisionJsonReader:
    """Reads new JSON lines appended to ``path`` since the last call (non-blocking tail)."""

    def __init__(self, path: str, from_start: bool = False):
        self.path = os.path.expanduser(path)
        self.pos = 0 if from_start or not os.path.exists(self.path) else os.path.getsize(self.path)

    def read(self) -> list:
        if not os.path.exists(self.path):
            return []
        with open(self.path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(self.pos)
            data = f.read()
        if not data:
            return []
        cut = data.rfind("\n") + 1                     # keep a half-written last line for later
        self.pos += len(data[:cut].encode("utf-8"))
        return [p for p in (parse_boxes_line(ln) for ln in data[:cut].splitlines()) if p is not None]
