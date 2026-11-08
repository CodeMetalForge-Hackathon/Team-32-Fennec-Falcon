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


# ---- HTI HT-203U (raw YUYV 256x392, kelvin = raw / 16) -----------------------------------------
from types import SimpleNamespace  # noqa: E402

import eagle.thermal as T  # noqa: E402


def hti_buffer(temps_c, preview_byte=0x80):
    """Synthetic HT-203U frame: (192, 256) deg C -> 200704 raw bytes, with junk in rows 192-391."""
    raw = np.full((T.HTI_HEIGHT, T.HTI_WIDTH), preview_byte * 257, dtype="<u2")     # preview/metadata rows
    raw[:T.HTI_SENSOR_ROWS] = np.round((np.asarray(temps_c) + 273.15) * T.HTI_COUNTS_PER_KELVIN).astype("<u2")
    return raw.tobytes()


def test_hti_decode_synthetic_buffer():
    temps = np.full((192, 256), 20.0)
    temps[100:120, 50:60] = 33.0
    temps[0, 0], temps[191, 255] = -20.0, 120.0
    out = T.hti_frame_to_celsius(hti_buffer(temps))
    assert out.shape == (192, 256)
    assert np.allclose(out, temps, atol=1 / 16)              # 1 count = 1/16 K
    assert out[110, 55] == pytest.approx(33.0, abs=0.07) and out[0, 0] == pytest.approx(-20.0, abs=0.07)
    # little-endian check: 0x1234 counts in the first pixel
    b = bytearray(hti_buffer(temps))
    b[0:2] = (0x1234).to_bytes(2, "little")
    assert T.hti_frame_to_celsius(bytes(b))[0, 0] == pytest.approx(0x1234 / 16 - 273.15)
    assert T.hti_frame_to_celsius(np.frombuffer(bytes(b), np.uint8))[0, 0] == pytest.approx(0x1234 / 16 - 273.15)


def test_hti_rejects_converted_frames():
    with pytest.raises(ThermalSourceError, match="200704"):
        T.hti_frame_to_celsius(bytes(256 * 392 * 3))           # a BGR-converted frame


def test_hti_source_uses_driver_readers_and_detector_finds_person():
    temps = np.full((192, 256), 18.0) + np.random.default_rng(3).normal(0, 0.1, (192, 256))
    temps[120:132, 110:150] = 31.0                               # lying person, mid-frame
    frames = [hti_buffer(temps), None]
    calls = {}

    class Reader:
        def read(self, timeout=5.0):
            calls["timeout"] = timeout
            return frames.pop(0)

        def close(self):
            calls["closed"] = True

    drv = SimpleNamespace(open_camera=lambda dev, reader: calls.update(dev=dev, reader=reader) or Reader())
    src = T.HtiSource("/dev/video0", "ffmpeg", timeout_s=2.0, _driver_mod=drv)
    t, frame, radiometric = src.read()
    assert calls["dev"] == "/dev/video0" and calls["reader"] == "ffmpeg" and calls["timeout"] == 2.0
    assert radiometric and frame.shape == (192, 256)
    cam = Camera.from_fov(50, 256, 192, position=(0, 0, 3), heading_deg=0, tilt_down_deg=35)
    blobs = ThermalDetector(cam).detect(frame)
    assert len(blobs) == 1 and 29 < blobs[0].peak < 33
    with pytest.raises(ThermalSourceError, match="no frame"):
        src.read()
    src.close()
    assert calls["closed"]


def test_hti_source_not_found_and_factory(monkeypatch):
    drv = SimpleNamespace(open_camera=lambda dev, reader: None)
    with pytest.raises(ThermalSourceError, match="not found"):
        T.HtiSource(_driver_mod=drv)
    seen = {}

    def fake_loader(path=None):
        seen["path"] = path

        def open_camera(dev, reader):
            seen.update(d=dev, r=reader)
            return SimpleNamespace(read=lambda timeout=5: None, close=lambda: None)
        return SimpleNamespace(open_camera=open_camera)
    monkeypatch.setattr(T, "_load_hti_driver", fake_loader)
    src = T.make_thermal_source({"source": "hti", "hti": {"device": "/dev/video4", "reader": "opencv",
                                                          "driver": "/x/thermal_rtsp.py"}})
    assert isinstance(src, T.HtiSource) and seen == {"path": "/x/thermal_rtsp.py", "d": "/dev/video4", "r": "opencv"}


def test_hti_decode_matches_pi_driver(root):
    """eagle's decode and pi/thermal_rtsp.py's to_celsius agree (needs OpenCV for that module)."""
    pytest.importorskip("cv2")
    drv = T._load_hti_driver()
    temps = np.linspace(-10, 60, 192 * 256).reshape(192, 256)
    buf = hti_buffer(temps)
    assert drv.WIDTH == T.HTI_WIDTH and drv.HEIGHT == T.HTI_HEIGHT and drv.COUNTS_PER_KELVIN == T.HTI_COUNTS_PER_KELVIN
    assert np.allclose(drv.to_celsius(buf), T.hti_frame_to_celsius(buf), atol=1e-4)
