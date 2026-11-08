"""Live face scan (eagle/facescan.py, enroll_target --live/--verify) with synthetic frames and embeddings."""
import importlib.util
from types import SimpleNamespace

import numpy as np
import pytest

from eagle import protocol as P
from eagle.facescan import (Candidate, FaceObs, LiveFaceScan, check_frame, chroma_spread, coverage, face_sharpness,
                            head_angles, laplacian_variance, live_verify, prepare_for_face, select_diverse, to_gray3)
from eagle.identity import FaceEmbedder, TargetProfile, l2n, score_embeddings

RNG = np.random.default_rng(11)


def unit(d=512):
    return l2n(RNG.normal(size=d))[0]


def near(ref, c):
    o = RNG.normal(size=ref.size)
    o -= (o @ ref) * ref
    o /= np.linalg.norm(o)
    return c * ref + np.sqrt(1 - c * c) * o


def colour_scene(h=240, w=320):
    img = np.zeros((h, w, 3), np.uint8)
    img[:, : w // 3] = (200, 40, 40)
    img[:, w // 3: 2 * w // 3] = (40, 180, 60)
    img[:, 2 * w // 3:] = (50, 60, 210)
    return np.clip(img + RNG.normal(0, 8, img.shape), 0, 255).astype(np.uint8)


def ir_scene(h=240, w=320, tint=(1.15, 0.92, 1.10)):
    """NoIR under IR light: monochrome texture with a uniform pink/purple tint."""
    g = np.clip(RNG.normal(120, 45, (h, w)), 0, 210)
    return np.clip(np.stack([g * tint[0], g * tint[1], g * tint[2]], axis=2), 0, 255).astype(np.uint8)


# ---- IR / low colour ---------------------------------------------------------------------------------
def test_low_colour_frames_become_gray3():
    col, ir = colour_scene(), ir_scene()
    assert chroma_spread(col) > 5.0 and chroma_spread(ir) < 1.0 and chroma_spread(to_gray3(col)) < 1e-3
    img, conv = prepare_for_face(col, 2.0)
    assert not conv and img is not None and np.array_equal(img, col)
    img, conv = prepare_for_face(ir, 2.0)
    assert conv and img.shape == ir.shape and np.array_equal(img[..., 0], img[..., 1])
    assert np.array_equal(img[..., 1], img[..., 2])
    assert not prepare_for_face(ir, 0)[1]                              # threshold 0 disables it
    dark = (ir // 20).astype(np.uint8)                                  # near-black frame: no false colour
    assert chroma_spread(dark) < 1e-3


def test_face_embedder_applies_ir_conversion_for_detect_and_embed():
    seen = []

    class Det:
        def detect(self, bgr, max_num=0, metric="default"):
            seen.append(("det", bgr.copy()))
            k = np.array([[140, 100], [180, 100], [160, 120], [145, 140], [175, 140]], float)
            return np.array([[120, 70, 200, 170, 0.9]]), np.array([k])

    class Rec:
        def get(self, bgr, face):
            seen.append(("rec", bgr.copy()))
            face.embedding = np.ones(512)

    fe = FaceEmbedder(None, gray_threshold=2.0, _app=SimpleNamespace(det_model=Det(), models={"recognition": Rec()}))
    out = fe.faces(ir_scene())
    assert len(out) == 1 and out[0][1] == 80.0 and np.isclose(np.linalg.norm(out[0][0]), 1.0)
    for _, img in seen:                                                  # both SCRFD and ArcFace saw gray x3
        assert np.array_equal(img[..., 0], img[..., 1]) and np.array_equal(img[..., 1], img[..., 2])
    seen.clear()
    col = colour_scene()
    fe.faces(col)
    assert all(np.array_equal(img, col[..., ::-1]) for _, img in seen)   # colour frames untouched (BGR)


# ---- sharpness, angles, gates ------------------------------------------------------------------------------
def test_sharpness():
    sharp = (np.indices((120, 120)).sum(0) % 2 * 255).astype(np.uint8)
    blur = np.full((120, 120), 128, np.uint8)
    assert laplacian_variance(sharp) > 1000 and laplacian_variance(blur) == 0.0
    img = np.dstack([sharp] * 3)
    assert face_sharpness(img, [10, 10, 100, 110]) > 1000 and face_sharpness(img, [0, 0, 2, 2]) == 0.0


def landmarks(yaw_deg, pitch_deg, roll_deg=0.0, iod=60.0, centre=(320, 240)):
    """Five SCRFD-style landmarks of a simple 3D face model (units of inter-ocular distance), rotated by
    yaw then pitch, projected orthographically, rolled in the image plane."""
    model = np.array([[-0.5, 0, 0], [0.5, 0, 0], [0, 0.55, 0.6], [-0.35, 1.0, 0], [0.35, 1.0, 0]])
    y, p, r = np.radians([yaw_deg, pitch_deg, roll_deg])
    Ry = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
    Rp = np.array([[1, 0, 0], [0, np.cos(p), -np.sin(p)], [0, np.sin(p), np.cos(p)]])   # up: nose tip rises
    xy = (model @ Ry.T @ Rp.T)[:, :2]
    R2 = np.array([[np.cos(r), -np.sin(r)], [np.sin(r), np.cos(r)]])
    return xy @ R2.T * iod + np.asarray(centre)


@pytest.mark.parametrize("yaw,pitch,roll", [(0, 0, 0), (30, 0, 0), (-40, 0, 0), (0, 20, 0), (0, -20, 0),
                                            (25, -15, 0), (-30, 15, 20), (35, 10, -15)])
def test_head_angles_from_landmarks(yaw, pitch, roll):
    ey, ep = head_angles(landmarks(yaw, pitch, roll))
    assert ey == pytest.approx(yaw, abs=5) and ep == pytest.approx(pitch, abs=5)


def test_head_angle_signs():
    assert head_angles(landmarks(25, 0))[0] > 0          # nose moved toward the image's right
    assert head_angles(landmarks(0, 20))[1] > 0          # looking up: nose moves up in the image


def obs(w=120, x=260, y=160, score=0.9, kps=None):
    k = landmarks(0, 0, iod=w * 0.4, centre=(x + w / 2, y + w * 0.3)) if kps is None else kps
    return FaceObs(np.array([x, y, x + w, y + w * 1.2]), k, score)


def test_check_frame_gates(cfg):
    live = cfg["identity"]["live"]
    sharp = lambda f: 500.0  # noqa: E731
    assert check_frame([], (480, 640, 3), sharp, live)[1] == "no_face"
    assert check_frame([obs(), obs(x=10)], (480, 640, 3), sharp, live)[1] == "several_faces"
    assert check_frame([obs(), obs(x=10, score=0.2)], (480, 640, 3), sharp, live)[1] == "ok"   # weak 2nd ignored
    assert check_frame([obs(w=70)], (480, 640, 3), sharp, live)[1] == "too_small"
    edge = obs(x=600, w=100)                                             # landmarks past the right edge
    assert check_frame([edge], (480, 640, 3), sharp, live)[1] == "off_frame"
    assert check_frame([obs()], (480, 640, 3), lambda f: 10.0, live)[1] == "blurry"
    f, code, _ = check_frame([obs(w=80)], (480, 640, 3), sharp, live)
    assert code == "ok" and f.width == 80
    assert (live["min_face_px"], live["max_embeddings"], live["min_kept"]) == (80, 20, 8)


def cand(yaw, pitch, q=200.0, emb=None):
    return Candidate(0.0, yaw, pitch, 120.0, q, emb if emb is not None else unit())


def test_select_diverse_spreads_angles():
    cands = [cand(RNG.normal(0, 3), RNG.normal(0, 3), RNG.uniform(100, 300)) for _ in range(180)]
    cands += [cand(-40 + RNG.normal(0, 2), 0) for _ in range(8)] + [cand(40 + RNG.normal(0, 2), 0) for _ in range(8)]
    cands += [cand(0, 25), cand(0, -25), cand(2, 26)]
    kept = select_diverse(cands, 20)
    assert len(kept) == 20 and [c.yaw for c in kept] == sorted(c.yaw for c in kept)
    cov = coverage(kept)
    assert all(cov[k] for k in ("frontal", "turned_left_in_image", "turned_right_in_image", "up", "down"))
    assert sum(c.yaw < -30 for c in kept) >= 2 and sum(c.yaw > 30 for c in kept) >= 2   # both sides, not 20 frontal
    few = [cand(0, 0), cand(10, 0)]
    assert select_diverse(few, 20) == sorted(few, key=lambda c: c.yaw)


# ---- the live loops with a fake camera and face model --------------------------------------------------------
class FakeCamera:
    """Each read() returns the next frame and advances the fake clock by 1/fps."""

    def __init__(self, frames, fps=5.0):
        self.frames, self.i, self.t, self.dt = frames, 0, 0.0, 1.0 / fps

    def clock(self):
        return self.t

    def read(self):
        f = self.frames[self.i % len(self.frames)]
        self.i += 1
        self.t += self.dt
        return f["img"]


class FakeFace:
    """Wraps the real IR conversion; detect/embed follow a per-frame script stored on the image."""

    def __init__(self, script, person):
        self.script, self.person = script, person

    def prepare(self, rgb):
        return prepare_for_face(rgb, 2.0)

    def detect(self, img, prepared=False):
        return self.script[int(img[0, 0, 0]) if not np.array_equal(img[..., 0], img[..., 1]) else int(img[0, 0, 1])]

    def embed(self, img, f, prepared=False):
        f.embedding = near(self.person, 0.8)
        return f.embedding


def textured(key, ir=False, h=480, w=640):
    img = ir_scene(h, w) if ir else np.clip(colour_scene(h, w).astype(int) + RNG.integers(0, 60, (h, w, 3)), 0, 255)
    img = img.astype(np.uint8)
    img[0, 0, :] = key                                  # frame id readable after gray conversion too
    return {"img": img}


def scripted_scan(n_good, yaws, extra_bad=True, ir_every=0):
    script, frames = {}, []
    k = 1
    for i in range(n_good):
        y = yaws[i % len(yaws)]
        script[k] = [FaceObs(np.array([260, 150, 380, 300]), landmarks(y, 0, iod=48, centre=(320, 200)), 0.9)]
        frames.append(textured(k, ir=bool(ir_every) and i % ir_every == 0))
        k += 1
    if extra_bad:
        script[k] = []
        frames.append(textured(k))
        script[k + 1] = [FaceObs(np.array([260, 150, 320, 220]), landmarks(0, 0, iod=24, centre=(290, 170)), 0.9)]
        frames.append(textured(k + 1))
        script[k + 2] = [FaceObs(np.array([100, 150, 220, 300]), landmarks(0, 0, iod=48, centre=(160, 200)), 0.9),
                         FaceObs(np.array([400, 150, 520, 300]), landmarks(0, 0, iod=48, centre=(460, 200)), 0.9)]
        frames.append(textured(k + 2))
    return script, frames


def test_live_scan_keeps_good_frames_and_warns(cfg):
    person = unit()
    script, frames = scripted_scan(5, [0, -30, 30], ir_every=2)
    cam = FakeCamera(frames, fps=5.0)
    logs = []
    scan = LiveFaceScan(cam, FakeFace(script, person), dict(cfg["identity"]["live"], seconds=1.5), log=logs.append,
                        clock=cam.clock)
    kept, summary = scan.scan()
    oks = [s for s in logs if s.lstrip().startswith("ok #")]
    assert len(kept) >= 5 and len(oks) == len(kept) and any("[IR->gray]" in s for s in oks)
    assert any("WARNING: only" in s for s in summary)                    # 8 good frames needed
    assert "rejected:" in " ".join(summary) and "several_faces" in " ".join(summary)
    assert scan.rejects.get("too_small") and scan.rejects.get("no_face")


def test_live_scan_enough_frames_no_warning(cfg):
    person = unit()
    script, frames = scripted_scan(40, [-35, -20, -5, 0, 5, 20, 35], extra_bad=False)
    cam = FakeCamera(frames, fps=10.0)
    logs = []
    kept, summary = LiveFaceScan(cam, FakeFace(script, person), cfg["identity"]["live"], log=logs.append,
                                 clock=cam.clock).scan()
    assert len(kept) == 20 and not any("WARNING" in s for s in summary)
    cov = coverage(kept)
    assert cov["turned_left_in_image"] and cov["turned_right_in_image"] and cov["frontal"]


@pytest.fixture(scope="module")
def en(root):
    spec = importlib.util.spec_from_file_location("enroll_target_live", root / "apps/enroll_target.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_live_and_photo_faces_share_one_profile(en, tmp_path, cfg):
    person = unit()
    photo = TargetProfile(face=np.stack([near(person, 0.85), near(person, 0.8)]), osnet=np.stack([unit()]),
                          description="red jacket", meta={"models": {"osnet": "x"}})
    kept = [cand(y, 0, emb=near(person, 0.8)) for y in (-30, 0, 30)]
    prof = en.add_live_faces(photo, kept, ["summary line"])
    prof.save(tmp_path / "target_profile.npz")
    q = TargetProfile.load(tmp_path / "target_profile.npz")
    assert q.counts() == {"face": 5, "clip_image": 0, "clip_text": 0, "osnet": 1}
    assert q.meta["face_sources"] == {"photos": 2, "live": 3} and q.meta["live_scan"]["yaw_deg"] == [-30, 0, 30]
    assert q.meta["models"]["osnet"] == "x" and q.description == "red jacket"
    r = score_embeddings(q, cfg["identity"], face=near(person, 0.9), face_width_px=60)
    assert r.level == P.ID_FACE                                          # the same matcher reads it
    only_live = en.add_live_faces(None, kept, [])
    assert only_live.counts()["face"] == 3 and only_live.meta["face_sources"] == {"photos": 0, "live": 3}
    assert en.add_live_faces(None, [], ["none kept"]).counts()["face"] == 0


def test_cli_requires_a_mode(en):
    with pytest.raises(SystemExit):
        en.main([])


def test_live_verify_prints_scores(cfg):
    person, stranger = unit(), unit()
    prof = TargetProfile(face=np.stack([near(person, 0.9)]))
    box = np.array([260, 150, 380, 300])
    kps = landmarks(0, 0, iod=48, centre=(320, 200))
    script = {1: [FaceObs(box, kps, 0.9)], 2: [], 3: [FaceObs(box, kps, 0.9)]}
    frames = [textured(1), textured(2), textured(3, ir=True)]
    cam = FakeCamera(frames, fps=3.0)

    class VFace(FakeFace):
        def embed(self, img, f, prepared=False):
            k = int(img[0, 0, 1]) if np.array_equal(img[..., 0], img[..., 1]) else int(img[0, 0, 0])
            return near(person, 0.85) if k == 1 else near(stranger, 0.95)
    logs = []
    res = live_verify(cam, VFace(script, person), prof, cfg["identity"], seconds=1.0, log=logs.append, clock=cam.clock)
    assert [lv for _, _, lv in res] == [P.ID_FACE, P.ID_NONE]
    assert any("no face" in s for s in logs) and any("[IR->gray]" in s for s in logs)
    assert any("FACE" in s and "score" in s for s in logs)
    with pytest.raises(ValueError):
        live_verify(cam, VFace(script, person), TargetProfile(), cfg["identity"], seconds=0.1, log=logs.append)
