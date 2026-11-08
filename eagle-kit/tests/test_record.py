"""eagle/record.py and life_node --record: files written, readable, and closed on exit."""
import argparse
import importlib.util
import json
import threading
import wave

import numpy as np
import pytest

from eagle.config import deep_merge
from eagle.record import Recorder, make_run_dir, size_per_hour


class FakeCapture:
    """LiveCapture stand-in: ``feed`` appends samples; ``since`` as in eagle/audio_io.py."""
    fs, channels = 16000, 6

    def __init__(self, ring=16000):
        self.buf = np.zeros((ring, self.channels), np.float32)
        self.total, self.lock = 0, threading.Lock()

    def feed(self, n):
        x = (np.arange(self.total, self.total + n)[:, None] % 100 / 100.0 - 0.5).astype(np.float32)
        x = np.repeat(x, self.channels, axis=1) * (np.arange(self.channels) + 1) / 10
        for row in x:
            self.buf[self.total % len(self.buf)] = row
            self.total += 1

    def stop(self):
        pass

    def window(self, seconds):
        return self.total, np.zeros((0, self.channels), np.float32)


def _since():
    from eagle.audio_io import LiveCapture
    return LiveCapture.since


FakeCapture.since = _since()


def test_run_dirs_are_unique_and_latest_points_at_newest(tmp_path):
    a, b = make_run_dir(tmp_path, now=0), make_run_dir(tmp_path, now=0)
    assert a != b and b.name.endswith("-2")
    assert (tmp_path / "latest").resolve() == b.resolve()


def test_recorder_thermal_audio_events(tmp_path):
    t = [1000.0]
    r = Recorder(tmp_path, thermal_hz=2.0, chunk_s=2.0, min_free_gb=0.0, log=lambda m: None, clock=lambda: t[0])
    cap = FakeCapture()
    r.audio(cap)                                         # first call only syncs to the current total
    for k in range(12):                                  # 6 s at 4 steps/s
        t[0] = 1000.0 + k * 0.25
        frame = np.full((4, 5), 20.0) + k * 0.01
        r.thermal(t[0], frame)
        cap.feed(4000)
        r.audio(cap)
        r.event("detection", kind="HEAT", xy=np.array([1.0, 2.0]))
    r.close()
    th = sorted(r.dir.glob("thermal_*.npz"))
    ts = np.concatenate([np.load(p)["t"] for p in th])
    assert len(ts) == 6 and np.allclose(np.diff(ts), 0.5)          # 2/s, not every call
    c = np.load(th[0])["centi_c"]
    assert c.dtype == np.int16 and c[0, 0, 0] == 2000
    wavs = sorted(r.dir.glob("audio_*.wav"))
    assert len(wavs) >= 2                                          # chunked
    n = 0
    for p in wavs:
        with wave.open(str(p)) as w:
            assert w.getnchannels() == 6 and w.getframerate() == 16000 and w.getsampwidth() == 2
            n += w.getnframes()
    assert n == 12 * 4000
    meta = json.loads((r.dir / "audio_0000.json").read_text())
    assert meta["fs"] == 16000 and meta["channels"] == 6
    ev = [json.loads(l) for l in (r.dir / "events.jsonl").read_text().splitlines()]
    assert ev[0]["xy"] == [1.0, 2.0] and ev[0]["event"] == "detection" and ev[0]["kind"] == "HEAT" and ev[-1]["event"] == "record_closed"
    assert set(size_per_hour(r.dir)) == {"thermal", "audio", "events"}


def test_audio_gap_is_logged(tmp_path):
    r = Recorder(tmp_path, min_free_gb=0.0, log=lambda m: None)
    cap = FakeCapture(ring=1000)
    r.audio(cap)
    cap.feed(2500)                                       # more than the ring holds
    r.audio(cap)
    r.close()
    ev = [json.loads(l) for l in (r.dir / "events.jsonl").read_text().splitlines()]
    assert any(e["event"] == "audio_gap" and e["samples"] == 1500 for e in ev)


def test_recording_stops_when_disk_is_full(tmp_path):
    msgs = []
    r = Recorder(tmp_path, min_free_gb=1e9, log=msgs.append)      # "needs" an exabyte free
    r.tick()
    r.thermal(0.0, np.zeros((2, 2)))
    r.close()
    assert not r.active and any("stopped" in m for m in msgs)
    assert not list(r.dir.glob("thermal_*.npz"))


def test_life_node_record(root, cfg, tmp_path):
    spec = importlib.util.spec_from_file_location("life_node_rec", root / "apps/life_node.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    c = deep_merge(cfg, {"audio": {"device": "eagle-test-no-such-device"}, "life": {"modules": ["thermal"]}})
    args = argparse.Namespace(modules=None, no_radio=True, vitals_port=None, record=str(tmp_path))
    node = mod.LifeNode(c, args)
    try:
        for k in range(8):                               # 4 s at the default 0.5 s step
            node.step(now=100.0 + 0.5 * k)
    finally:
        node.close()
    run = (tmp_path / "latest").resolve()
    ts = np.concatenate([np.load(p)["t"] for p in sorted(run.glob("thermal_*.npz"))])
    assert len(ts) == 8                                  # 2/s although detection runs every 2 s
    kinds = [json.loads(l)["event"] for l in (run / "events.jsonl").read_text().splitlines()]
    assert kinds[0] == "run_start" and "detection" in kinds and "life_list" in kinds and kinds[-1] == "record_closed"
