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

    BOX = np.array([40.0, 20.0, 80.0, 60.0])       # inside the fake person box [10, 10, 110, 190]

    def faces(self, rgb, with_boxes=False):
        k = int(rgb[0, 0, 0])
        out = {1: [(self.vec, 60.0, 0.9)], 2: [(self.vec, 20.0, 0.8)],
               3: [(self.vec, 80.0, 0.9), (l2n(np.ones(512))[0], 50.0, 0.7)]}.get(k, [])
        return [f + (self.BOX,) for f in out] if with_boxes else out


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
    # far.jpg has a person but no kept face (20 px < 40 px): no person crop, so 3 CLIP (face, group, jacket)
    assert n == {"face": 2, "clip_image": 3, "clip_text": 3, "osnet": 2}
    joined = "\n".join(logs)
    assert "NOFACE far.jpg: largest face 20 px < 40 px" in joined
    assert "NOFACE jacket.jpg: no face found" in joined and "SKIP   broken.jpg" in joined
    assert "WARN   group.jpg: 2 faces" in joined and "ITEM   jacket.jpg" in joined
    assert "NOCROP far.jpg: no kept face" in joined
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


def test_consistent_faces_drops_a_different_person(en):
    rng = np.random.default_rng(0)
    mitch = l2n(rng.normal(size=512))[0]
    same = [l2n(mitch + 0.5 * l2n(rng.normal(size=512))[0])[0] for _ in range(6)]   # cos ~0.9 to centre
    other = l2n(rng.normal(size=512))[0]                                            # cos ~0 to centre
    faces = same[:3] + [other] + same[3:]
    names = [f"p{i}.jpg" for i in range(len(faces))]
    logs = []
    kept, dropped = en.consistent_faces(faces, names, min_cos=0.30, log=logs.append)
    assert len(kept) == 6 and [n for n, _ in dropped] == ["p3.jpg"]
    assert any(l.startswith("OUTLIER p3.jpg") for l in logs)


def test_consistent_faces_keeps_small_sets_and_can_be_disabled(en):
    rng = np.random.default_rng(1)
    faces = list(l2n(rng.normal(size=(2, 512))))
    assert len(en.consistent_faces(faces, ["a", "b"], min_cos=0.3, log=lambda *_: None)[0]) == 2
    faces = list(l2n(rng.normal(size=(4, 512))))
    assert len(en.consistent_faces(faces, list("abcd"), min_cos=None, log=lambda *_: None)[0]) == 4


def test_read_images_skips_duplicate_files(en, tmp_path):
    import cv2
    img = np.zeros((20, 20, 3), np.uint8)
    cv2.imwrite(str(tmp_path / "IMG_1.jpg"), img)
    (tmp_path / "IMG_1 (1).jpg").write_bytes((tmp_path / "IMG_1.jpg").read_bytes())
    cv2.imwrite(str(tmp_path / "IMG_2.png"), img + 5)
    names = [n for n, _ in en.read_images(tmp_path)]
    assert names == ["IMG_1 (1).jpg", "IMG_2.png"] or names == ["IMG_1.jpg", "IMG_2.png"]
    assert len(names) == 2


def test_person_box_for_face(en):
    boxes = [[0, 0, 400, 600], [300, 50, 500, 600], [320, 60, 420, 300]]
    face = [340, 80, 380, 120]
    assert en.person_box_for_face(boxes, face) == [320, 60, 420, 300]        # tightest box holding the face
    assert en.person_box_for_face(boxes, [450, 10, 520, 40]) is None          # mostly outside every box
    assert en.person_box_for_face([[0, 0, 100, 100]], [90, 10, 110, 30]) is None   # only half inside (< 80 %)


def test_appearance_only_from_the_target_persons_box(en):
    """Group photo: the crop comes from the box holding the kept face, not the largest person; a photo whose
    face is in no box, or whose face is an outlier, adds no CLIP/OSNet."""
    rng = np.random.default_rng(3)
    target = l2n(rng.normal(size=512))[0]
    stranger = l2n(rng.normal(size=512))[0]

    class Face:
        # first pixel: 1 target face at x 300-340 (inside the small right-hand person only),
        # 2 target face outside every person box, 3 a stranger's face (an outlier among the target's)
        def faces(self, rgb, with_boxes=False):
            k = int(rgb[0, 0, 0])
            f = {1: (target, 40.0, 0.9, np.array([300.0, 40, 340, 80])),
                 2: (target, 40.0, 0.9, np.array([560.0, 10, 600, 50])),
                 3: (stranger, 40.0, 0.9, np.array([300.0, 40, 340, 80])),
                 4: (target, 40.0, 0.9, np.array([300.0, 40, 340, 80]))}.get(k)
            return [f if with_boxes else f[:3]] if f is not None else []

    crops = []

    class Clip:
        def image_embedding(self, crop):
            crops.append(crop.shape[:2])
            return l2n(np.full(512, 1.0) + crop.shape[1])[0]

    def persons(rgb):                    # largest first: a big bystander on the left, the target on the right
        return [[0, 0, 280, 400], [290, 20, 400, 400]]

    def img(k):
        a = np.zeros((400, 620, 3), np.uint8)
        a[0, 0, 0] = k
        return a
    logs = []
    prof = en.enroll([("group.jpg", img(1)), ("nobox.jpg", img(2)), ("other.jpg", img(3)), ("again.jpg", img(4))],
                     "", Embedders(clip=Clip(), osnet=None, face=Face()), persons, min_face_px=30,
                     consistency=0.50, log=logs.append)       # 4 faces: the mean includes the stranger
    joined = "\n".join(logs)
    assert crops[0][1] < 150                     # the right-hand target box (110 px + padding), not the 280 px bystander
    assert "NOCROP nobox.jpg: the kept face is in none of the 2 person box(es)" in joined
    assert "OUTLIER other.jpg" in joined and "NOCROP other.jpg: face was an outlier" in joined
    assert prof.counts()["face"] == 3            # group, nobox (face kept, crop skipped), again; stranger dropped
    assert prof.counts()["clip_image"] == 2 and len(crops) == 3     # crops: group, other (later dropped), again
