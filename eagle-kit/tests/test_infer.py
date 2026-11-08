"""Thermal person detector: letterbox, both decoders, runtime fallback and the real ONNX file."""
import numpy as np
import pytest

from eagle import infer as I


def test_letterbox_and_unletterbox_round_trip():
    img = np.zeros((192, 256), np.uint8)
    canvas, s, pad = I.letterbox(img, 320)
    assert canvas.shape == (320, 320, 3) and s == pytest.approx(1.25) and pad == (0, 40)
    assert (canvas[:40] == 114).all() and (canvas[40:280] == 0).all()
    back = I._unletterbox(np.array([[25.0, 65.0, 125.0, 165.0]]), s, pad, (256, 192))
    assert back[0] == pytest.approx([20.0, 20.0, 100.0, 100.0])


def test_thermal_to_gray_is_white_hot_with_a_minimum_span():
    g = I.thermal_to_gray(np.array([[20.0, 30.0]]))
    assert g.tolist() == [[0, 255]]
    flat = I.thermal_to_gray(np.array([[20.0, 20.2]]), min_span_c=0.5)
    assert flat.max() < 120                                  # a flat scene is not stretched to full white


def _raw(boxes, nc=4, n=50):
    """YOLOv8 head tensor 1 x (4+nc) x n with the given (cx, cy, w, h, cls, score) rows."""
    out = np.zeros((1, 4 + nc, n), np.float32)
    for i, (cx, cy, w, h, c, s) in enumerate(boxes):
        out[0, :4, i] = (cx, cy, w, h)
        out[0, 4 + c, i] = s
    return out


def test_decode_yolov8_nms_per_class_and_unletterbox():
    raw = _raw([(100, 140, 20, 40, 0, 0.9), (102, 141, 20, 40, 0, 0.6),   # same person twice
                (101, 140, 20, 40, 1, 0.7),                             # a "car" on top: other class
                (250, 200, 30, 30, 0, 0.2)])                            # below threshold
    boxes = I.decode_yolov8(raw, 1.25, (0, 40), (256, 192), conf=0.3, iou=0.5)
    assert [(b.cls, round(b.score, 1)) for b in boxes] == [(0, 0.9), (1, 0.7)]
    b = boxes[0]
    assert [round(v, 1) for v in b.xyxy] == [72.0, 64.0, 88.0, 96.0]


def test_decode_hailo_nms_matches_raw_decoder():
    per_class = [np.array([[100 / 320, 90 / 320, 140 / 320, 110 / 320, 0.8]]), np.zeros((0, 5)),
                 np.zeros((0, 5)), np.zeros((0, 5))]                    # y_min, x_min, y_max, x_max, score
    hb = I.decode_hailo_nms(per_class, 320, 1.25, (0, 40), (256, 192), conf=0.3)
    rb = I.decode_yolov8(_raw([(100, 120, 20, 40, 0, 0.8)]), 1.25, (0, 40), (256, 192), conf=0.3)
    assert len(hb) == len(rb) == 1
    assert hb[0].xyxy == pytest.approx(rb[0].xyxy, abs=1e-6) and hb[0].score == pytest.approx(0.8)


def test_hailo_is_skipped_without_a_hef_and_cpu_takes_over(tmp_path):
    pytest.importorskip("onnxruntime")
    if not __import__("pathlib").Path(I.DEFAULTS["onnx"]).exists():
        pytest.skip("fusion/models/thermal_person.onnx not present")
    logs = []
    m = I.ThermalPersonModel({"hef": str(tmp_path / "none.hef")}, log=logs.append)
    assert m.runtime.name.startswith("onnxruntime")
    assert any("hailo" in x and "none.hef" in x for x in logs)


def test_real_model_runs_and_finds_aerial_style_figures():
    """The committed model (HIT-UAV) on a crude aerial-style frame: three small warm figures."""
    pytest.importorskip("onnxruntime")
    if not __import__("pathlib").Path(I.DEFAULTS["onnx"]).exists():
        pytest.skip("fusion/models/thermal_person.onnx not present")
    rng = np.random.default_rng(0)
    img = 18 + rng.normal(0, 0.6, (192, 256))
    for y, x in [(60, 60), (100, 180), (150, 90)]:
        img[y - 6:y + 6, x - 2:x + 2] = 31
        img[y - 9:y - 6, x - 1:x + 1] = 33
        img[y + 6:y + 12, x - 2:x - 1] = 30
        img[y + 6:y + 12, x + 1:x + 2] = 30
    m = I.ThermalPersonModel({"prefer": ["onnxruntime"], "conf": 0.3}, log=lambda *_: None)
    boxes = m(img)
    assert boxes and all(b.cls == 0 for b in boxes)
    assert any(b.contains(60, 60, pad=3) for b in boxes)
    empty = m(18 + rng.normal(0, 0.6, (192, 256)))
    assert not empty


def test_onnxruntime_and_opencv_agree():
    pytest.importorskip("onnxruntime")
    pytest.importorskip("cv2")
    if not __import__("pathlib").Path(I.DEFAULTS["onnx"]).exists():
        pytest.skip("fusion/models/thermal_person.onnx not present")
    rng = np.random.default_rng(1)
    canvas = I.letterbox(I.thermal_to_gray(18 + rng.normal(0, 2, (192, 256))), 320)[0]
    a = I.OrtRuntime(I.DEFAULTS["onnx"])(canvas)[1]
    b = I.CvRuntime(I.DEFAULTS["onnx"])(canvas)[1]
    assert a.shape == b.shape == (1, 8, 2100)
    assert np.abs(a - b).max() < 1e-2
