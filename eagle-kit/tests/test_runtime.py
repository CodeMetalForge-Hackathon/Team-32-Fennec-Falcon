"""End to end on the bench: eagle_runtime -> gate -> one sealed FIX (two bursts) over UDP -> the
Nest decrypts it; training and key guards; the thermal model hook; record + replay."""
import argparse
import importlib.util
import json
import socket
import time

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
                                    "emcon": {"relocated_flag": None}},
                         "fennec": {"mission_start": time.time() - 3600.0,      # within the 45-day minute counter
                                    "counter_file": str(tmp_path / "fennec-ctr.json"),
                                    "compromised_flag": str(tmp_path / "compromised")}})
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


def test_night_scene_sends_one_fennec_report_twice_and_the_nest_reads_it(rt_mod, cfg, tmp_path):
    from eagle import fennec as F
    from eagle.nest import NestStation
    c, (pn, pq) = _cfg(cfg, tmp_path)
    nest_rx = UdpTransport(f"127.0.0.1:{pq}", f"127.0.0.1:{pn}")
    try:
        rt = _run(rt_mod, c, _args(tmp_path), "night")
        frames = _frames(nest_rx)
    finally:
        nest_rx.close()
    assert len(frames) == 2 and frames[0] == frames[1] and len(frames[0]) == 15   # one sealed report, twice
    nest = NestStation(c, senders=[], log=lambda *_: None, sim=True)
    d, xmls = nest.handle(type("Rx", (), {"payload": frames[0], "rssi": -90.0, "snr": 5.0})())
    assert nest.handle(type("Rx", (), {"payload": frames[1], "rssi": -90.0, "snr": 5.0})()) is None   # replay
    assert d["type_name"] == "TEST"                                   # --sim marks every report as a test
    assert d["cue"] == F.CUE["several sensors"] and d["conf"] == 2   # thermal + briefed code; P >= 0.99
    assert d["state"] == F.ST_PATTERN | F.ST_INJURED and d["err95_m"] == 25
    e, n, _ = rt.frame.to_enu(d["lat"], d["lon"], None)
    assert np.hypot(e - 12.0, n - 5.0) < 2.0                          # 2.5 m steps
    assert b'ce="25' in xmls[0] and b"SAR TEST" in xmls[0]
    sends = [r for r in _log(tmp_path) if r.get("event") == "SEND"]
    assert [r.get("burst") for r in sends] == [1, 2] and sends[0]["path"] == "nest" and sends[0]["bytes"] == 15


def test_night_scene_with_the_48_byte_fix(rt_mod, cfg, tmp_path):
    c, (pn, pq) = _cfg(cfg, tmp_path, decide={"report": {"format": "fix48"}})
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
    assert np.hypot(e - 12.0, n - 5.0) < 0.5
    assert 9.5 <= b.ce_m <= 12.0


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
    from eagle.nest import NestStation
    nest = NestStation(c, senders=[], log=lambda *_: None, sim=True)
    got = [nest.handle(type("Rx", (), {"payload": f, "rssi": -90.0, "snr": 5.0})()) for f in frames]
    kinds = [g[0]["type_name"] for g in got if g]
    assert "TEST" in kinds and "STATUS" in kinds                      # training: STATUS every 15 min on the bench


def _step_until(rt, t0, t_end, until=lambda: False):
    t = t0
    while t <= t_end and not until():
        rt.step(t)
        t += 1.0
    return t


def test_a_send_error_never_stops_the_eagle_and_the_report_is_retried(rt_mod, cfg, tmp_path):
    c, _ = _cfg(cfg, tmp_path)
    sim, dur, _, _ = rt_mod.make_sim("night", c["perch"]["enu_m"][:2])
    rt = rt_mod.EagleRuntime(c, _args(tmp_path, no_radio=True), sim=sim)
    calls, real = [], rt.report

    def flaky(d, now):
        calls.append(now)
        if len(calls) == 1:
            raise OSError("radio unplugged")
        return real(d, now)

    rt.report = flaky
    try:
        _step_until(rt, 1000.0, 1000.0 + dur + 60.0)
    finally:
        rt.close()
    assert len(calls) == 2 and calls[1] - calls[0] >= rt_mod.TX_RETRY_S
    assert [r for r in _log(tmp_path) if r.get("event") == "SEND"]


@pytest.mark.parametrize("mode", ["default", "strict"])
def test_burst_2_waits_while_someone_could_hear_it_and_strict_drops_it(rt_mod, cfg, tmp_path, mode):
    c, (pn, pq) = _cfg(cfg, tmp_path, decide={"mode": mode})
    nest_rx = UdpTransport(f"127.0.0.1:{pq}", f"127.0.0.1:{pn}")
    sim, dur, _, _ = rt_mod.make_sim("night", c["perch"]["enu_m"][:2])
    rt = rt_mod.EagleRuntime(c, _args(tmp_path), sim=sim)
    try:
        t = _step_until(rt, 1000.0, 1000.0 + dur, until=lambda: bool(rt.pending))
        assert rt.pending                                              # burst 1 is out
        someone = {"who": "1 other person(s) in view within the 300 m intercept radius"}
        rt.engine.listeners_now = lambda subject: someone["who"]       # a stranger walks into view
        t = _step_until(rt, t, t + 200.0)
        assert len(_frames(nest_rx)) == 1
        if mode == "strict":
            assert not rt.pending and any(r.get("event") == "BURST2_DROPPED" for r in _log(tmp_path))
        else:
            assert rt.pending and rt.pending[0]["waiting"]
            someone["who"] = ""                                          # gone
            _step_until(rt, t, t + 20.0)
            assert not rt.pending and len(_frames(nest_rx)) == 1         # the second copy went out
    finally:
        rt.close()
        nest_rx.close()


def test_strict_mode_keeps_positions_off_the_disk(rt_mod, cfg, tmp_path, capsys):
    import re
    import life_node
    c, _ = _cfg(cfg, tmp_path, decide={"mode": "strict"})
    rt = _run(rt_mod, c, _args(tmp_path, no_radio=True, record=str(tmp_path / "rec")), "night")
    out = capsys.readouterr().out
    assert rt.rec is None and not (tmp_path / "rec").exists() and "recording is off" in out
    assert "list (not transmitted)" not in out and not re.search(r"\bE -?\d+(\.\d+)? N -?\d+", out)
    sends = [r for r in _log(tmp_path) if r.get("event") == "SEND"]
    assert sends and all("enu_m" not in r for r in _log(tmp_path))
    assert life_node.log.__name__ == "log"                             # the log filter is undone on close


def test_the_radio_is_drained_every_step(rt_mod, cfg, tmp_path):
    c, _ = _cfg(cfg, tmp_path)
    rt = rt_mod.EagleRuntime(c, _args(tmp_path))
    n = {"drain": 0}
    rt.radio.drain = lambda: n.__setitem__("drain", n["drain"] + 1)
    try:
        for k in range(3):
            rt.step(1000.0 + k)
    finally:
        rt.close()
    assert n["drain"] == 3


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
