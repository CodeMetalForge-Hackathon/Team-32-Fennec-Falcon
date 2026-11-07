"""Thermal detector on synthetic frames: counts, positions, size gate, sources."""
import sys

import numpy as np
import pytest

from eagle.camgeo import Camera
from eagle.lifesim import SimPerson, _capsule_gap
from eagle.thermal import (BlobTracker, MLX90640Source, SyntheticThermal, ThermalDetector, ThermalSourceError,
                           hot_spot, label_components, person, warm_decoy)


def cam160():
    return Camera.from_fov(57, 160, 120, position=(0, 0, 3), heading_deg=0, tilt_down_deg=35)


def place(n, rng, cam, r=(3.0, 10.0), gap=1.2):
    """n non-touching lying / curled bodies, fully in view, 3-10 m from the perch."""
    out = []
    while len(out) < n:
        rr, a = rng.uniform(*r), np.radians(90 + rng.uniform(-30, 30))
        p = SimPerson("x", (rr * np.cos(a), rr * np.sin(a)), "unconscious", str(rng.choice(["lying", "curled"])),
                      float(rng.uniform(0, 180)), float(rng.uniform(29.5, 33)))
        u, v, f = cam.project(np.array([[p.xy[0], p.xy[1], 0.1]]))
        if f.all() and cam.in_image(u, v, margin=12).all() and all(_capsule_gap(p, q) >= gap for q in out):
            out.append(p)
    return out


@pytest.mark.parametrize("n", range(1, 11))
def test_counts_and_positions(n):
    cam = cam160()
    rng = np.random.default_rng(100 + n)
    ppl = place(n, rng, cam)
    bodies = [person(p.xy, p.posture, p.yaw_deg, p.temp_c) for p in ppl]
    bodies.append(hot_spot((5.5, 7.0)))                        # a hot mug in view
    frame = SyntheticThermal(cam, bodies, noise_c=0.2, seed=n).render()
    det = ThermalDetector(cam)
    blobs = det.detect(frame)
    assert len(blobs) == n
    for p in ppl:
        d = min(np.linalg.norm(b.xy - np.asarray(p.xy)) for b in blobs)
        assert d < 0.5, (p, d)
    for b in blobs:
        assert np.all(np.linalg.eigvalsh(b.cov) > 0)


def test_small_hot_spots_rejected_decoy_kept():
    cam = cam160()
    frame = SyntheticThermal(cam, [hot_spot((0.5, 5.0)), hot_spot((-1.5, 7.0), temp_c=36.0, radius=0.1),
                                   warm_decoy((1.5, 8.0))], noise_c=0.2).render()
    det = ThermalDetector(cam)
    blobs = det.detect(frame)
    assert len(blobs) == 1 and np.linalg.norm(blobs[0].xy - [1.5, 8.0]) < 0.5      # the jacket looks human
    assert any("too small" in r.reason for r in det.rejected)


def test_low_resolution_mlx_like_sensor():
    cam = Camera.from_fov(55, 32, 24, position=(0, 0, 3), heading_deg=0, tilt_down_deg=35)
    det = ThermalDetector(cam)
    for r in (3.0, 5.0, 7.0):
        blobs = det.detect(SyntheticThermal(cam, [person((0.3, r), "lying", 20)], noise_c=0.3, seed=1).render())
        assert len(blobs) == 1 and np.linalg.norm(blobs[0].xy - [0.3, r]) < 0.7


def test_sitting_person_bias_is_covered_by_covariance():
    cam = cam160()
    b = ThermalDetector(cam).detect(SyntheticThermal(cam, [person((0.5, 6.0), "sitting")]).render())
    assert len(b) == 1
    err = b[0].xy - [0.5, 6.0]
    assert np.linalg.norm(err) < 1.2
    assert err @ np.linalg.solve(b[0].cov, err) < 9.21                 # inside the 99 % gate


def test_label_components_numpy_fallback(monkeypatch):
    mask = np.zeros((20, 30), bool)
    mask[2:5, 2:6] = True
    mask[10:12, 20:29] = True
    mask[5, 6] = True                                   # diagonal neighbour joins the first blob
    monkeypatch.setitem(sys.modules, "scipy", None)     # force the pure-numpy path
    lab, n = label_components(mask)
    assert n == 2 and lab[5, 6] == lab[2, 2] != lab[10, 20]


def test_tracker_keeps_ids():
    cam = cam160()
    sim = SyntheticThermal(cam, [person((1, 5)), person((-2, 8))], noise_c=0.2)
    det, trk = ThermalDetector(cam), BlobTracker()
    ids = [sorted(b.track_id for b in trk.update(det.detect(sim.read()[1]))) for _ in range(3)]
    assert ids[0] == ids[1] == ids[2] == [1, 2]


def test_hardware_source_errors_are_clear():
    try:
        import adafruit_mlx90640  # noqa: F401
        pytest.skip("adafruit_mlx90640 installed")
    except ImportError:
        pass
    with pytest.raises(ThermalSourceError, match="pip install adafruit-circuitpython-mlx90640"):
        MLX90640Source()
