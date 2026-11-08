"""End-to-end: the simulation app, the node app on a WAV, the pipeline glue."""
import importlib.util
import socket

import numpy as np
import pytest

from eagle import protocol as P
from eagle.config import array_geometry, deep_merge, load_keys
from eagle.detect import Event, Match
from eagle.link import UdpTransport
from eagle.pipeline import Locator, dedupe_matches, noise_segments_for
from eagle.sim import simulate_array_capture


def _load_app(root, name):
    spec = importlib.util.spec_from_file_location(name, root / "apps" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_locator_fix_on_simulated_capture(cfg):
    geom = array_geometry(cfg, "respeaker_usb_v2")
    c = deep_merge(cfg, {"perch": {"enu_m": [0, 0, 8.0], "heading_deg": 30.0, "az_offset_deg": 0.0},
                         "audio": {"temperature_c": 20.0}})
    loc = Locator(c, geometry=geom)
    src = np.array([14.0, 9.0, 0.5])
    audio, _ = simulate_array_capture(geom.positions, geom.fs, loc.perch, 60.0, src, "three_whistles",
                                      snr_db=15, c=loc.c, seed=0)
    events, dets = loc.process(audio)
    assert len(dets) == 1 and dets[0].has_fix
    d = dets[0]
    assert np.linalg.norm(d.xyz[:2] - src[:2]) < 1.5
    assert d.lat is not None and abs(d.lat - cfg["site"]["origin"]["lat"]) < 0.001
    body = loc.fix_body(d, 1_800_000_000)
    assert body.cls == P.CLS_WHISTLE and 0 < body.ce_m < 10


def test_onset_segments_option(cfg):
    loc = Locator(deep_merge(cfg, {"doa": {"onset_s": 0.1}}))
    ev = [Event("whistle", 16000, 24000, 16000), Event("tap", 40000, 41000, 16000)]
    segs = loc.segments(Match("x", "whistle", ev))
    assert segs[0] == (16000 - 320, 16000 + 1600) and segs[1] == (40000, 41000)


def test_dedupe_prefers_bigger_then_earlier_code():
    ev = [Event("whistle", i * 16000, i * 16000 + 6000, 16000) for i in range(3)]
    a, b = Match("lss", "whistle", ev), Match("three", "whistle", ev)
    assert [m.code for m in dedupe_matches([b, a], ["lss", "three"])] == ["lss"]
    big = Match("five", "tap", ev + [Event("tap", 60000, 61000, 16000)])
    assert [m.code for m in dedupe_matches([a, big], ["lss", "five"])] == ["five"]


def test_noise_segments_avoid_events():
    fs = 16000
    ev = [Event("whistle", 2 * fs, int(2.5 * fs), fs), Event("whistle", 4 * fs, int(4.5 * fs), fs)]
    segs = noise_segments_for(Match("x", "whistle", ev), ev, 10 * fs, fs)
    for s, e in segs:
        for x in ev:
            assert e <= x.start or s >= x.end


def test_sim_demo_end_to_end(root, tmp_path):
    sim = _load_app(root, "sim_demo")
    out = sim.main(["--plot", str(tmp_path / "floor.png")])
    assert out["ok"] and out["nest_ok"]
    assert abs(out["bearing_err_deg"]) < 3.0 and out["pos_err_m"] < 3.0 and out["fused_err_m"] < 3.0
    assert out["fix_bytes"] == 48 and out["fix_airtime_ms"] == pytest.approx(48.768, abs=0.01)
    assert out["replay_ignored"] and not out["duress_seen_by_nest"]
    assert not out["scanner_saw_fix"] and out["scanner_saw_fix_at_max"]
    if importlib.util.find_spec("matplotlib") is not None:      # the plot is optional
        assert (tmp_path / "floor.png").stat().st_size > 10_000


@pytest.mark.parametrize("args,expect_duress", [
    (["--code", "long_short_short", "--duress", "--preset", "minidsp_uma16_v2"], True),
    (["--code", "taps3", "--no-tag", "--preset", "diy_tetra_4", "--no-beacon"], False),
])
def test_sim_demo_variants(root, args, expect_duress):
    out = _load_app(root, "sim_demo").main(args + ["--plot", "", "--single"])
    assert out["ok"] and out["pos_err_m"] < 3.0
    assert out["duress_seen_by_nest"] == expect_duress


def test_eagle_node_wav_over_udp(root, tmp_path, cfg):
    """sim -> WAV -> apps/eagle_node.py --wav -> UDP -> decrypt as the Nest would."""
    sim = _load_app(root, "sim_demo")
    wav = tmp_path / "cap.wav"
    sim.main(["--plot", "", "--single", "--save-wav", str(wav)])

    def port():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p
    p_node, p_nest = port(), port()
    yaml_text = (f"eagle: {{state_file: {tmp_path / 'ctr.json'}}}\n"
                 f"radio: {{transport: udp, udp: {{bind: '127.0.0.1:{p_node}', peer: '127.0.0.1:{p_nest}'}}}}\n"
                 f"poll: {{reply_window_ms: 50}}\n")
    (tmp_path / "local.yaml").write_text(yaml_text)
    nest_rx = UdpTransport(f"127.0.0.1:{p_nest}", f"127.0.0.1:{p_node}")
    try:
        node = _load_app(root, "eagle_node")
        assert node.main(["--config", str(tmp_path / "local.yaml"), "--wav", str(wav)]) == 0
        frames = []
        while True:
            f = nest_rx.recv(timeout=0.5)
            if f is None:
                break
            frames.append(f.payload)
    finally:
        nest_rx.close()
    assert [len(f) for f in frames] == [P.CHAL_LEN, P.FIX_LEN]          # one poll, ONE fix burst
    keys = load_keys(cfg)
    rep = P.NestSession(keys["fix_aes"], keys["covert"], max_age_s=600).decrypt_fix(frames[1])
    sim_lat, sim_lon = 42.3550767, -71.0653398              # the sim's own estimate for perch A
    assert abs(rep.lat - sim_lat) < 2e-5 and abs(rep.lon - sim_lon) < 2e-5
    assert rep.body.cls == P.CLS_WHISTLE and not rep.authenticated


def test_live_sliding_windows_report_each_code_once(root, cfg):
    """eagle_node's live loop logic (sliding windows + settle hold) without a sound card."""
    import argparse
    node_mod = _load_app(root, "eagle_node")
    args = argparse.Namespace(no_radio=True, clap_test=False, no_poll=True)
    node = node_mod.Node(cfg, args)
    geom = node.geom
    audio, _ = simulate_array_capture(geom.positions, geom.fs, node.loc.perch, 60.0, [10.0, 6.0, 0.5],
                                      "tap_2_3", snr_db=20, total_s=12.0, c=node.loc.c, seed=2)
    reported = []
    node.report = lambda det: reported.append(det.match.code)
    fs, win = geom.fs, 8 * geom.fs
    for end in range(fs, audio.shape[1] + 1, fs):              # 1 s steps, 8 s window
        start = max(0, end - win)
        node.process(audio[:, start:end], start, settle_s=1.5)
    assert reported == ["tap_2_3"]
