"""End to end on the bench: eagle_runtime -> gate -> one sealed FIX (two bursts) over UDP -> the
Nest decrypts it; training and key guards; the thermal model hook; record + replay."""
import argparse
import importlib.util
import json
import socket

import numpy as np
import pytest

from eagle import protocol as P
from eagle.config import deep_merge, load_keys
from eagle.infer import Box
from eagle.link import UdpTransport


def _load_app(root, name):
    spec = importlib.util.spec_from_file_location(name, root / "apps" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def rt_mod(root):
    return _load_app(root, "eagle_runtime")


def _port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _args(tmp_path, **kw):
    a = dict(config=None, mode=None, modules=None, thermal=None, vitals_port=None, transport=None, port=None,
             no_radio=False, allow_test_keys=False, require=None, sim=None, speed=0.0, record=None,
             duration=None, step=1.0)
    a.update(kw)
    return argparse.Namespace(**a)


def _cfg(cfg, tmp_path, transport="udp", **over):
    pn, pq = _port(), _port()
    c = deep_merge(cfg, {"eagle": {"state_file": str(tmp_path / "ctr.json")},
                         "radio": {"transport": transport,
                                   "udp": {"bind": f"127.0.0.1:{pn}", "peer": f"127.0.0.1:{pq}"}},
                         "decide": {"log": {"path": str(tmp_path / "decisions.jsonl")},
                                    "emcon": {"relocated_flag": None}}})
    return deep_merge(c, over), (pn, pq)


def _run(rt_mod, c, args, scene):
    sim, dur, _, _ = rt_mod.make_sim(scene, c["perch"]["enu_m"][:2])
    rt = rt_mod.EagleRuntime(c, args, sim=sim)
    rt.sim_story = rt.sim_expected = ""
    rt.run_sim(dur, 1.0, speed=0)
    return rt


def _frames(nest_rx):
    out = []
    while True:
        f = nest_rx.recv(timeout=0.3)
        if f is None:
            return out
        out.append(f.payload)


def _log(tmp_path):
    p = tmp_path / "decisions.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_night_scene_sends_one_fix_as_two_identical_bursts_and_the_nest_reads_it(rt_mod, cfg, tmp_path):
    c, (pn, pq) = _cfg(cfg, tmp_path)
    nest_rx = UdpTransport(f"127.0.0.1:{pq}", f"127.0.0.1:{pn}")
    try:
        rt = _run(rt_mod, c, _args(tmp_path), "night")
        frames = _frames(nest_rx)
    finally:
        nest_rx.close()
    assert len(frames) == 2 and frames[0] == frames[1]                 # the same sealed frame twice
    assert frames[0][0] == P.MSG_FIX and len(frames[0]) == P.FIX_LEN == 48
    nest = P.NestSession(load_keys(cfg)["fix_aes"], None, max_age_s=600)
    rep = nest.decrypt_fix(frames[0])
    with pytest.raises(P.ProtocolError):                                # the copy is dropped as a replay
        nest.decrypt_fix(frames[1])
    b = rep.body
    assert b.confidence >= 0.98 and b.status & P.ST_INJURED
    assert P.class_name(b.cls) == "TAP+SIM"
    e, n, _ = rt.frame.to_enu(b.lat, b.lon, None)
    assert np.hypot(e - 12.0, n - 5.0) < 0.5                          # he lies at E 12 N 5 from the perch
    assert 9.5 <= b.ce_m <= 12.0                                        # own position error dominates
    sends = [r for r in _log(tmp_path) if r.get("event") == "SEND"]
    assert [r.get("burst") for r in sends] == [1, 2]


def test_patrol_never_transmits(rt_mod, cfg, tmp_path):
    c, (pn, pq) = _cfg(cfg, tmp_path)
    nest_rx = UdpTransport(f"127.0.0.1:{pq}", f"127.0.0.1:{pn}")
    try:
        _run(rt_mod, c, _args(tmp_path), "patrol")
        frames = _frames(nest_rx)
    finally:
        nest_rx.close()
    assert frames == []
    assert not any(r.get("event") == "SEND" for r in _log(tmp_path))


def test_training_mode_mutes_a_real_radio(rt_mod, cfg, tmp_path, capsys):
    c, _ = _cfg(cfg, tmp_path, transport="serial", radio={"serial_port": "/dev/does-not-exist"})
    rt = _run(rt_mod, c, _args(tmp_path, mode="training"), "night")
    assert rt.radio is None
    sends = [r for r in _log(tmp_path) if r.get("event") == "SEND"]
    assert sends and all(r.get("muted") for r in sends)
    assert "training mode: RF muted" in capsys.readouterr().out


def test_training_mode_over_udp_reaches_the_bench_nest(rt_mod, cfg, tmp_path):
    c, (pn, pq) = _cfg(cfg, tmp_path)
    nest_rx = UdpTransport(f"127.0.0.1:{pq}", f"127.0.0.1:{pn}")
    try:
        _run(rt_mod, c, _args(tmp_path, mode="training"), "night")
        frames = _frames(nest_rx)
    finally:
        nest_rx.close()
    rep = P.NestSession(load_keys(cfg)["fix_aes"], None, max_age_s=600).decrypt_fix(frames[0])
    assert rep.body.cls & P.CLS_SIM


def test_refuses_the_public_test_keys_on_a_real_radio(rt_mod, cfg, tmp_path):
    c, _ = _cfg(cfg, tmp_path, transport="serial", radio={"serial_port": "/dev/does-not-exist"})
    with pytest.raises(SystemExit, match="TEST keys"):
        rt_mod.EagleRuntime(c, _args(tmp_path))
    rt = rt_mod.EagleRuntime(c, _args(tmp_path, allow_test_keys=True))   # bench override: radio just fails to open
    rt.close()


def test_thermal_model_scores_the_overlapping_blob_and_adds_unmatched_boxes(rt_mod, cfg, tmp_path):
    c, _ = _cfg(cfg, tmp_path, life={"thermal": {"source": "synthetic", "model": {"enabled": False}}})
    rt = rt_mod.EagleRuntime(c, _args(tmp_path, no_radio=True, modules=["thermal"]))
    cam = rt.mods["thermal"][1].cam

    class Stub:
        describe = "stub"

        def due(self):
            return True

        def __call__(self, frame):
            return [Box(0, 0, cam.width, cam.height, 0.85, 0), Box(0, cam.height - 20, 10, cam.height, 0.6, 0)]

    rt.tmodel = Stub()
    dets = rt.step_thermal(100.0)
    heat = [d for d in dets if d.kind == "HEAT"]
    person = [d for d in dets if d.kind == "PERSON"]
    assert heat and heat[0].shape == pytest.approx(0.85)
    assert person and person[0].source.startswith("thermal-model") and person[0].shape == pytest.approx(0.6)
    rt.close()


def _events(run_dir):
    return [json.loads(x) for x in (run_dir / "events.jsonl").read_text().splitlines()]


def test_record_then_replay_reproduces_the_send_and_a_weaker_code_does_not(rt_mod, root, cfg, tmp_path):
    c, _ = _cfg(cfg, tmp_path)
    rt = _run(rt_mod, c, _args(tmp_path, no_radio=True, record=str(tmp_path / "rec")), "night")
    ev = _events(rt.rec.dir)                                        # eagle/record.py's events.jsonl
    first = next(e for e in ev if e["event"] == "decision" and e.get("kind") == "SEND")
    assert any(e["event"] == "detection" and e.get("sensor") == "thermal" and e.get("shape") == 0.8 for e in ev)
    assert any(e["event"] == "life_list" for e in ev)              # the dashboard's life list keeps working
    replay = _load_app(root, "replay_decisions")
    batches = replay.load_batches(rt.rec.dir)
    assert batches and {s for _, s, _ in batches} == {"thermal", "audio"}
    res = replay.replay(batches, c, log=lambda *_: None)
    assert len(res["sends"]) == 1 and res["sends"][0]["subject"] == "track:1"
    assert abs(res["sends"][0]["t_s"] - (first["t"] - batches[0][0])) <= 1.0
    weak = replay.replay(batches, deep_merge(c, replay.dotted(["decide.lr.code=10"])), log=lambda *_: None)
    assert weak["sends"] == [] and 0.85 < weak["subjects"]["track:1"] < 0.95


def test_replay_reads_a_life_node_recording(root, cfg, tmp_path):
    """Recordings life_node already made on the Pi (no sensor key, one mixed batch per step) replay too."""
    from eagle.decide import det_to_json
    from eagle.scenes import make_scene
    scene, dur, _, _ = make_scene("night", (0.0, 0.0))
    run = tmp_path / "life_run"
    run.mkdir()
    with (run / "events.jsonl").open("w") as f:
        f.write(json.dumps({"t": 0.0, "event": "run_start", "modules": ["thermal", "audio"]}) + "\n")
        for t in range(0, 200, 2):
            for _, dets in scene(float(t)):
                for d in dets:
                    f.write(json.dumps({"event": "detection", **det_to_json(d)}) + "\n")
    replay = _load_app(root, "replay_decisions")
    res = replay.replay(replay.load_batches(run), cfg, log=lambda *_: None)
    assert [s["subject"] for s in res["sends"]] == ["track:1"]


def test_record_keeps_thermal_frames_and_exports_calibration_images(rt_mod, root, cfg, tmp_path):
    pytest.importorskip("cv2")
    c, _ = _cfg(cfg, tmp_path, life={"thermal": {"source": "synthetic", "model": {"enabled": False}}})
    rt = rt_mod.EagleRuntime(c, _args(tmp_path, no_radio=True, modules=["thermal"], record=str(tmp_path / "rec")))
    for k in range(5):
        rt.step(1000.0 + 2.0 * k)
    rt.close()
    files = sorted(rt.rec.dir.glob("thermal_*.npz"))
    assert files
    z = np.load(files[0])
    assert z["centi_c"].shape[0] == 5 and z["centi_c"].shape[1:] == (120, 160) and len(z["t"]) == 5
    out = tmp_path / "calib"
    assert _load_app(root, "export_thermal_calib").main([str(rt.rec.dir), "--out", str(out), "--max", "3"]) == 0
    assert len(list(out.glob("*.png"))) == 3


def test_field_dashboard_shows_the_gate(rt_mod, root, cfg, tmp_path):
    c, _ = _cfg(cfg, tmp_path)
    logs = tmp_path / "field_logs"
    _run(rt_mod, c, _args(tmp_path, no_radio=True, record=str(logs)), "night")
    spec = importlib.util.spec_from_file_location("dashboard", root.parent / "pi" / "field" / "dashboard.py")
    dash = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dash)
    st = dash.status(logs, dash.Runner(logs))
    assert st["gate"] and "track:1" in st["gate"] and "REPORTED" in st["gate"] and "SENT" in st["gate"]
    assert "(muted)" in st["gate"] and st["life_list"]
    assert dash.node_unit() == "eagle-life"                # eagle-runtime.service not installed here
