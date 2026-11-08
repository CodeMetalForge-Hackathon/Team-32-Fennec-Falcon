"""pi/field/demo.py: the demo banner for each scene the recording must show."""
import importlib.util
import json
import time

import pytest


@pytest.fixture(scope="module")
def demo(root):
    spec = importlib.util.spec_from_file_location("eagle_demo", root.parent / "pi/field/demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dirs(tmp_path, vision=None, events=()):
    logs, eagle = tmp_path / "field_logs", tmp_path / "eagle"
    (logs / "run1").mkdir(parents=True)
    eagle.mkdir()
    (logs / "latest").symlink_to("run1")
    now = time.time()
    with open(logs / "run1" / "events.jsonl", "w") as f:
        for e in events:
            f.write(json.dumps({"t": now - e.pop("age", 0.5), **e}) + "\n")
    if vision is not None:
        with open(eagle / "vision.jsonl", "w") as f:
            f.write(json.dumps({"t": now - 0.3, **vision}) + "\n")
    return logs, eagle


def test_target_found_with_heat_and_voice(demo, tmp_path):
    logs, eagle = _dirs(tmp_path, {"boxes": [[0, 0, 10, 20, 0.9]], "identity": [[3, 0.6, 0.55]]},
                        [{"event": "detection", "kind": "HEAT", "temp_c": 34.2},
                         {"event": "detection", "kind": "SOUND", "category": "VOICE"}])
    s = demo.state(logs, eagle, demo.Verdict())
    v = s["verdict"]
    assert v["key"] == "target" and v["title"] == "TARGET FOUND: MITCH"
    assert "face match 0.55" in v["sub"] and "34.2" in v["sub"] and "voice" in v["sub"]
    assert all(s["chips"][k]["on"] for k in ("heat", "sound", "vision", "face"))


def test_other_person_is_not_the_target(demo, tmp_path):
    logs, eagle = _dirs(tmp_path, {"boxes": [[0, 0, 10, 20, 0.9]], "identity": [[0, 0.2, 0.08]]},
                        [{"event": "detection", "kind": "HEAT", "temp_c": 33.0}])
    v = demo.state(logs, eagle, demo.Verdict())["verdict"]
    assert v["key"] == "not_target" and "NOT MITCH" in v["title"]


def test_far_person_without_face_is_unidentified(demo, tmp_path):
    logs, eagle = _dirs(tmp_path, {"boxes": [[0, 0, 10, 20, 0.9]], "identity": [[0, 0.1, None]]})
    assert demo.state(logs, eagle, demo.Verdict())["verdict"]["key"] == "human"


def test_heat_or_sound_alone_is_a_human_detection(demo, tmp_path):
    logs, eagle = _dirs(tmp_path, None, [{"event": "detection", "kind": "SOUND", "category": "DISTRESS"}])
    v = demo.state(logs, eagle, demo.Verdict())["verdict"]
    assert v["key"] == "human" and "shout" in v["sub"]


def test_empty_scene(demo, tmp_path):
    logs, eagle = _dirs(tmp_path, {"boxes": [], "identity": []},
                        [{"event": "detection", "kind": "HEAT", "temp_c": 34.0, "age": 60}])
    v = demo.state(logs, eagle, demo.Verdict())["verdict"]
    assert v["key"] == "none" and v["title"] == "NO HUMAN DETECTED"


def test_target_banner_holds_briefly_then_drops(demo, tmp_path):
    verdict = demo.Verdict()
    vis_t = {"people": 1, "best": {"level": 3, "appearance": 0.6, "face": 0.5, "t": 0}, "face_not_target": None}
    vis_0 = {"people": 0, "best": None, "face_not_target": None}
    life_0 = {"heat_c": None, "sounds": [], "tracks": 0}
    assert verdict.decide(vis_t, life_0, 100.0)["key"] == "target"
    assert verdict.decide(vis_0, life_0, 102.0)["key"] == "target"          # held through a turned head
    assert verdict.decide(vis_0, life_0, 104.0)["key"] == "none"
    assert [l["key"] for l in verdict.log] == ["none", "target"]
