"""End to end: apps/life_demo.py and apps/life_node.py."""
import argparse
import importlib.util
import socket

import pytest

from eagle import protocol as P
from eagle.link import UdpTransport


def _load_app(root, name):
    spec = importlib.util.spec_from_file_location(name, root / "apps" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_life_demo_story(root, tmp_path):
    out = _load_app(root, "life_demo").main(["--plot", str(tmp_path / "life.png"), "--quiet"])
    assert out["ok"] and out["story_ok"]
    assert out["status"] == {"P1": "RESPONSIVE", "P2": "BREATHING", "P3": "WARM_ONLY", "P4": "RF_ONLY"}
    assert out["pos_err_m"]["P1"] < 0.5 and out["pos_err_m"]["P2"] < 0.5 and out["pos_err_m"]["P3"] < 0.5
    assert out["nest_persons"] == 4 and out["life_frames"] == 1 and out["life_bytes"][0] <= P.LIFE_MAX_LEN
    assert out["life_airtime_ms"] < 110
    types = {c["uid"]: c["type"] for c in out["cot"]}
    assert sorted(types.values()).count("a-f-G-U-C") == 1                 # only the tagged person
    if importlib.util.find_spec("matplotlib") is not None:
        assert (tmp_path / "life.png").stat().st_size > 10_000


def test_life_demo_ten_people():
    out = _load_app(__import__("pathlib").Path(__file__).resolve().parent.parent, "life_demo").main(
        ["--people", "10", "--seed", "2", "--plot", "", "--quiet"])
    assert out["ok"] and out["persons_found"] == out["persons"] and out["false_tracks"] == 0
    assert out["status_correct"] >= 9 and out["err_p95_m"] < 0.5
    assert out["life_frames"] == 2 and all(b <= P.LIFE_MAX_LEN for b in out["life_bytes"])


def _args(**kw):
    a = dict(modules=None, no_radio=True, vitals_port=None, require=None, record=None)
    a.update(kw)
    return argparse.Namespace(**a)


def test_life_node_runs_with_missing_modules(root, cfg, capsys):
    from eagle.config import deep_merge
    # Never open real hardware here: on a Pi with a USB mic array plugged in, the default input device
    # would otherwise be captured, and a PortAudio callback left running crashes later tests.
    c = deep_merge(cfg, {"audio": {"device": "eagle-test-no-such-device"}})
    mod = _load_app(root, "life_node")
    node = mod.LifeNode(c, _args())
    try:
        for k in range(3):
            node.step(now=100.0 + k)
    finally:
        node.close()
    text = capsys.readouterr().out
    assert "MODULE  audio: unavailable" in text
    assert "MODULE  vitals: unavailable" in text and "MODULE  vision: unavailable" in text
    assert "thermal" in node.mods and "THERMAL 1 warm body" in text and "LIFE    list" in text


def test_life_node_sends_life_over_udp(root, cfg, tmp_path):
    from eagle.config import deep_merge

    def port():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p
    pn, pq = port(), port()
    c = deep_merge(cfg, {"eagle": {"state_file": str(tmp_path / "ctr.json")},
                         "radio": {"transport": "udp", "udp": {"bind": f"127.0.0.1:{pn}", "peer": f"127.0.0.1:{pq}"}},
                         "life": {"modules": ["thermal"]}})
    nest_rx = UdpTransport(f"127.0.0.1:{pq}", f"127.0.0.1:{pn}")
    try:
        mod = _load_app(root, "life_node")
        node = mod.LifeNode(c, _args(no_radio=False))
        node.step(now=1000.0)
        node.close()
        frames = []
        while True:
            f = nest_rx.recv(timeout=0.5)
            if f is None:
                break
            frames.append(f.payload)
    finally:
        nest_rx.close()
    assert len(frames) == 1 and frames[0][0] == P.MSG_LIFE
    from eagle.config import load_keys
    rep = P.NestSession(load_keys(cfg)["fix_aes"], None, max_age_s=600).decrypt_life(frames[0])
    assert len(rep.persons) == 1 and rep.persons[0].evidence & P.EV_HEAT


def test_life_node_require(root, cfg, tmp_path):
    import pytest
    mod = _load_app(root, "life_node")
    c = {**cfg, "audio": {**cfg["audio"], "device": "eagle-test-no-such-device"}}
    with pytest.raises(mod.RequiredModuleError, match="audio"):
        mod.LifeNode(c, _args(require=["audio", "thermal"]))
    node = mod.LifeNode(c, _args(modules=["thermal"], require=["thermal"]))
    try:
        node.mods["thermal"][0].read = lambda: (_ for _ in ()).throw(OSError("unplugged"))
        with pytest.raises(mod.RequiredModuleError, match="thermal failed"):
            for k in range(mod.MAX_REQUIRED_FAILS * 2 + 2):
                node.step(now=100.0 + 2.0 * k)
    finally:
        node.close()
    assert mod.main(["--modules", "audio", "--require", "audio", "--no-radio", "--duration", "0",
                     "--config", str(_cfg_file(tmp_path, {"audio": {"device": "eagle-test-no-such-device"}}))]) == 3


def _cfg_file(tmp_path, d):
    import yaml
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(d))
    return p


def test_life_node_sends_one_target_fix_with_identity(root, cfg, tmp_path):
    """A track verified as the missing person (IDENTITY level 3) goes out once as a FIX whose class
    byte carries ID-FACE; LIFE lists alone carry no identity. No repeat within report.target_fix.every_s."""
    import numpy as np
    from eagle.config import deep_merge, load_keys
    from eagle.fusion import LifeDetection, iso_cov

    def port():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p
    pn, pq = port(), port()
    c = deep_merge(cfg, {"eagle": {"state_file": str(tmp_path / "ctr.json")},
                         "radio": {"transport": "udp", "udp": {"bind": f"127.0.0.1:{pn}", "peer": f"127.0.0.1:{pq}"}},
                         "life": {"modules": ["thermal"]}})
    nest_rx = UdpTransport(f"127.0.0.1:{pq}", f"127.0.0.1:{pn}")
    frames = []
    try:
        mod = _load_app(root, "life_node")
        node = mod.LifeNode(c, _args(no_radio=False))
        node.step(now=1000.0)                                    # warm body -> one track, LIFE list
        assert node.maybe_send_target_fix(1000.5) is None        # not verified yet: no FIX
        tr = node.life.tracks[0]
        node.life.update([LifeDetection("IDENTITY", 1001.0, xy=np.asarray(tr.x, float), cov=iso_cov(0.5),
                                        match_level=P.ID_FACE, match_face=0.55)], now=1001.0)
        node.life.evaluate(1001.0)
        assert node.maybe_send_target_fix(1001.0) is not None   # verified: one FIX
        assert node.maybe_send_target_fix(1010.0) is None        # within every_s: no repeat
        node.close()
        while True:
            f = nest_rx.recv(timeout=0.5)
            if f is None:
                break
            frames.append(f.payload)
    finally:
        nest_rx.close()
    fixes = [f for f in frames if f[0] != P.MSG_LIFE]
    assert len(fixes) == 1
    rep = P.NestSession(load_keys(cfg)["fix_aes"], None, max_age_s=10 ** 9).decrypt_fix(fixes[0])
    assert P.identity_level(rep.body.cls) == P.ID_FACE and "ID-FACE" in P.class_name(rep.body.cls)
