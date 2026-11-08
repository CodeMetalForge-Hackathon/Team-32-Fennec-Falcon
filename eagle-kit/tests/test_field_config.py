"""Field page support: config `include:` overrides and the recorder's live thermal frame."""
import json

import numpy as np

from eagle.config import load_config
from eagle.record import Recorder


def test_include_overrides_in_order_and_skips_missing(tmp_path):
    site = tmp_path / "site.yaml"
    site.write_text(json.dumps({"perch": {"heading_deg": 123.0}, "site": {"origin": {"lat": 1.5}}}))
    main = tmp_path / "field.yaml"
    main.write_text(f"include: [{site}, {tmp_path / 'missing.yaml'}]\nperch:\n  heading_deg: 10.0\n"
                    "  az_offset_deg: 7.0\n")
    cfg = load_config(main)
    assert "include" not in cfg
    assert cfg["perch"]["heading_deg"] == 123.0          # include wins over the main file
    assert cfg["perch"]["az_offset_deg"] == 7.0          # untouched keys survive the merge
    assert cfg["site"]["origin"]["lat"] == 1.5
    assert "lon" in cfg["site"]["origin"]                # example.yaml defaults still there


def test_include_as_single_string(tmp_path):
    site = tmp_path / "site.yaml"
    site.write_text(json.dumps({"perch": {"heading_deg": 45.0}}))
    main = tmp_path / "field.yaml"
    main.write_text(f"include: {site}\n")
    assert load_config(main)["perch"]["heading_deg"] == 45.0


def test_no_include_unchanged(tmp_path):
    main = tmp_path / "field.yaml"
    main.write_text("perch:\n  heading_deg: 10.0\n")
    assert load_config(main)["perch"]["heading_deg"] == 10.0


def test_recorder_writes_live_thermal(tmp_path):
    rec = Recorder(tmp_path, thermal_hz=2.0, log=lambda *a: None, clock=lambda: 1000.0)
    frame = np.full((192, 256), 31.25)
    rec.thermal(1000.0, frame)
    live = np.load(tmp_path / "live_thermal.npy")
    assert live.shape == (192, 256) and live.dtype == np.int16
    assert live[0, 0] == 3125
    assert not (tmp_path / "live_thermal.tmp.npy").exists()
    rec.close()


def test_recorder_writes_live_audio(tmp_path):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("trec", Path(__file__).with_name("test_record.py"))
    trec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trec)
    cap = trec.FakeCapture()
    t = [1000.0]
    rec = Recorder(tmp_path, log=lambda *a: None, clock=lambda: t[0])
    rec.audio(cap)                                        # first call only sets the start sample
    cap.feed(8000)
    rec.audio(cap)
    d = np.load(tmp_path / "live_audio.npz")
    assert d["pcm"].dtype == np.int16 and len(d["pcm"]) == 8000 and int(d["fs"]) == 16000
    for _ in range(12):                                   # 6 s more: the live buffer keeps the last 4 s
        t[0] += 0.5
        cap.feed(8000)
        rec.audio(cap)
    assert len(np.load(tmp_path / "live_audio.npz")["pcm"]) == 64000
    rec.close()


def test_live_thermal_faster_than_recording(tmp_path):
    rec = Recorder(tmp_path, thermal_hz=2.0, live_hz=8.0, log=lambda *a: None, clock=lambda: 1000.0)
    recorded = live = 0
    for k in range(16):                                   # 2 s at 8 Hz
        t = 1000.0 + k * 0.125
        if rec.live_due(t) or rec.thermal_due(t):
            if rec.thermal_due(t):
                rec.thermal(t, np.full((4, 4), 30.0))
                recorded += 1
            else:
                rec.thermal_live(t, np.full((4, 4), 31.0))
            live += 1
    assert recorded == 4 and live == 16                   # recording stays at 2/s, live view at 8/s
    assert np.load(tmp_path / "live_thermal.npy")[0, 0] == 3100
    rec.close()
