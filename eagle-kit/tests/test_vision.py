"""Person boxes -> floor positions."""
import numpy as np

from eagle.camgeo import Camera
from eagle.vision import VisionJsonReader, box_to_ground, boxes_to_detections, parse_boxes_line


def test_upright_and_lying_boxes():
    cam = Camera.from_fov(66, 640, 480, position=(0, 0, 3), heading_deg=0, tilt_down_deg=30)
    feet, head = np.array([1.0, 6.0, 0.0]), np.array([1.0, 6.0, 1.7])
    (uf, vf, _), (uh, vh, _) = cam.project(feet), cam.project(head)
    xy, cov, used = box_to_ground([uf - 25, vh, uf + 25, vf, 0.9], cam)
    assert used == "upright" and np.linalg.norm(xy - feet[:2]) < 0.05
    c = np.array([-2.0, 5.0, 0.15])
    a, b = cam.project(c + [-0.85, 0, 0]), cam.project(c + [0.85, 0, 0])
    uc, vc, _ = cam.project(c)
    xy, cov, used = box_to_ground([a[0], vc - 8, b[0], vc + 8, 0.8], cam)
    assert used == "lying" and np.linalg.norm(xy - c[:2]) < 0.1 and np.all(np.linalg.eigvalsh(cov) > 0)
    dets = boxes_to_detections([[uf - 25, vh, uf + 25, vf, 0.9], [0, 0, 5, 5, 0.1]], cam, 3.0)
    assert len(dets) == 1 and dets[0].kind == "PERSON"
    half = boxes_to_detections([[(uf - 25) / 2, vh / 2, (uf + 25) / 2, vf / 2, 0.9]], cam, size=(320, 240))
    assert np.linalg.norm(half[0].xy - feet[:2]) < 0.1


def test_json_lines_tail(tmp_path):
    p = tmp_path / "boxes.jsonl"
    p.write_text('{"t": 1, "boxes": [[1, 2, 3, 4, 0.9]]}\n')
    rd = VisionJsonReader(str(p), from_start=True)
    assert len(rd.read()) == 1 and rd.read() == []
    with open(p, "a") as f:
        f.write('not json\n{"t": 2, "boxes": [], "w": 320, "h": 240}\n{"t": 3, "bo')
    got = rd.read()
    assert len(got) == 1 and got[0][2] == (320, 240)
    assert parse_boxes_line('{"t": 5, "boxes": [[0, 0, 10, 20]]}')[1] == [[0.0, 0.0, 10.0, 20.0, 1.0]]
