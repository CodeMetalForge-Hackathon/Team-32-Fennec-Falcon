"""Thermal person detector: one model, three runtimes (Hailo NPU, ONNX Runtime CPU, OpenCV CPU).

The model is Alex's ``fusion/models/thermal_person.onnx``: Ultralytics YOLOv8n, 320 x 320 input,
4 classes (person, car, bicycle, other_vehicle), output 1 x 8 x 2100 with no NMS. It was trained
on HIT-UAV (aerial thermal from a drone 60-130 m up, white-hot 8-bit images), so its scores on a
perch view under the canopy are unknown until calibrated: treat them as a ranking, and let the
decision engine's likelihood ratios (``decide.lr.shape_*``) carry the meaning.

Preprocessing is the one ``fusion/app.py`` uses, so both apps score a frame the same way: the
temperature frame is stretched min..max (span >= 0.5 C) to 8-bit grey, white-hot, copied to 3
channels and letterboxed to 320 with grey 114.

Runtimes, first available wins (``life.thermal.model.prefer``):
* ``hailo``: a compiled HEF through hailo-apps' ``HailoInfer`` (the wrapper ``eagle/identity.py``
  already uses). It joins the NPU's round-robin scheduler (group "SHARED"), next to
  ``hailo_vision.py``. A HEF compiled by the Hailo Model Zoo carries NMS on the chip: one array
  per class of [y_min, x_min, y_max, x_max, score], normalised to the letterboxed input (as
  hailo-apps' object_detection_post_process.py reads it). A HEF without on-chip NMS returns the
  raw 1 x 8 x 2100 tensor and is decoded like the ONNX output.
* ``onnxruntime``: CPU. On a Pi 5 a 320 x 320 YOLOv8n is small enough for the 1-2 frames/s the
  decision needs; the NPU stays free for the camera models.
* ``opencv``: ``cv2.dnn`` on the CPU, the fallback ``fusion/app.py`` uses.

Nothing here has run on the Pi or the Hailo yet; the decode paths are tested on synthetic tensors
and the ONNX path on the real model file.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

KIT = Path(__file__).resolve().parent.parent
REPO = KIT.parent
THERMAL_CLASSES = ("person", "car", "bicycle", "other_vehicle")

DEFAULTS = {
    "onnx": str(REPO / "fusion" / "models" / "thermal_person.onnx"),
    "hef": "~/eagle-models/thermal_person.hef",
    "prefer": ["hailo", "onnxruntime", "opencv"],
    "size": 320,
    "conf": 0.30,
    "iou": 0.5,
    "person_class": 0,
    "every_n": 1,                   # run the model on every Nth thermal frame
    "min_span_c": 0.5,
}


@dataclass
class Box:
    x1: float
    y1: float
    x2: float
    y2: float
    score: float
    cls: int = 0

    @property
    def xyxy(self) -> list:
        return [self.x1, self.y1, self.x2, self.y2]

    def contains(self, u: float, v: float, pad: float = 0.0) -> bool:
        return self.x1 - pad <= u <= self.x2 + pad and self.y1 - pad <= v <= self.y2 + pad


# ---------------------------------------------------------------------------------- pre / post
def thermal_to_gray(temps_c, min_span_c: float = 0.5) -> np.ndarray:
    """Temperature frame (deg C) -> uint8 white-hot grey, min..max stretch (as fusion/app.py)."""
    t = np.asarray(temps_c, float)
    lo, hi = float(np.nanmin(t)), float(np.nanmax(t))
    g = np.clip((t - lo) / max(hi - lo, float(min_span_c)), 0.0, 1.0) * 255.0
    return np.nan_to_num(g).astype(np.uint8)


def letterbox(img: np.ndarray, size: int) -> tuple:
    """HxW or HxWx3 uint8 -> (size x size x 3 uint8, scale, (left, top)); centred, grey 114."""
    if img.ndim == 2:
        img = np.repeat(img[:, :, None], 3, axis=2)
    h, w = img.shape[:2]
    s = size / max(h, w)
    nh, nw = max(int(round(h * s)), 1), max(int(round(w * s)), 1)
    rs = _resize(img, nw, nh)
    canvas = np.full((size, size, 3), 114, np.uint8)
    top, left = (size - nh) // 2, (size - nw) // 2
    canvas[top:top + nh, left:left + nw] = rs
    return canvas, s, (left, top)


def _resize(img: np.ndarray, w: int, h: int) -> np.ndarray:
    try:
        import cv2
        return cv2.resize(img, (w, h), interpolation=cv2.INTER_LINEAR)
    except ImportError:                                          # nearest neighbour, numpy only
        ys = (np.arange(h) * img.shape[0] / h).astype(int)
        xs = (np.arange(w) * img.shape[1] / w).astype(int)
        return img[ys][:, xs]


def nms(boxes: np.ndarray, scores: np.ndarray, iou: float = 0.5) -> list:
    """Greedy non-maximum suppression on N x 4 xyxy boxes; returns kept indices, best first."""
    order = np.argsort(-scores)
    keep = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        r = order[1:]
        xx1 = np.maximum(boxes[i, 0], boxes[r, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[r, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[r, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[r, 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        area = lambda b: (b[..., 2] - b[..., 0]) * (b[..., 3] - b[..., 1])  # noqa: E731
        o = inter / np.maximum(area(boxes[i]) + area(boxes[r]) - inter, 1e-9)
        order = r[o <= iou]
    return keep


def _unletterbox(xyxy: np.ndarray, scale: float, pad: tuple, img_wh: tuple) -> np.ndarray:
    left, top = pad
    w, h = img_wh
    out = np.empty_like(xyxy, dtype=float)
    out[:, [0, 2]] = np.clip((xyxy[:, [0, 2]] - left) / scale, 0, w)
    out[:, [1, 3]] = np.clip((xyxy[:, [1, 3]] - top) / scale, 0, h)
    return out


def decode_yolov8(raw, scale: float, pad: tuple, img_wh: tuple, *, conf: float = 0.3,
                  iou: float = 0.5) -> list:
    """Raw YOLOv8 head (1 x (4+nc) x N or (4+nc) x N; cx, cy, w, h in input pixels) -> [Box]."""
    out = np.asarray(raw, float)
    out = out[0] if out.ndim == 3 else out
    out = out.T                                                  # N x (4 + nc)
    cls = out[:, 4:].argmax(1)
    sc = out[np.arange(len(out)), 4 + cls]
    k = sc >= conf
    if not np.any(k):
        return []
    out, cls, sc = out[k], cls[k], sc[k]
    cx, cy, bw, bh = out[:, 0], out[:, 1], out[:, 2], out[:, 3]
    xyxy = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], 1)
    res = []
    for c in np.unique(cls):                                     # class-aware NMS
        idx = np.where(cls == c)[0]
        for j in nms(xyxy[idx], sc[idx], iou):
            res.append((idx[j], int(c)))
    if not res:
        return []
    sel = np.array([i for i, _ in res])
    b = _unletterbox(xyxy[sel], scale, pad, img_wh)
    boxes = [Box(*map(float, b[n]), float(sc[i]), c) for n, (i, c) in enumerate(res)]
    return sorted(boxes, key=lambda x: -x.score)


def decode_hailo_nms(per_class, size: int, scale: float, pad: tuple, img_wh: tuple, *,
                     conf: float = 0.3) -> list:
    """Hailo on-chip NMS output: one array per class of [y_min, x_min, y_max, x_max, score],
    normalised to the size x size letterboxed input -> [Box] in image pixels."""
    boxes = []
    for c, dets in enumerate(per_class):
        a = np.asarray(dets, float).reshape(-1, 5)
        a = a[a[:, 4] >= conf]
        if not len(a):
            continue
        xyxy = np.stack([a[:, 1], a[:, 0], a[:, 3], a[:, 2]], 1) * size
        b = _unletterbox(xyxy, scale, pad, img_wh)
        boxes += [Box(*map(float, b[n]), float(a[n, 4]), c) for n in range(len(a))]
    return sorted(boxes, key=lambda x: -x.score)


# ---------------------------------------------------------------------------------- runtimes
class OrtRuntime:
    name = "onnxruntime (CPU)"

    def __init__(self, path):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = int(os.environ.get("EAGLE_ORT_THREADS", "2"))   # leave cores for audio
        self.s = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
        self.inp = self.s.get_inputs()[0].name

    def __call__(self, canvas: np.ndarray):
        x = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0   # BGR->RGB, NCHW
        return "raw", self.s.run(None, {self.inp: np.ascontiguousarray(x)})[0]


class CvRuntime:
    name = "opencv dnn (CPU)"

    def __init__(self, path):
        import cv2
        self.cv2 = cv2
        self.net = cv2.dnn.readNetFromONNX(str(path))

    def __call__(self, canvas: np.ndarray):
        self.net.setInput(self.cv2.dnn.blobFromImage(canvas, 1 / 255.0, swapRB=True))
        return "raw", self.net.forward()


class HailoRuntime:
    name = "hailo (NPU)"

    def __init__(self, hef):
        from hailo_apps.python.core.common.hailo_inference import HailoInfer
        self.m = HailoInfer(str(hef), batch_size=1, output_type="FLOAT32")
        h, w = self.m.get_input_shape()[:2]
        self.size = int(h)
        if h != w:
            raise RuntimeError(f"HEF input {h}x{w} is not square")
        self.nms = bool(getattr(self.m, "is_nms_postprocess_enabled", lambda: False)()) or self._has_nms()
        if not self.nms:      # a YOLOv8 HEF without on-chip NMS has six raw heads; only the NMS output is decoded
            raise RuntimeError(f"{hef} has no on-chip NMS output: compile it with NMS (pi/compile_thermal_hef.sh)")

    def _has_nms(self) -> bool:
        try:
            return any("nms" in str(o.name).lower() for o in self.m.hef.get_output_vstream_infos())
        except Exception:
            return False

    def __call__(self, canvas: np.ndarray):
        box = {}

        def done(completion_info, bindings_list):
            if completion_info.exception:
                box["err"] = completion_info.exception
            else:
                box["out"] = bindings_list[0].output().get_buffer()
        self.m.run([np.ascontiguousarray(canvas[:, :, ::-1])], done).wait(10000)   # BGR -> RGB uint8
        if "err" in box:
            raise RuntimeError(f"Hailo inference failed: {box['err']}")
        return ("nms" if self.nms else "raw"), box["out"]


class ThermalPersonModel:
    """Temperature frame in, person boxes (thermal pixels) out. ``.runtime`` says where it runs."""

    def __init__(self, cfg: dict | None = None, *, log=print):
        self.c = {**DEFAULTS, **(cfg or {})}
        self.size = int(self.c["size"])
        self.log = log
        self.runtime = None
        errors = []
        for name in self.c["prefer"]:
            try:
                self.runtime = self._open(name)
                break
            except Exception as e:                                # missing package, file or device
                errors.append(f"{name}: {type(e).__name__}: {e}")
        if self.runtime is None:
            raise RuntimeError("no runtime for the thermal person model (" + "; ".join(errors) + ")")
        if errors:
            log("MODEL   thermal person: skipped " + "; ".join(errors))
        if getattr(self.runtime, "size", self.size) != self.size:
            self.size = int(self.runtime.size)
        self.n, self.calls, self.ms = 0, 0, 0.0

    def _open(self, name: str):
        if name == "hailo":
            hef = Path(os.path.expanduser(str(self.c["hef"])))
            if not hef.exists():
                raise FileNotFoundError(f"{hef} (compile it: docs/pi-runtime.md, 'Thermal model on the Hailo')")
            return HailoRuntime(hef)
        path = Path(os.path.expanduser(str(self.c["onnx"])))
        if not path.is_absolute():
            path = KIT / path
        if not path.exists():
            raise FileNotFoundError(str(path))
        if name == "onnxruntime":
            return OrtRuntime(path)
        if name == "opencv":
            return CvRuntime(path)
        raise ValueError(f"unknown runtime {name!r}")

    @property
    def describe(self) -> str:
        avg = f", {self.ms / self.calls:.0f} ms/frame" if self.calls else ""
        return f"{self.runtime.name}{avg}"

    def due(self) -> bool:
        self.n += 1
        return (self.n - 1) % max(int(self.c["every_n"]), 1) == 0

    def __call__(self, temps_c, *, persons_only: bool = True) -> list:
        gray = thermal_to_gray(temps_c, float(self.c["min_span_c"]))
        h, w = gray.shape
        canvas, s, pad = letterbox(gray, self.size)
        t0 = time.perf_counter()
        kind, out = self.runtime(canvas)
        self.ms += (time.perf_counter() - t0) * 1e3
        self.calls += 1
        if kind == "nms":
            boxes = decode_hailo_nms(out, self.size, s, pad, (w, h), conf=float(self.c["conf"]))
        else:
            boxes = decode_yolov8(out, s, pad, (w, h), conf=float(self.c["conf"]), iou=float(self.c["iou"]))
        if persons_only:
            boxes = [b for b in boxes if b.cls == int(self.c["person_class"])]
        return boxes


def make_thermal_model(cfg: dict | None, *, log=print):
    """ThermalPersonModel from ``life.thermal.model`` or None (disabled / nothing available)."""
    if cfg is not None and cfg.get("enabled", True) is False:
        return None
    try:
        m = ThermalPersonModel(cfg or {}, log=log)
    except Exception as e:
        log(f"MODEL   thermal person: unavailable ({e}); warm-body detection only")
        return None
    log(f"MODEL   thermal person: {m.runtime.name}")
    return m
