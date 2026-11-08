"""Identity matcher with synthetic embeddings (no models needed)."""
import socket

import numpy as np
import pytest

from eagle import protocol as P
from eagle.identity import (DEFAULTS, IdentityMatcher, TargetProfile, best_cosine, crop_box, l2n,
                            make_runtime_embedders, score_embeddings)

RNG = np.random.default_rng(7)


def unit(d=512):
    return l2n(RNG.normal(size=d))[0]


def at_cosine(ref, c):
    """A unit vector with cosine exactly ``c`` to ``ref``."""
    o = RNG.normal(size=ref.size)
    o -= (o @ ref) * ref
    o /= np.linalg.norm(o)
    return c * ref + np.sqrt(max(0.0, 1 - c * c)) * o


@pytest.fixture(scope="module")
def prof():
    return TargetProfile(face=np.stack([unit(), unit()]), clip_image=np.stack([unit(), unit(), unit()]),
                         clip_text=np.stack([unit()]), osnet=np.stack([unit(), unit()]),
                         description="red jacket, blue jeans", meta={"models": "test"})


def test_profile_roundtrip(tmp_path, prof):
    p = tmp_path / "target_profile.npz"
    prof.save(p)
    q = TargetProfile.load(p)
    assert q.counts() == {"face": 2, "clip_image": 3, "clip_text": 1, "osnet": 2}
    assert q.description == "red jacket, blue jeans" and q.meta == {"models": "test"}
    assert np.allclose(q.face, prof.face) and np.allclose(np.linalg.norm(q.osnet, axis=1), 1)


def test_best_cosine():
    a = unit()
    assert best_cosine(a, np.stack([unit(), a])) == pytest.approx(1.0)
    assert best_cosine(a, np.zeros((0, 512))) is None and best_cosine(None, np.stack([a])) is None
    with pytest.raises(ValueError):
        best_cosine(a, np.stack([unit(256)]))


def test_levels_from_appearance_and_face(prof, cfg):
    c = cfg["identity"]
    stranger = score_embeddings(prof, c, clip_image=unit(), osnet=unit())
    assert stranger.level == P.ID_NONE and stranger.appearance == 0.0 and stranger.face is None
    # candidate: clip image 0.85 -> 0.75, osnet 0.70 -> 0.75, description text ~0 -> 0; (0.3+0.3)/1.0 = 0.6
    cand = score_embeddings(prof, c, clip_image=at_cosine(prof.clip_image[1], 0.85), osnet=at_cosine(prof.osnet[0], 0.70))
    assert cand.level == P.ID_APPEARANCE and 0.5 <= cand.appearance < 0.8
    strong = score_embeddings(prof, c, clip_image=at_cosine(prof.clip_image[0], 0.95), osnet=at_cosine(prof.osnet[1], 0.85))
    assert strong.level == P.ID_STRONG and strong.appearance >= 0.8
    face = score_embeddings(prof, c, clip_image=unit(), osnet=unit(), face=at_cosine(prof.face[0], 0.55),
                            face_width_px=48)
    assert face.level == P.ID_FACE and face.face == pytest.approx(0.55, abs=1e-5)


def test_face_needs_40px_and_threshold(prof, cfg):
    c = cfg["identity"]
    f = at_cosine(prof.face[1], 0.6)
    small = score_embeddings(prof, c, face=f, face_width_px=39.9)
    assert small.face is None and small.level == P.ID_NONE            # too small: not even scored
    ok = score_embeddings(prof, c, face=f, face_width_px=40)
    assert ok.level == P.ID_FACE
    other = score_embeddings(prof, c, face=at_cosine(prof.face[1], 0.2), face_width_px=120)
    assert other.face == pytest.approx(0.2, abs=1e-5) and other.level == P.ID_NONE


def test_missing_modalities_renormalise(prof, cfg):
    c = cfg["identity"]
    only_osnet = score_embeddings(prof, c, osnet=at_cosine(prof.osnet[0], 0.80))
    assert only_osnet.appearance == pytest.approx(1.0) and only_osnet.level == P.ID_STRONG
    empty = TargetProfile(osnet=prof.osnet)                           # profile without CLIP or faces
    r = score_embeddings(empty, c, clip_image=unit(), osnet=at_cosine(prof.osnet[0], 0.6), face=unit(), face_width_px=90)
    assert r.cosines["clip_image"] is None and r.face is None and r.appearance == pytest.approx(0.5)


def test_thresholds_come_from_config(prof, cfg):
    c = dict(cfg["identity"], face=dict(cfg["identity"]["face"], verify=0.7))
    r = score_embeddings(prof, c, face=at_cosine(prof.face[0], 0.6), face_width_px=60)
    assert r.level == P.ID_NONE
    assert cfg["identity"]["face"]["min_width_px"] == 40


def test_config_matches_code_defaults(cfg):
    ident = {k: v for k, v in cfg["identity"].items() if k != "models"}
    assert ident == DEFAULTS


def test_matcher_holds_best_level_and_throttles(prof, cfg):
    now = [0.0]

    class FakeEmb:
        calls = 0

        def embed_person(self, crop):
            FakeEmb.calls += 1
            seq = [dict(face=at_cosine(prof.face[0], 0.6), face_width_px=50),     # face verified
                   dict(clip_image=unit(), osnet=unit())]                          # then looks away
            return seq[min(FakeEmb.calls - 1, 1)]

    m = IdentityMatcher(prof, FakeEmb(), dict(cfg["identity"], every_s=1.0, hold_s=30.0), clock=lambda: now[0])
    frame = np.zeros((720, 1280, 3), np.uint8)
    box = [600, 200, 680, 500]
    assert m.update(5, frame, box)[0] == P.ID_FACE
    now[0] = 0.5
    assert m.update(5, frame, box)[0] == P.ID_FACE and FakeEmb.calls == 1       # throttled
    now[0] = 2.0
    assert m.update(5, frame, box)[0] == P.ID_FACE and FakeEmb.calls == 2       # held although face lost
    now[0] = 40.0
    assert m.update(5, frame, box)[0] == P.ID_NONE                               # hold expired
    assert m.update(9, frame, [0, 0, 10, 10])[0] == P.ID_NONE                    # crop too small: no embedding
    assert m.held(123).level == P.ID_NONE


def test_crop_box():
    f = np.arange(100 * 200 * 3, dtype=np.uint8).reshape(100, 200, 3)
    c = crop_box(f, [50, 20, 150, 80], pad=0.0)
    assert c.shape == (60, 100, 3)
    assert crop_box(f, [190, 90, 400, 300], pad=0.0).shape[:2] == (10, 10)      # clipped to the image
    assert crop_box(f, [50, 20, 150, 80]).shape[:2] == (66, 110)                 # 5 % padding each side
    assert crop_box(f, [300, 300, 400, 400]) is None


def test_missing_models_are_logged_never_downloaded(tmp_path, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("tried to open a network connection")
    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    logs = []
    e = make_runtime_embedders({"models_dir": str(tmp_path)}, log=logs.append)
    assert e.clip is None and e.osnet is None and e.face is None
    assert len(logs) == 3 and all("off" in s for s in logs)
    assert e.embed_person(np.zeros((128, 64, 3), np.uint8)) == {}


def test_clip_hailo_text_one_prompt_per_call_and_releases_image(monkeypatch):
    """Text HEF has batch 1 and opens its own device: one call per prompt, image network closed first."""
    import sys
    import types
    import numpy as np
    from eagle.identity import ClipHailo
    calls, opened, closed = [], [], []

    class FakeInfer:
        def __init__(self, hef, **kw):
            opened.append(hef)

        def get_input_shape(self):
            return (224, 224, 3)

        def close(self):
            closed.append(1)

    def fake_text(t, **kw):
        assert isinstance(t, str)
        calls.append(t)
        return np.ones((1, 512)) * (len(calls))

    mods = {"hailo_apps.python.core.common.hailo_inference": types.SimpleNamespace(HailoInfer=FakeInfer),
            "hailo_apps.python.pipeline_apps.clip.clip_text_utils":
                types.SimpleNamespace(run_text_encoder_inference=fake_text)}
    for name, m in mods.items():
        monkeypatch.setitem(sys.modules, name, m)
    c = ClipHailo("img.hef", text_hef="txt.hef")
    out = c.text_embeddings(["a", "b", "c"])
    assert calls == ["a", "b", "c"] and out.shape == (3, 512)
    assert np.allclose(np.linalg.norm(out, axis=1), 1.0)
    assert closed == [1] and c.image is None                      # released before the text encoder ran
    c._open_image()
    assert opened == ["img.hef", "img.hef"]
    c.close()
    c.close()                                                     # idempotent
    assert closed == [1, 1]


def test_face_only_mode_never_opens_clip_and_still_verifies(monkeypatch, tmp_path):
    import numpy as np
    import eagle.identity as ident
    from eagle.identity import TargetProfile, l2n, score_embeddings

    def boom(*a, **k):
        raise AssertionError("ClipHailo must not be constructed with clip=False")
    monkeypatch.setattr(ident, "ClipHailo", boom)
    logs = []
    e = ident.make_runtime_embedders({"models_dir": str(tmp_path)}, clip=False, log=logs.append)
    assert e.clip is None and any("CLIP off" in m and "face-only" in m for m in logs)
    face = l2n(np.random.default_rng(5).normal(size=512))[0]
    prof = TargetProfile(face[None], l2n(np.ones((2, 512))), l2n(np.ones((1, 512))), np.zeros((0, 512), np.float32))
    r = score_embeddings(prof, {}, face=face, face_width_px=60)        # no clip_image / osnet observed
    assert r.level == 3
