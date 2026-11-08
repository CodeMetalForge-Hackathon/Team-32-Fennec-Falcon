"""apps/hailo_vision.py helpers (no Hailo needed: the hailo imports live inside main())."""
import importlib.util
import json

import pytest

from eagle.camgeo import Camera
from eagle.vision import VisionJsonReader, boxes_to_detections, parse_boxes_line


@pytest.fixture(scope="module")
def hv(root):
    spec = importlib.util.spec_from_file_location("hailo_vision", root / "apps/hailo_vision.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bbox_to_pixels_clips_normalized_boxes(hv):
    assert hv.bbox_to_pixels(0.25, 0.5, 0.25, 0.25, 1280, 720) == [320.0, 360.0, 640.0, 540.0]
    assert hv.bbox_to_pixels(-0.05, 0.9, 0.2, 0.3, 1280, 720) == [0.0, 648.0, 192.0, 720.0]


def test_vision_line_is_read_by_eagle(hv, tmp_path, cfg):
    line = hv.vision_line(12.5, [[600.0, 200.0, 680.0, 500.0, 0.91]], 1280, 720, ids=[4],
                          identity=[(2, 0.71, None)])
    d = json.loads(line)
    assert d["ids"] == [4] and d["identity"] == [[2, 0.71, None]]
    t, boxes, size = parse_boxes_line(line)
    assert t == 12.5 and size == (1280, 720) and boxes == [[600.0, 200.0, 680.0, 500.0, 0.91]]
    w = hv.RateLimitedWriter(str(tmp_path / "v.jsonl"), rate_hz=0)
    w.write(line, has_boxes=True)
    w.close()
    got = VisionJsonReader(str(tmp_path / "v.jsonl"), from_start=True).read()
    cam = Camera.from_config(dict(cfg["life"]["vision"]["camera"], mount={"enu_m": [0, 0, 3], "tilt_down_deg": 30}))
    dets = boxes_to_detections(got[0][1], cam, 1.0, size=got[0][2])
    assert len(dets) == 1 and dets[0].kind == "PERSON" and dets[0].xy[1] > 0


def test_rate_limit_and_heartbeat(hv, tmp_path):
    now = [0.0]
    w = hv.RateLimitedWriter(str(tmp_path / "v.jsonl"), rate_hz=2.0, heartbeat_s=5.0, clock=lambda: now[0])
    wrote = []
    for k in range(40):                                     # 10 s at 4 frames/s: boxes in the first half
        now[0] = k * 0.25
        wrote.append(w.write("x", has_boxes=k < 20))
    w.close()
    assert sum(wrote[:20]) == 10                             # 2 per second while people are in view
    assert sum(wrote[20:]) == 1                              # then an empty heartbeat every 5 s


def test_split_args_defaults_to_pi_camera(hv):
    ours, rest = hv.split_args(["--jsonl", "/tmp/v.jsonl", "--arch", "hailo8l"])
    assert ours.jsonl == "/tmp/v.jsonl" and rest == ["--input", "rpi", "--arch", "hailo8l"]
    ours, rest = hv.split_args(["--input", "clip.mp4", "--rate", "5"])
    assert ours.rate == 5.0 and rest == ["--input", "clip.mp4"] and ours.tuning_file == "imx708_wide_noir.json"


def test_find_tuning_file(hv, tmp_path):
    pisp = tmp_path / "pisp"
    pisp.mkdir()
    (pisp / "imx708_wide_noir.json").write_text("{}")
    assert hv.find_tuning_file("imx708_wide_noir.json", dirs=(str(tmp_path / "vc4"), str(pisp))) == \
        str(pisp / "imx708_wide_noir.json")
    assert hv.find_tuning_file("imx708_wide_noir.json", dirs=(str(tmp_path / "vc4"),)) is None
    assert hv.find_tuning_file("none") is None


def test_vision_camera_is_camera_module_3_wide(cfg):
    c = cfg["life"]["vision"]["camera"]
    assert (c["hfov_deg"], c["vfov_deg"], c["width"], c["height"]) == (102.0, 67.0, 1280, 720)
    cam = Camera.from_config(dict(c, mount=dict(c["mount"], enu_m=[0, 0, 3])))
    assert cam.hfov_deg == pytest.approx(102.0, abs=0.1) and cam.vfov_deg == pytest.approx(67.0, abs=0.1)
