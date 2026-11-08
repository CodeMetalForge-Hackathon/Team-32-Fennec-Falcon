"""apps/enroll_target.py with fake models (synthetic embeddings) and the real person detector file."""
import importlib.util

import numpy as np
import pytest

from eagle.identity import Embedders, TargetProfile, l2n, score_embeddings


@pytest.fixture(scope="module")
def en(root):
    spec = importlib.util.spec_from_file_location("enroll_target", root / "apps/enroll_target.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeFace:
    """Faces keyed by the image's first pixel value: 1 = one 60 px face, 2 = a 20 px face, 3 = two faces."""

    def __init__(self, vec):
        self.vec = vec

    def faces(self, rgb):
        k = int(rgb[0, 0, 0])
        return {1: [(self.vec, 60.0, 0.9)], 2: [(self.vec, 20.0, 0.8)],
                3: [(self.vec, 80.0, 0.9), (l2n(np.ones(512))[0], 50.0, 0.7)]}.get(k, [])


class FakeClip:
    def __init__(self):
        self.images, self.texts = [], []

    def image_embedding(self, crop):
        self.images.append(crop.shape)
        return l2n(np.full(512, crop.shape[0] % 7 + 1.0) + np.arange(512))[0]

    def text_embeddings(self, texts):
        self.texts += list(texts)
        return l2n(np.random.default_rng(len(texts)).normal(size=(len(texts), 512)))


def test_description_prompts(en):
    p = en.description_prompts("red rain jacket, blue jeans; grey beanie")
    assert p[0].startswith("a photo of a person: red rain jacket") and len(p) == 4
    assert en.description_prompts("   ") == []


def test_enroll_logs_and_skips_photos_without_usable_face(en):
    face_vec = l2n(np.random.default_rng(0).normal(size=512))[0]
    clip = FakeClip()
    emb = Embedders(clip=clip, osnet=lambda crop: l2n(np.full(512, 2.0))[0], face=FakeFace(face_vec))

    def img(k, h=200, w=120):
        a = np.full((h, w, 3), 50, np.uint8)
        a[0, 0, 0] = k
        return a

    def persons(rgb):                                     # images 1-3 contain a person, 0 is a jacket photo
        return [[10, 10, 110, 190]] if rgb[0, 0, 0] in (1, 2, 3) else []
    logs = []
    prof = en.enroll([("face.jpg", img(1)), ("far.jpg", img(2)), ("group.jpg", img(3)), ("jacket.jpg", img(0)),
                      ("broken.jpg", None)], "red jacket, jeans", emb, persons, min_face_px=40, log=logs.append)
    n = prof.counts()
    assert n == {"face": 2, "clip_image": 4, "clip_text": 3, "osnet": 3}
    joined = "\n".join(logs)
    assert "NOFACE far.jpg: largest face 20 px < 40 px" in joined
    assert "NOFACE jacket.jpg: no face found" in joined and "SKIP   broken.jpg" in joined
    assert "WARN   group.jpg: 2 faces" in joined and "ITEM   jacket.jpg" in joined
    assert len(clip.texts) == 3 and prof.description == "red jacket, jeans"
    assert {s[0] for s in prof.meta["skipped"]} == {"far.jpg", "jacket.jpg", "broken.jpg"}
    # the enrolled face verifies at runtime with the same (fake) model
    r = score_embeddings(prof, {}, face=face_vec, face_width_px=45)
    assert r.level == 3


def test_enroll_profile_roundtrip(en, tmp_path):
    emb = Embedders(clip=None, osnet=lambda crop: l2n(np.arange(512.0))[0], face=None)
    prof = en.enroll([("a.png", np.zeros((300, 150, 3), np.uint8))], "", emb, lambda rgb: [[0, 0, 150, 300]],
                     log=lambda s: None)
    prof.save(tmp_path / "p.npz")
    q = TargetProfile.load(tmp_path / "p.npz")
    assert q.counts()["osnet"] == 1 and q.counts()["face"] == 0 and q.meta["models"]["osnet"]


def test_person_detector_reuses_fusion_yolo(en):
    pytest.importorskip("cv2")
    if not en.PERSON_MODEL.is_file():
        pytest.skip("fusion/models/coco_yolov8n_320.onnx not present")
    detect = en.load_person_detector()
    assert detect(np.full((240, 320, 3), 127, np.uint8)) == []          # flat grey: nobody there
