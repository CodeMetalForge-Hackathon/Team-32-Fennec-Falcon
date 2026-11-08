"""Fennec Fix: bit-exact with the relay design's reference code, sealed to 15 bytes, decoded at the
Nest; what the gate chooses to say; which path home; COMPROMISED, courier, Iridium."""
import math
import random
import time

import numpy as np
import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

from eagle import fennec as F
from eagle.config import deep_merge
from eagle.decide import DecisionEngine
from eagle.geo import LocalFrame
from eagle.iridium import RockBlock

KEY, MID = bytes(range(16)), bytes.fromhex("000a0001")

# ---- the design's reference code (Fennec Data Relay, "Reference code"), verbatim in behaviour ----
REF_TYPES = {"FIX": 0, "BEARING": 1, "MOVED": 2, "STATUS": 3, "COMPROMISED": 4, "TEST": 5}


def ref_pack64(mtype, east_m, north_m, err_k, cue, conf, state, t_min):
    e = round(east_m / 2.5) & 0xFFFF
    n = round(north_m / 2.5) & 0xFFFF
    v = ((REF_TYPES[mtype] << 61) | (e << 45) | (n << 29) | (err_k << 25)
         | (cue << 22) | (conf << 20) | (state << 16) | (t_min & 0xFFFF))
    return v.to_bytes(8, "big")


def ref_unpack64(b):
    v = int.from_bytes(b, "big")
    s16 = lambda x: x - 0x10000 if x & 0x8000 else x  # noqa: E731
    return {"type": v >> 61, "east_m": s16((v >> 45) & 0xFFFF) * 2.5,
            "north_m": s16((v >> 29) & 0xFFFF) * 2.5, "err95_m": round(5 * 1.5 ** ((v >> 25) & 0xF)),
            "cue": (v >> 22) & 7, "conf": (v >> 20) & 3, "state": (v >> 16) & 0xF, "t_min": v & 0xFFFF}


def ref_seal(key, mission_id, node, ctr, payload8):
    hdr = bytes([0x40 | node]) + ctr.to_bytes(2, "big")
    nonce = mission_id + bytes([node]) + ctr.to_bytes(2, "big") + bytes(6)
    return hdr + AESCCM(key, tag_length=4).encrypt(nonce, payload8, hdr)


# ---------------------------------------------------------------------------------- codec
def test_design_example_is_15_bytes_and_bit_exact():
    p = F.pack64("FIX", -412.5, 1287.5, 7, 0, 2, 0b0010, 1432)
    assert p == ref_pack64("FIX", -412.5, 1287.5, 7, 0, 2, 0b0010, 1432)
    pkt = F.seal(KEY, MID, 17, 301, p)
    assert len(pkt) == 15 and pkt == ref_seal(KEY, MID, 17, 301, p)
    node, ctr, p8 = F.open_packet(KEY, MID, pkt)
    assert (node, ctr) == (17, 301)
    d = F.unpack64(p8)
    assert {k: d[k] for k in ref_unpack64(p8)} == ref_unpack64(p8)
    assert d["type_name"] == "FIX" and d["east_m"] == -412.5 and d["north_m"] == 1287.5 and d["err95_m"] == 85


def test_random_reports_match_the_reference():
    rng = random.Random(7)
    for _ in range(300):
        args = (rng.choice(["FIX", "MOVED", "STATUS", "COMPROMISED", "TEST"]), rng.uniform(-81000, 81000),
                rng.uniform(-81000, 81000), rng.randrange(16), rng.randrange(8), rng.randrange(4), rng.randrange(16),
                rng.randrange(65536))
        assert F.pack64(*args) == ref_pack64(*args)
        assert {k: v for k, v in F.unpack64(F.pack64(*args)).items() if k in ref_unpack64(b"\0" * 8)} == \
            ref_unpack64(ref_pack64(*args))


def test_any_flipped_bit_or_wrong_key_is_rejected():
    pkt = F.seal(KEY, MID, 3, 9, F.pack64("FIX", 100, 200, 3, 5, 2, 6, 77))
    for i in range(15):
        bad = bytearray(pkt)
        bad[i] ^= 0x01
        with pytest.raises(InvalidTag):
            F.open_packet(KEY, MID, bytes(bad))                          # header bits are authenticated too
    with pytest.raises(InvalidTag):
        F.open_packet(bytes(16), MID, pkt)
    with pytest.raises(InvalidTag):
        F.open_packet(KEY, bytes(4), pkt)                                 # another mission id


def test_bearing_layout_round_trip():
    for az, el in ((0.0, -10.0), (47.3, -20.0), (359.6, 44.9), (181.0, -45.0)):
        d = F.unpack64(F.pack_bearing(-1234.5, 5678.0, az, el, 4000))
        assert d["type_name"] == "BEARING" and d["east_m"] == -1235.0 and d["north_m"] == 5677.5
        assert \
            abs((d["az_deg"] - az + 180) % 360 - 180) <= 360 / 1024 + 1e-9
        assert abs(d["el_deg"] - el) <= 90 / 254 + 1e-9 and d["t_min"] == 4000


def test_ranges_classes_and_counter():
    assert F.err_k_for(0) == 0 and F.err_k_for(5) == 0 and F.err_k_for(25) == 4 and F.err_k_for(1e6) == 15
    assert [F.conf_level(p) for p in (0.5, 0.95, 0.99, 0.9995)] == [0, 1, 2, 3]
    with pytest.raises(F.FennecError):
        F.pack64("FIX", 82000, 0, 0, 0, 0, 0, 0)                         # beyond +-81.9 km
    with pytest.raises(F.FennecError):
        F.seal(KEY, MID, 64, 0, bytes(8))                                # eagle id is 6 bits
    with pytest.raises(F.FennecError):
        F.seal(KEY, MID, 1, 0x10000, bytes(8))


def test_counter_persists_and_refuses_to_wrap(tmp_path):
    c = F.CounterFile(str(tmp_path / "ctr.json"))
    assert [c.next() for _ in range(3)] == [0, 1, 2]
    c2 = F.CounterFile(str(tmp_path / "ctr.json"))
    assert c2.next() == 3                                                # a reboot never reuses a nonce
    c2.value = 0xFFFF
    with pytest.raises(F.FennecError):
        c2.next()


def _mission(cfg):
    return F.Mission.from_config(cfg)


def test_nest_codec_drops_replays_per_eagle(cfg):
    m = _mission(cfg)
    e1, e2, nest = F.EagleCodec(m, 1), F.EagleCodec(m, 2), F.NestCodec(m, window=4)
    lat, lon = cfg["site"]["origin"]["lat"], cfg["site"]["origin"]["lon"]
    r = F.Report("FIX", m.start_unix + 600, lat=lat, lon=lon, err95_m=30, cue=5, conf=2, state=2)
    a, b = e1.seal(r), e2.seal(r)
    assert nest.open(a)["eagle_id"] == 1 and nest.open(b)["eagle_id"] == 2   # same counter, two Eagles
    with pytest.raises(F.FennecError, match="replay"):
        nest.open(a)
    for _ in range(6):
        nest.open(e1.seal(r))
    with pytest.raises(F.FennecError):
        nest.open(F.seal(m.key, m.mission_id, 1, 1, e1.payload(r)))      # far behind the window


def test_wipe_key_file(tmp_path):
    k = tmp_path / "mission.key"
    k.write_text("00" * 16)
    assert F.wipe_key_file(k) and not k.exists() and not F.wipe_key_file(k)


# ---------------------------------------------------------------------------------- what to say
def _engine(**over):
    cfg = {"mission": {"others_expected": 6, "search_area": None}, "log": {"path": None},
           "emcon": {"relocated_flag": None}}
    cfg = deep_merge(cfg, over)
    return DecisionEngine(cfg, perch_xy=(0, 0), log=lambda *_: None, codes=[{"name": "tap_2_3", "kind": "tap"}])


def _night(eng, life=None):
    from test_decide import _decided_scene, run
    return run(eng, _decided_scene(), 400, life=life)


def test_compose_says_little_and_what_matters():
    eng = _engine()
    life, d, _ = _night(eng)
    frame = LocalFrame(42.355, -71.0655, 10.0)
    r = eng.compose(d, site_frame=frame, perch_enu=(0, 0, 8))
    assert r.mtype == "FIX" and r.cue == F.CUE["several sensors"] and r.conf == 2
    assert r.state == F.ST_PATTERN | F.ST_INJURED and 24 < r.err95_m < 26     # 2.45 x hypot(track, 10 m own)
    e, n, _ = frame.to_enu(r.lat, r.lon, None)
    assert abs(e - 12.0) < 0.5 and abs(n - 5.0) < 0.5
    assert eng.compose(d, site_frame=frame, perch_enu=(0, 0, 8), test=True).mtype == "TEST"


def test_bearing_subject_goes_out_as_a_compass_bearing_from_the_perch():
    from test_decide import code, run
    eng = _engine()
    far = np.array([60.0, 50.0])
    times = [0.0, 40.0, 80.0] + [float(t) for t in range(120, 420, 60)]
    _, d, _ = run(eng, lambda t: [code(t, far, point=False)] if t in times else [], 600)
    frame = LocalFrame(42.355, -71.0655, 10.0)
    r = eng.compose(d, site_frame=frame, perch_enu=(0, 0, 8))
    assert r.mtype == "BEARING" and abs(r.az_deg - (90 - math.degrees(math.atan2(50, 60)))) < 0.5
    e, n, _ = frame.to_enu(r.own_lat, r.own_lon, None)
    assert abs(e) < 0.5 and abs(n) < 0.5                               # the Eagle's own position


def test_a_poor_fix_becomes_a_bearing():
    eng = _engine(report={"own_position_ce_m": 300.0})                 # no navigation: own position 300 m
    _, d, _ = _night(eng)
    r = eng.compose(d, site_frame=LocalFrame(42.355, -71.0655, 10.0), perch_enu=(0, 0, 8))
    assert r.mtype == "BEARING" and abs(r.az_deg - (90 - math.degrees(math.atan2(5, 12)))) < 0.5


# ---------------------------------------------------------------------------------- which path
@pytest.mark.parametrize("mode,los,relay,irid,courier,want", [
    ("default", True, False, False, False, "nest"),
    ("default", False, True, True, False, "whisper"),
    ("default", False, False, True, True, "iridium"),
    ("default", False, False, False, True, "courier"),
    ("default", None, False, False, False, "nest"),                     # nothing known: blind Nest burst
    ("default", None, False, True, False, "iridium"),                   # unknown LOS: the confirmed path first
    ("strict", False, False, True, True, "courier"),                    # strict never uses Iridium
    ("strict", False, False, True, False, None),
    ("training", True, True, True, True, "nest"),
])
def test_path_choice(mode, los, relay, irid, courier, want):
    eng = _engine(mode=mode)
    path, why, table = eng.plan_path(nest_los=los, relay_overhead=relay, iridium=irid, courier=courier, relocated=False)
    assert path == want, (why, table)


def test_contested_ground_never_transmits_from_the_perch():
    eng = _engine(mission={"contested": True})
    _, send, hist = _night_noreturn(eng)
    acts = [d for t, ds in hist for d in ds if t >= 160]
    assert send is None and acts and all(d.action == "HOLD" for d in acts) and "contested" in acts[0].reason


def _night_noreturn(eng):
    from test_decide import _decided_scene, run
    return run(eng, _decided_scene(), 600, stop_on_send=False)


# ---------------------------------------------------------------------------------- Iridium
class FakeModem:
    """Scripted RockBLOCK: answers AT commands like the 9603N."""

    def __init__(self, mo_statuses=(0,)):
        self.mo = list(mo_statuses)
        self.out, self.written = [], b""

    def write(self, b):
        self.written += b
        txt = b.decode("ascii", "replace").strip()
        if txt.startswith("AT+SBDWB="):
            self.out += [b"READY\r\n"]
            self.expect_bin = int(txt.split("=")[1]) + 2
        elif txt == "AT+SBDIX":
            self.out += [f"+SBDIX: {self.mo.pop(0)}, 12, 0, 0, 0, 0\r\n".encode(), b"OK\r\n"]
        elif txt.startswith("AT"):
            self.out += [b"OK\r\n"]
        else:                                                           # the binary message + checksum
            self.out += [b"0\r\n", b"OK\r\n"]

    def readline(self):
        return self.out.pop(0) if self.out else b""

    def close(self):
        pass


def test_rockblock_sends_and_confirms():
    m = FakeModem([32, 0])                                             # no network, then through
    rb = RockBlock(ser=m, backoff_s=0.0, log=lambda *_: None)
    res = rb.send(b"\x41" + bytes(14))
    assert res["ok"] and res["mo_status"] == 0 and res["attempts"] == 2
    payload = b"\x41" + bytes(14)
    assert payload + (sum(payload) & 0xFFFF).to_bytes(2, "big") in m.written


# ---------------------------------------------------------------------------------- runtime paths
from test_runtime import rt_mod  # noqa: E402,F401,F811  (the runtime module fixture)


def test_courier_holds_the_sealed_report_until_the_nest_flag(rt_mod, cfg, tmp_path):
    from test_runtime import _args, _cfg, _frames
    from eagle.link import UdpTransport
    flag = tmp_path / "at_nest"
    c, (pn, pq) = _cfg(cfg, tmp_path, decide={"paths": {"nest_los": False, "courier": True,
                                                         "courier_flag": str(flag)}})
    nest_rx = UdpTransport(f"127.0.0.1:{pq}", f"127.0.0.1:{pn}")
    try:
        sim, dur, _, _ = rt_mod.make_sim("night", c["perch"]["enu_m"][:2])
        rt = rt_mod.EagleRuntime(c, _args(tmp_path), sim=sim)
        t0 = time.time()
        for k in range(150):
            rt.step(t0 + k)
        assert rt.courier and _frames(nest_rx) == []                   # decided, sealed, held: nothing on air
        flag.touch()
        import os
        os.utime(flag, (t0 + 151, t0 + 151))                             # touched after the hold (sim clock)
        rt.step(t0 + 151)
        frames = _frames(nest_rx)
        rt.close()
    finally:
        nest_rx.close()
    assert len(frames) == 1 and len(frames[0]) == 15 and not rt.courier


def test_compromised_sends_once_wipes_the_key_and_goes_silent(rt_mod, cfg, tmp_path):
    from test_runtime import _args, _cfg, _frames
    from eagle.link import UdpTransport
    from eagle.nest import NestStation
    key_file, flag = tmp_path / "mission.key", tmp_path / "compromised"
    key_file.write_text(cfg["fennec"]["key"])
    c, (pn, pq) = _cfg(cfg, tmp_path, fennec={"key_file": str(key_file), "compromised_flag": str(flag)})
    nest_rx = UdpTransport(f"127.0.0.1:{pq}", f"127.0.0.1:{pn}")
    try:
        sim, dur, _, _ = rt_mod.make_sim("night", c["perch"]["enu_m"][:2])
        rt = rt_mod.EagleRuntime(c, _args(tmp_path), sim=sim)
        t0 = time.time()
        rt.step(t0)
        flag.touch()
        for k in range(1, 200):
            rt.step(t0 + k)                                            # the night scene decides at ~100 s
        rt.close()
        frames = _frames(nest_rx)
    finally:
        nest_rx.close()
    assert not key_file.exists() and rt.silenced
    nest = NestStation(c, senders=[], log=lambda *_: None, sim=True)
    got = [nest.handle(type("Rx", (), {"payload": f, "rssi": -90.0, "snr": 5.0})()) for f in frames]
    assert [g[0]["type_name"] for g in got if g] == ["COMPROMISED"]    # once, and nothing after it


def test_nest_crosses_two_bearings_from_different_eagles(cfg):
    from eagle.nest import NestStation
    m = _mission(cfg)
    frame = m.frame()
    target = np.array([400.0, 300.0])
    pkts = []
    for node, own in ((1, np.array([0.0, 0.0])), (2, np.array([800.0, 0.0]))):
        v = target - own
        az = math.degrees(math.atan2(v[0], v[1])) % 360                 # compass
        lat, lon, _ = frame.to_geodetic(*own, 0.0)
        pkts.append(F.EagleCodec(m, node).seal(F.Report("BEARING", m.start_unix + 60, own_lat=lat, own_lon=lon,
                                                        az_deg=az, el_deg=45.0)))
    nest = NestStation(cfg, senders=[], log=lambda *_: None, sim=True)
    out = [nest.handle(type("Rx", (), {"payload": p, "rssi": -90.0, "snr": 5.0})()) for p in pkts]
    xmls = out[1][1]
    assert len(xmls) == 2 and b"SAR CROSS FIX" in xmls[1]
    import re
    lat = float(re.search(rb'lat="([-0-9.]+)"', xmls[1]).group(1))
    lon = float(re.search(rb'lon="([-0-9.]+)"', xmls[1]).group(1))
    e, n, _ = frame.to_enu(lat, lon, None)
    assert np.hypot(e - 400.0, n - 300.0) < 10.0
