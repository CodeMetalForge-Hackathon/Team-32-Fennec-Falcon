"""Live face scan for enrollment (apps/enroll_target.py --live / --verify).

The person stands in front of the Pi camera and turns their head slowly left, right, up and down
for ~10 s. Every frame goes through SCRFD (InsightFace buffalo_l); a frame is kept only if

* exactly one face is in view (det score >= ``det_score``),
* the face is at least ``min_face_px`` wide (80 px: well above the runtime matcher's 40 px),
* all five landmarks lie inside the frame (``edge_margin_px``),
* the face crop is sharp: variance of the Laplacian (on the crop scaled to 112 px wide) >=
  ``min_sharpness``.

ArcFace runs only on kept frames. Up to ``max_embeddings`` (20) are chosen spread across head
angles: yaw and pitch are estimated from the landmark geometry (:func:`head_angles`) and picked by
farthest-point sampling, starting from the best frontal frame.

Night / infrared: a low-colour frame (NoIR camera under IR light) is converted to grayscale
replicated to 3 channels before SCRFD and ArcFace (:func:`prepare_for_face`). FaceEmbedder applies
the same conversion at runtime, so enrollment and matching see the same kind of image.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from . import protocol as P

LIVE_DEFAULTS = {"seconds": 10.0, "min_face_px": 80, "min_sharpness": 60.0, "max_embeddings": 20,
                 "min_kept": 8, "det_score": 0.5, "edge_margin_px": 2}
NOSE_DEPTH = 0.6          # nose tip depth / inter-ocular distance (adult face average, approximate)
PITCH_NEUTRAL = 0.55      # nose height between eye line (0) and mouth line (1) when level
PITCH_DEPTH = 0.6


# ---- image helpers ----------------------------------------------------------------------------------
def chroma_spread(rgb, min_sum: float = 45.0) -> float:
    """How much the colour varies across the image, 0..~100: 100 x the spread of the normalized
    chromaticity (R/(R+G+B), B/(R+G+B)) over pixels that are not near-black. A NoIR frame under IR
    light is monochrome with at most a uniform tint (pink/purple), so its chromaticity is nearly
    constant and this stays near 0 however bright the frame is; daylight colour scenes give ~3-15."""
    a = np.asarray(rgb, np.float32)
    if a.ndim != 3 or a.shape[2] < 3:
        return 0.0
    step = max(1, int(np.sqrt(a.shape[0] * a.shape[1] / 40000)))      # ~40k samples is plenty
    a = a[::step, ::step, :3].reshape(-1, 3)
    s = a.sum(axis=1)
    a, s = a[s >= min_sum], s[s >= min_sum]                           # dark pixels: chromaticity is noise
    if len(s) < 16:
        return 0.0
    r, b = a[:, 0] / s, a[:, 2] / s
    return float(100.0 * np.hypot(r.std(), b.std()))


def to_gray3(rgb) -> np.ndarray:
    a = np.asarray(rgb, np.float32)
    g = (0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]).round().clip(0, 255).astype(np.uint8)
    return np.repeat(g[..., None], 3, axis=2)


def prepare_for_face(rgb, gray_threshold: float = 2.0) -> tuple:
    """-> (image for SCRFD/ArcFace, converted?). Low-colour frames become grayscale x 3 channels."""
    if gray_threshold and gray_threshold > 0 and chroma_spread(rgb) < gray_threshold:
        return to_gray3(rgb), True
    return np.ascontiguousarray(rgb), False


def laplacian_variance(gray) -> float:
    """Variance of the 4-neighbour Laplacian (numpy only)."""
    g = np.asarray(gray, np.float32)
    if g.ndim == 3:
        g = g.mean(axis=2)
    if g.shape[0] < 3 or g.shape[1] < 3:
        return 0.0
    lap = g[1:-1, :-2] + g[1:-1, 2:] + g[:-2, 1:-1] + g[2:, 1:-1] - 4 * g[1:-1, 1:-1]
    return float(lap.var())


def face_sharpness(rgb, bbox, width: int = 112) -> float:
    """Laplacian variance of the face crop resized to ``width`` px (so big faces are not favoured)."""
    h, w = rgb.shape[:2]
    x1, y1, x2, y2 = (int(round(v)) for v in bbox[:4])
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
    if x2 - x1 < 3 or y2 - y1 < 3:
        return 0.0
    crop = np.asarray(rgb[y1:y2, x1:x2], np.float32).mean(axis=2)
    s = width / crop.shape[1]
    ys = np.clip((np.arange(int(round(crop.shape[0] * s))) / s).astype(int), 0, crop.shape[0] - 1)
    xs = np.clip((np.arange(width) / s).astype(int), 0, crop.shape[1] - 1)
    return laplacian_variance(crop[np.ix_(ys, xs)])                   # nearest-neighbour resize


def deg(v: float) -> str:
    """Signed whole degrees without a "-0"."""
    return f"{int(round(v)):+d}"


def head_angles(kps) -> tuple:
    """Approximate (yaw, pitch) in degrees from SCRFD's 5 landmarks (image-left eye, image-right eye,
    nose, image-left mouth corner, image-right mouth corner).

    yaw > 0: the nose has moved toward the image's right (the person turned to THEIR left on a
    non-mirrored camera). pitch > 0: looking up. Model: eyes at +-iod/2, nose tip NOSE_DEPTH*iod in
    front of the eye plane, so nose offset / projected iod = NOSE_DEPTH * tan(yaw). Head roll is
    removed with the face midline (eye midpoint -> mouth midpoint), which yaw and pitch leave
    vertical; the eye line would not do (a yawed head tilts it when it also pitches). Good to ~10 deg."""
    k = np.asarray(kps, np.float64).reshape(5, 2)
    le, re, nose, lm, rm = k
    eye_mid, mouth_mid = (le + re) / 2, (lm + rm) / 2
    mid = mouth_mid - eye_mid
    face_h = float(np.linalg.norm(mid))
    if face_h < 1e-6:
        return 0.0, 0.0
    uy = mid / face_h                                  # down the face midline (handles head roll)
    ux = np.array([uy[1], -uy[0]])                     # toward the image's right for an upright face
    iod = float((re - le) @ ux)
    if iod < 1e-6:
        return 0.0, 0.0
    yaw = np.degrees(np.arctan2(float((nose - eye_mid) @ ux), NOSE_DEPTH * iod))
    p = float((nose - eye_mid) @ uy) / face_h
    pitch = np.degrees(np.arctan2(PITCH_NEUTRAL - p, PITCH_DEPTH))
    return float(yaw), float(pitch)


# ---- face observations and gates ---------------------------------------------------------------------
@dataclass
class FaceObs:
    bbox: np.ndarray                  # x1, y1, x2, y2 (pixels)
    kps: np.ndarray                   # (5, 2)
    det_score: float
    embedding: np.ndarray | None = None

    @property
    def width(self) -> float:
        return float(self.bbox[2] - self.bbox[0])


def check_frame(faces: list, shape, sharpness_of, cfg: dict) -> tuple:
    """-> (face or None, code, detail). Codes: ok | no_face | several_faces | too_small | off_frame | blurry.
    ``sharpness_of(face)`` is only called once the cheap gates pass."""
    c = dict(LIVE_DEFAULTS, **(cfg or {}))
    faces = [f for f in faces if f.det_score >= float(c["det_score"])]
    if not faces:
        return None, "no_face", "no face"
    if len(faces) > 1:
        return None, "several_faces", f"{len(faces)} faces in view"
    f = faces[0]
    if f.width < float(c["min_face_px"]):
        return None, "too_small", f"face {f.width:.0f} px < {c['min_face_px']} px"
    m, (h, w) = float(c["edge_margin_px"]), shape[:2]
    k = np.asarray(f.kps).reshape(5, 2)
    if (k[:, 0] < m).any() or (k[:, 1] < m).any() or (k[:, 0] > w - 1 - m).any() or (k[:, 1] > h - 1 - m).any():
        return None, "off_frame", "landmarks outside the frame"
    s = sharpness_of(f)
    if s < float(c["min_sharpness"]):
        return None, "blurry", f"sharpness {s:.0f} < {c['min_sharpness']}"
    return f, "ok", f"sharpness {s:.0f}"


@dataclass
class Candidate:
    t: float
    yaw: float
    pitch: float
    width: float
    sharpness: float
    embedding: np.ndarray
    gray: bool = False

    @property
    def quality(self) -> float:
        return float(np.log1p(self.sharpness) + self.width / 200.0)


def select_diverse(cands: list, k: int = 20, angle_scale=(1.0, 1.5)) -> list:
    """Up to ``k`` candidates spread over (yaw, pitch): the best near-frontal frame first, then
    repeatedly the one farthest (in angle) from those already chosen; quality breaks near-ties."""
    if len(cands) <= k:
        return sorted(cands, key=lambda c: c.yaw)
    ang = np.array([[c.yaw * angle_scale[0], c.pitch * angle_scale[1]] for c in cands])
    q = np.array([c.quality for c in cands])
    start = int(np.argmax(q - 0.05 * np.hypot(ang[:, 0], ang[:, 1])))
    chosen = [start]
    dmin = np.hypot(*(ang - ang[start]).T)
    while len(chosen) < k:
        score = dmin + 0.5 * (q - q.min())                            # degrees of angle, plus a little quality
        score[chosen] = -np.inf
        i = int(np.argmax(score))
        chosen.append(i)
        dmin = np.minimum(dmin, np.hypot(*(ang - ang[i]).T))
    return sorted((cands[i] for i in chosen), key=lambda c: c.yaw)


def coverage(cands: list) -> dict:
    """Which head poses the kept frames cover (|angle| >= 15 deg counts as turned)."""
    y = [c.yaw for c in cands]
    p = [c.pitch for c in cands]
    return {"frontal": any(abs(a) < 15 and abs(b) < 15 for a, b in zip(y, p)),
            "turned_right_in_image": any(a >= 15 for a in y), "turned_left_in_image": any(a <= -15 for a in y),
            "up": any(b >= 15 for b in p), "down": any(b <= -15 for b in p),
            "yaw_range": (min(y), max(y)) if y else None, "pitch_range": (min(p), max(p)) if p else None}


# ---- live scan / verify loops (camera and face model injected) ----------------------------------------
class LiveFaceScan:
    """Drives ``camera.read() -> RGB frame`` and ``face.detect(rgb) / face.embed(rgb, obs)``."""

    def __init__(self, camera, face, cfg: dict | None = None, log=print, clock=time.monotonic):
        self.cam, self.face, self.log, self.clock = camera, face, log, clock
        self.cfg = dict(LIVE_DEFAULTS, **(cfg or {}))
        self.rejects: dict = {}

    def run(self) -> list:
        t0 = self.clock()
        cands, n = [], 0
        self.log(f"Look at the camera, then turn your head slowly left, right, up and down "
                 f"({self.cfg['seconds']:.0f} s)...")
        while self.clock() - t0 < float(self.cfg["seconds"]):
            rgb = self.cam.read()
            if rgb is None:
                continue
            n += 1
            img, gray = self.face.prepare(rgb)
            faces = self.face.detect(img, prepared=True)
            f, code, _ = check_frame(faces, img.shape, lambda o: face_sharpness(img, o.bbox), self.cfg)
            if f is None:
                self.rejects[code] = self.rejects.get(code, 0) + 1
                continue
            emb = self.face.embed(img, f, prepared=True)
            yaw, pitch = head_angles(f.kps)
            c = Candidate(self.clock() - t0, yaw, pitch, f.width, face_sharpness(img, f.bbox), emb, gray)
            cands.append(c)
            self.log(f"  ok #{len(cands):3d}  t {c.t:4.1f} s  yaw {deg(yaw):>4}  pitch {deg(pitch):>4}  "
                     f"face {f.width:4.0f} px  sharp {c.sharpness:6.0f}{'  [IR->gray]' if gray else ''}")
        self.frames_seen = n
        return cands

    def summary(self, cands: list, kept: list) -> list:
        cov = coverage(kept)
        lines = [f"Live scan: {self.frames_seen} frames, {len(cands)} good, kept {len(kept)} "
                 f"(max {self.cfg['max_embeddings']})"]
        if cov["yaw_range"]:
            lines.append(f"  yaw {deg(cov['yaw_range'][0])}..{deg(cov['yaw_range'][1])} deg, "
                         f"pitch {deg(cov['pitch_range'][0])}..{deg(cov['pitch_range'][1])} deg; covered: "
                         + ", ".join(k for k in ("frontal", "turned_left_in_image", "turned_right_in_image", "up", "down")
                                     if cov[k]))
            missing = [k for k in ("frontal", "turned_left_in_image", "turned_right_in_image", "up", "down") if not cov[k]]
            if missing:
                lines.append("  not covered: " + ", ".join(missing))
        if self.rejects:
            lines.append("  rejected: " + ", ".join(f"{k} x{v}" for k, v in sorted(self.rejects.items(), key=lambda x: -x[1])))
        if len(kept) < int(self.cfg["min_kept"]):
            lines.append(f"WARNING: only {len(kept)} face embeddings kept (< {self.cfg['min_kept']}). Scan again "
                         "closer to the camera, in better light, turning more slowly.")
        return lines

    def scan(self) -> tuple:
        """-> (kept candidates, summary lines)."""
        cands = self.run()
        kept = select_diverse(cands, int(self.cfg["max_embeddings"]))
        lines = self.summary(cands, kept)
        for line in lines:
            self.log(line)
        return kept, lines


def live_verify(camera, face, profile, cfg: dict | None = None, seconds: float = 30.0, log=print,
                clock=time.monotonic) -> list:
    """Print the ArcFace score of the largest face vs the profile, frame by frame. -> [(t, score, level)]."""
    from .identity import merged_config, score_embeddings
    c = merged_config(cfg)
    if len(profile.face) == 0:
        raise ValueError("the profile has no face embeddings to verify against")
    out, t0 = [], clock()
    log(f"Verifying against {len(profile.face)} face embedding(s); ArcFace verify threshold "
        f"{c['face']['verify']:.2f}, faces >= {c['face']['min_width_px']} px. Ctrl-C to stop.")
    while clock() - t0 < seconds:
        rgb = camera.read()
        if rgb is None:
            continue
        img, gray = face.prepare(rgb)
        faces = sorted(face.detect(img, prepared=True), key=lambda f: -f.width)
        t = clock() - t0
        if not faces:
            log(f"  t {t:4.1f} s  no face")
            continue
        f = faces[0]
        emb = face.embed(img, f, prepared=True)
        r = score_embeddings(profile, c, face=emb, face_width_px=f.width)
        score = "  -  " if r.face is None else f"{r.face:5.2f}"
        log(f"  t {t:4.1f} s  face {f.width:4.0f} px  score {score}  {P.ID_LEVEL_NAMES[r.level]:<10}"
            f"{'  [IR->gray]' if gray else ''}{'  (+%d more faces)' % (len(faces) - 1) if len(faces) > 1 else ''}")
        out.append((t, r.face, r.level))
    return out


class PiCamera:
    """Picamera2 on the Camera Module 3 Wide NoIR with the imx708_wide_noir tuning file. RGB frames."""

    def __init__(self, tuning: str | None = "imx708_wide_noir.json", size=(1280, 720)):
        from picamera2 import Picamera2                 # apt install python3-picamera2
        kw = {}
        if tuning:
            try:
                kw["tuning"] = Picamera2.load_tuning_file(tuning)
            except (OSError, RuntimeError) as e:
                print(f"WARNING: tuning file {tuning} not loaded ({e}); using libcamera's default")
        self.cam = Picamera2(**kw)
        # Picamera2's "BGR888" format stores pixels in R, G, B byte order (its docs), i.e. an RGB array.
        self.cam.configure(self.cam.create_video_configuration(main={"size": tuple(size), "format": "BGR888"}))
        self.cam.start()
        time.sleep(1.0)                                  # let exposure / white balance settle

    def read(self):
        return self.cam.capture_array("main")

    def close(self):
        self.cam.stop()
        self.cam.close()
