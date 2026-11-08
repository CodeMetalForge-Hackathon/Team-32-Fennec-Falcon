"""Missing-person identity: target profile, per-track matcher and the model backends.

The profile (``target_profile.npz``, written by apps/enroll_target.py on the Pi) holds embeddings
of the missing person computed with the SAME models used at runtime:

* ``face``        ArcFace R50 (512-d) from InsightFace ``buffalo_l`` (SCRFD-10G detector),
                  onnxruntime on the CPU;
* ``clip_image``  CLIP ViT-B/32 image embeddings of person / clothing crops, from the Hailo HEF used
                  by the hailo-apps ``clip`` app (``clip_vit_b_32_image_encoder``);
* ``clip_text``   CLIP ViT-B/32 text embeddings of the description (``clip_vit_b_32_text_encoder``
                  through hailo-apps' ``run_text_encoder_inference``);
* ``osnet``       OSNet re-ID embeddings (torchreid weights exported to ONNX), onnxruntime CPU.

Runtime (:class:`IdentityMatcher`, per person track): appearance score from CLIP + OSNet cosine
similarity against the profile, and an ArcFace score only when the face crop is at least
``face.min_width_px`` wide. Match levels (``eagle.protocol.ID_*``)::

    0 none   1 appearance candidate   2 strong appearance   3 face verified

Each modality's best cosine is mapped linearly to 0..1 over its calibration range
``appearance.ranges[m] = [lo, hi]`` and the appearance score is the weighted mean over the
modalities that produced an embedding. All thresholds live in ``identity:`` in the config.
A track keeps its best level for ``hold_s`` so a turned head does not flicker the level.

Identity is ADDITIVE evidence only (see eagle/fusion.py): it may raise a track's priority and
confidence, never lower, suppress or delay a human. A missing model disables that modality with a
log line; nothing is ever downloaded at runtime (apps/fetch_models.py does that beforehand).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import protocol as P

MODALITIES = ("clip_image", "clip_text", "osnet")
DEFAULTS = {
    "models_dir": "~/eagle-models",
    "every_s": 1.0,                       # re-score a track at most this often (runtime)
    "hold_s": 30.0,                       # a track keeps its best level this long
    "appearance": {
        "weights": {"clip_image": 0.4, "clip_text": 0.2, "osnet": 0.4},
        "ranges": {"clip_image": [0.70, 0.90], "clip_text": [0.20, 0.32], "osnet": [0.40, 0.80]},
        "candidate": 0.5,                 # appearance score >= this -> level 1
        "strong": 0.8,                    # appearance score >= this -> level 2
        "min_crop_px": [32, 64],          # skip person crops smaller than w x h pixels
    },
    "face": {"min_width_px": 40, "verify": 0.40, "det_size": 640},
}


def merged_config(cfg: dict | None) -> dict:
    from .config import deep_merge
    return deep_merge(DEFAULTS, cfg or {})


def l2n(x) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    x = x.reshape(1, -1) if x.ndim == 1 else x
    return x / np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-12)


def best_cosine(query, gallery) -> float | None:
    """Highest cosine similarity between ``query`` (d,) and the rows of ``gallery`` (n, d)."""
    if query is None or gallery is None or len(gallery) == 0:
        return None
    q, g = l2n(query), l2n(gallery)
    if q.shape[1] != g.shape[1]:
        raise ValueError(f"embedding size {q.shape[1]} does not match the profile's {g.shape[1]}")
    return float((g @ q[0]).max())


# ---- profile --------------------------------------------------------------------------------------
@dataclass
class TargetProfile:
    face: np.ndarray = field(default_factory=lambda: np.zeros((0, 512), np.float32))
    clip_image: np.ndarray = field(default_factory=lambda: np.zeros((0, 512), np.float32))
    clip_text: np.ndarray = field(default_factory=lambda: np.zeros((0, 512), np.float32))
    osnet: np.ndarray = field(default_factory=lambda: np.zeros((0, 512), np.float32))
    description: str = ""
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        for k in ("face",) + MODALITIES:
            a = np.asarray(getattr(self, k), np.float32)
            setattr(self, k, l2n(a) if a.size else a.reshape(0, a.shape[-1] if a.ndim == 2 else 512))

    def counts(self) -> dict:
        return {k: int(len(getattr(self, k))) for k in ("face",) + MODALITIES}

    def save(self, path) -> None:
        np.savez_compressed(path, face=self.face, clip_image=self.clip_image, clip_text=self.clip_text,
                            osnet=self.osnet, description=np.array(self.description),
                            meta=np.array(json.dumps(self.meta)))

    @classmethod
    def load(cls, path) -> "TargetProfile":
        with np.load(os.path.expanduser(str(path)), allow_pickle=False) as z:
            return cls(z["face"], z["clip_image"], z["clip_text"], z["osnet"], str(z["description"]),
                       json.loads(str(z["meta"])))


# ---- scoring ---------------------------------------------------------------------------------------
@dataclass
class MatchResult:
    level: int                     # P.ID_*
    appearance: float              # 0..1 (0 when no appearance modality produced a score)
    face: float | None             # ArcFace cosine, None when no usable face (< min_width_px or none found)
    cosines: dict = field(default_factory=dict)

    def as_tuple(self) -> tuple:
        return self.level, self.appearance, self.face


def score_embeddings(profile: TargetProfile, cfg: dict, *, clip_image=None, osnet=None, face=None,
                     face_width_px: float | None = None) -> MatchResult:
    """Pure scoring of one observation's embeddings against the profile (no models involved)."""
    c = merged_config(cfg)
    a = c["appearance"]
    cos = {"clip_image": best_cosine(clip_image, profile.clip_image),
           "clip_text": best_cosine(clip_image, profile.clip_text),      # CLIP: image vs description text
           "osnet": best_cosine(osnet, profile.osnet)}
    num = den = 0.0
    for m in MODALITIES:
        if cos[m] is None:
            continue
        lo, hi = a["ranges"][m]
        s = float(np.clip((cos[m] - lo) / max(hi - lo, 1e-6), 0.0, 1.0))
        w = float(a["weights"].get(m, 0.0))
        num, den = num + w * s, den + w
    appearance = num / den if den > 0 else 0.0
    fscore = None
    if face is not None and face_width_px is not None and face_width_px >= float(c["face"]["min_width_px"]):
        fscore = best_cosine(face, profile.face)
    level = P.ID_NONE
    if appearance >= float(a["candidate"]):
        level = P.ID_APPEARANCE
    if appearance >= float(a["strong"]):
        level = P.ID_STRONG
    if fscore is not None and fscore >= float(c["face"]["verify"]):
        level = P.ID_FACE
    return MatchResult(level, float(appearance), fscore, cos)


def crop_box(frame, box, pad: float = 0.05):
    """Crop [x1, y1, x2, y2] (pixels) from an (H, W, 3) frame with a little padding, or None."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = (float(v) for v in box[:4])
    px, py = pad * (x2 - x1), pad * (y2 - y1)
    x1, y1 = int(max(0, x1 - px)), int(max(0, y1 - py))
    x2, y2 = int(min(w, x2 + px)), int(min(h, y2 + py))
    if x2 <= x1 or y2 <= y1:
        return None
    return frame[y1:y2, x1:x2]


class IdentityMatcher:
    """Per-track identity levels from person crops (``update``) or precomputed embeddings (``observe``)."""

    def __init__(self, profile: TargetProfile, embedders, cfg: dict | None = None, clock=time.time):
        self.profile, self.emb, self.cfg = profile, embedders, merged_config(cfg)
        self.clock = clock
        self.tracks: dict = {}                       # track id -> {"t": last scored, "best": [(t, MatchResult)]}

    def observe(self, track_id, t: float | None = None, **embeddings) -> MatchResult:
        """Score embeddings for a track and return its held (best recent) result."""
        t = self.clock() if t is None else t
        r = score_embeddings(self.profile, self.cfg, **embeddings)
        st = self.tracks.setdefault(track_id, {"t": t, "hist": []})
        st["t"] = t
        hold = float(self.cfg["hold_s"])
        st["hist"] = [(tt, rr) for tt, rr in st["hist"] if t - tt <= hold] + [(t, r)]
        return self.held(track_id)

    def held(self, track_id) -> MatchResult:
        st = self.tracks.get(track_id)
        if not st or not st["hist"]:
            return MatchResult(P.ID_NONE, 0.0, None)
        best = max(st["hist"], key=lambda p: (p[1].level, p[1].appearance))[1]
        faces = [rr.face for _, rr in st["hist"] if rr.face is not None]
        return MatchResult(best.level, max(rr.appearance for _, rr in st["hist"]), max(faces) if faces else None,
                           best.cosines)

    def update(self, track_id, frame_rgb, box) -> tuple:
        """Runtime entry (apps/hailo_vision.py): crop, embed (throttled per track), score.
        -> (level, appearance, face) for the vision JSON line."""
        now = self.clock()
        st = self.tracks.get(track_id)
        if st is not None and now - st["t"] < float(self.cfg["every_s"]):
            return self.held(track_id).as_tuple()
        crop = crop_box(frame_rgb, box)
        mw, mh = self.cfg["appearance"]["min_crop_px"]
        if crop is None or crop.shape[1] < mw or crop.shape[0] < mh:
            return self.held(track_id).as_tuple()
        e = self.emb.embed_person(crop)
        return self.observe(track_id, now, **e).as_tuple()


# ---- model backends (Pi; each one optional) -----------------------------------------------------------
def _onnx_session(path):
    import onnxruntime as ort                             # pip install onnxruntime (CPU)
    return ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])


class OsnetEmbedder:
    """torchreid OSNet exported to ONNX (apps/fetch_models.py): 256x128 RGB, ImageNet normalisation."""

    MEAN = np.array([0.485, 0.456, 0.406], np.float32)
    STD = np.array([0.229, 0.224, 0.225], np.float32)

    def __init__(self, path):
        self.sess = _onnx_session(path)
        self.inp = self.sess.get_inputs()[0].name

    def __call__(self, crop_rgb) -> np.ndarray:
        import cv2
        x = cv2.resize(np.ascontiguousarray(crop_rgb), (128, 256), interpolation=cv2.INTER_LINEAR)
        x = ((x.astype(np.float32) / 255.0 - self.MEAN) / self.STD).transpose(2, 0, 1)[None]
        return l2n(self.sess.run(None, {self.inp: x})[0])[0]


class FaceEmbedder:
    """InsightFace buffalo_l (SCRFD-10G + ArcFace R50) on the CPU through the insightface package.
    ``root`` must already contain models/buffalo_l (FaceAnalysis would otherwise download it)."""

    def __init__(self, root, det_size: int = 640):
        root = Path(os.path.expanduser(str(root)))
        if not any((root / "models" / "buffalo_l").glob("*.onnx")):
            raise FileNotFoundError(f"buffalo_l not found under {root}/models/buffalo_l: run apps/fetch_models.py")
        from insightface.app import FaceAnalysis           # pip install insightface onnxruntime
        self.app = FaceAnalysis(name="buffalo_l", root=str(root), allowed_modules=["detection", "recognition"],
                                providers=["CPUExecutionProvider"])
        self.app.prepare(ctx_id=-1, det_size=(det_size, det_size))

    def faces(self, rgb) -> list:
        """-> [(normed 512-d embedding, face width px, det score)], largest face first."""
        bgr = np.ascontiguousarray(rgb[..., ::-1])
        out = [(f.normed_embedding, float(f.bbox[2] - f.bbox[0]), float(f.det_score)) for f in self.app.get(bgr)]
        return sorted(out, key=lambda f: -f[1])


class ClipHailo:
    """CLIP ViT-B/32 on the Hailo, exactly the HEFs of the hailo-apps clip app (needs hailo-apps)."""

    def __init__(self, image_hef, text_hef=None, tokenizer=None, token_embeddings=None, text_projection=None):
        from hailo_apps.python.core.common.hailo_inference import HailoInfer
        self.image = HailoInfer(str(image_hef), batch_size=1, output_type="FLOAT32")
        self.h, self.w = self.image.get_input_shape()[:2]
        self.text_args = dict(hef_path=str(text_hef), tokenizer_path=tokenizer, embeddings_path=token_embeddings,
                              text_projection_path=text_projection) if text_hef else None

    def image_embedding(self, crop_rgb) -> np.ndarray:
        import cv2
        h, w = crop_rgb.shape[:2]
        s = max(self.h / h, self.w / w)                    # CLIP preprocessing: resize short side, centre crop
        r = cv2.resize(np.ascontiguousarray(crop_rgb), (max(self.w, round(w * s)), max(self.h, round(h * s))),
                       interpolation=cv2.INTER_CUBIC)
        y0, x0 = (r.shape[0] - self.h) // 2, (r.shape[1] - self.w) // 2
        x = np.ascontiguousarray(r[y0:y0 + self.h, x0:x0 + self.w])
        box = {}

        def done(completion_info, bindings_list):
            box["out"] = np.array(bindings_list[0].output().get_buffer(), np.float32)
        self.image.run([x], done).wait(10000)
        return l2n(box["out"].reshape(-1))[0]

    def text_embeddings(self, texts: list) -> np.ndarray:
        if not self.text_args:
            raise RuntimeError("no CLIP text encoder HEF configured")
        from hailo_apps.python.pipeline_apps.clip.clip_text_utils import run_text_encoder_inference
        return l2n(run_text_encoder_inference(list(texts), **self.text_args))


@dataclass
class Embedders:
    """The runtime models; any of them may be None (that modality is then skipped)."""
    clip: object = None
    osnet: object = None
    face: object = None
    log: object = print

    def embed_person(self, crop_rgb) -> dict:
        out = {}
        if self.clip is not None:
            out["clip_image"] = self.clip.image_embedding(crop_rgb)
        if self.osnet is not None:
            out["osnet"] = self.osnet(crop_rgb)
        if self.face is not None:
            f = self.face.faces(crop_rgb)
            if f:
                out["face"], out["face_width_px"] = f[0][0], f[0][1]
        return out


def _hef(d: Path, name: str) -> Path:
    """clip/<name>, else clip/hailo8l/<name>, else clip/hailo8/<name> (apps/fetch_models.py layout)."""
    for sub in ("", "hailo8l", "hailo8"):
        p = d / "clip" / sub / name if sub else d / "clip" / name
        if p.is_file():
            return p
    return d / "clip" / name


def model_paths(cfg: dict | None) -> dict:
    c = merged_config(cfg)
    d = Path(os.path.expanduser(c["models_dir"]))
    m = c.get("models", {})

    def pick(key, default):
        return Path(os.path.expanduser(str(m.get(key) or default)))
    return {"insightface_root": pick("insightface_root", d / "insightface"),
            "osnet": pick("osnet", d / "osnet" / "osnet_x1_0_msmt17.onnx"),
            "clip_image_hef": pick("clip_image_hef", _hef(d, "clip_vit_b_32_image_encoder.hef")),
            "clip_text_hef": pick("clip_text_hef", _hef(d, "clip_vit_b_32_text_encoder.hef")),
            "clip_tokenizer": pick("clip_tokenizer", d / "clip" / "clip_tokenizer.json"),
            "clip_token_embeddings": pick("clip_token_embeddings", d / "clip" / "token_embedding_lut.npy"),
            "clip_text_projection": pick("clip_text_projection", d / "clip" / "text_projection.npy")}


def make_runtime_embedders(cfg: dict | None, *, text: bool = False, log=print) -> Embedders:
    """Load every model that is present locally; log (never download) the missing ones."""
    c, p = merged_config(cfg), model_paths(cfg)
    e = Embedders(log=log)
    try:
        if not p["clip_image_hef"].is_file():
            raise FileNotFoundError(p["clip_image_hef"])
        e.clip = ClipHailo(p["clip_image_hef"], p["clip_text_hef"] if text else None,
                           str(p["clip_tokenizer"]), str(p["clip_token_embeddings"]), str(p["clip_text_projection"]))
    except Exception as ex:                                   # noqa: BLE001 - any failure disables CLIP only
        log(f"identity: CLIP off ({ex!r})")
    try:
        if not p["osnet"].is_file():
            raise FileNotFoundError(p["osnet"])
        e.osnet = OsnetEmbedder(p["osnet"])
    except Exception as ex:                                   # noqa: BLE001
        log(f"identity: OSNet off ({ex!r})")
    try:
        e.face = FaceEmbedder(p["insightface_root"], int(c["face"]["det_size"]))
    except Exception as ex:                                   # noqa: BLE001
        log(f"identity: face off ({ex!r})")
    return e
