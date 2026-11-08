"""LIFE frames: round trip, tamper, replay, chunking, tag unmasking, Nest CoT per person."""
import numpy as np
import pytest

from eagle import protocol as P
from eagle.cot import parse_cot
from eagle.geo import LocalFrame
from eagle.link import RxFrame
from eagle.nest import NestStation

KT, KF, KC = bytes(range(32)), bytes(range(32, 64)), bytes(range(64, 96))
NOW = 1_800_000_000


def persons(n, tagged=()):
    out = []
    for k in range(n):
        p = P.LifePerson(k + 1, -3.0 + 0.7 * k, 4.0 + 0.5 * k, 0.33, P.LS_BREATHING if k % 2 else P.LS_WARM_ONLY,
                         0, 0.6, 12.0 if k % 2 else 0.0, 70.0 if k % 2 else 0.0, P.EV_HEAT | (P.EV_BREATH if k % 2 else 0))
        if k in tagged:
            p.evidence |= P.EV_TAG
            p.tag_id, p.tag_status_wire, p.tag_chal16 = 7, 0x5A, 0x1234
        out.append(p)
    return out


def session(start=100):
    return P.EagleSession(3, KT, KF, counter=P.CounterStore(None, start=start), salt=0x01020304)


def test_round_trip_and_sizes():
    head = P.LifeHead(NOW, 42.355, -71.0655, 10.0)
    frames = session().encrypt_life(head, persons(4, tagged=(1,)))
    assert len(frames) == 1 and len(frames[0]) == P.LIFE_MIN_LEN + 4 * P.LIFE_PERSON_LEN + P.LIFE_TAG_LEN == 88
    eid, salt, ctr, h, ps = P.decrypt_life_frame(KF, frames[0])
    assert (eid, salt, ctr) == (3, 0x01020304, 101) and h.n == 4 and h.chunk_count == 1 and h.list_seq == 101
    assert abs(h.lat - 42.355) < 1e-7 and h.floor_hae_m == 10
    src = persons(4, tagged=(1,))
    for a, b in zip(ps, src):
        assert (a.track_id, a.status, a.evidence) == (b.track_id, b.status, b.evidence)
        assert abs(a.east_m - b.east_m) <= 0.05 and abs(a.north_m - b.north_m) <= 0.05
        assert a.ce_m == pytest.approx(0.4) and a.breath_bpm == b.breath_bpm and abs(a.score - 0.6) < 0.08
    assert ps[1].tag_id == 7 and ps[1].tag_status_wire == 0x5A and ps[1].tag_chal16 == 0x1234
    big = P.LifePerson(1, 5000.0, -5000.0, 99.0, P.LS_RF_ONLY)           # saturating fields
    out = P.LifePerson.unpack(big.pack())[0]
    assert out.east_m == 3276.7 and out.north_m == -3276.8 and out.ce_m == 25.5


def test_tamper_and_wrong_key_rejected():
    frame = session().encrypt_life(P.LifeHead(NOW, 1.0, 2.0), persons(3))[0]
    for i in (0 + 1, 5, 9, 12, 30, len(frame) - 1):
        bad = bytearray(frame)
        bad[i] ^= 0x01
        with pytest.raises(P.ProtocolError):
            P.decrypt_life_frame(KF, bytes(bad))
    with pytest.raises(P.AuthError):
        P.decrypt_life_frame(bytes(32), frame)


def test_replay_and_stale_rejected_shared_counter_with_fix():
    s, nest = session(), P.NestSession(KF, KC, max_age_s=600)
    f1 = s.encrypt_life(P.LifeHead(NOW, 1.0, 2.0), persons(2))[0]
    fix = s.encrypt_fix(P.FixBody(1.0, 2.0, t_unix=NOW))
    f3 = s.encrypt_life(P.LifeHead(NOW, 1.0, 2.0), persons(2))[0]
    assert nest.decrypt_life(f1, now=NOW).counter == 101
    assert nest.decrypt_fix(fix, now=NOW).counter == 102
    with pytest.raises(P.ReplayError):
        nest.decrypt_life(f1, now=NOW)
    with pytest.raises(P.ReplayError):
        nest.decrypt_life(f3, now=NOW + 3600)
    assert nest.decrypt_life(f3, now=NOW).counter == 103


def test_chunking_for_ten_plus_people():
    ps = persons(14, tagged=(0, 5))
    frames = session().encrypt_life(P.LifeHead(NOW, 1.0, 2.0), ps)
    assert len(frames) == 2 and all(len(f) <= P.LIFE_MAX_LEN for f in frames)
    nest = P.NestSession(KF, None, max_age_s=0)
    reps = [nest.decrypt_life(f) for f in frames]
    assert [r.head.chunk_idx for r in reps] == [0, 1] and {r.head.chunk_count for r in reps} == {2}
    assert len({r.head.list_seq for r in reps}) == 1
    assert [p.track_id for r in reps for p in r.persons] == list(range(1, 15))
    small = session().encrypt_life(P.LifeHead(NOW, 1.0, 2.0), ps, max_len=64)   # 2 untagged or 1 tagged each
    assert len(small) == 8 and all(len(f) <= 64 for f in small)
    nest2 = P.NestSession(KF, None, max_age_s=0)
    assert sum(len(nest2.decrypt_life(f).persons) for f in small) == 14


def test_nest_unmasks_tag_status_duress_and_button():
    tag = P.TagLogic(7, KT, KC, status=P.ST_INJURED | P.ST_BUTTON)
    tag.duress = True
    s = session()
    ch, nonce = s.make_challenge(500, -9)
    rep = s.verify_reply(tag.on_frame(ch, -70).frame, nonce)
    p = persons(1)[0]
    p.status, p.evidence = P.LS_WARM_ONLY, P.EV_HEAT | P.EV_TAG
    p.tag_id, p.tag_status_wire, p.tag_chal16 = rep.tag_id, rep.status_wire, P.nonce_counter(nonce) & 0xFFFF
    frame = s.encrypt_life(P.LifeHead(NOW, 1.0, 2.0), [p])[0]
    out = P.NestSession(KF, KC, max_age_s=0).decrypt_life(frame).persons[0]
    assert out.flags & P.LF_DURESS and out.flags & P.LF_INJURED and out.status == P.LS_RESPONSIVE
    blind = P.NestSession(KF, None, max_age_s=0).decrypt_life(frame).persons[0]
    assert blind.tag_status is None and not blind.flags


def test_nest_cot_per_person(cfg, keys):
    nest = NestStation(cfg, senders=[], log=lambda m: None, sim=True)
    s = P.EagleSession(1, keys["tag_hmac"], keys["fix_aes"], counter=P.CounterStore(None, start=5))
    ps = persons(3, tagged=(2,))
    o = cfg["site"]["origin"]
    frame = s.encrypt_life(P.LifeHead(NOW, o["lat"], o["lon"], 10.0), ps)[0]
    rep, xmls = nest.handle(RxFrame(frame, -100, 5), now=NOW)
    assert isinstance(rep, P.LifeReport) and len(xmls) == 3
    cots = [parse_cot(x) for x in xmls]
    assert [c["uid"] for c in cots] == ["eagle-1-p1", "eagle-1-p2", "eagle-1-p3"]
    assert [c["type"] for c in cots] == ["a-u-G", "a-u-G", "a-f-G-U-C"]
    assert "BREATHING" in cots[1]["remarks"] and "breath 12/min" in cots[1]["remarks"] and "heart 70 bpm" in cots[1]["remarks"]
    assert "evidence: heat, breath" in cots[1]["remarks"] and "WARM_ONLY" in cots[0]["callsign"]
    fr = LocalFrame(o["lat"], o["lon"], 10.0)
    for c, p in zip(cots, ps):
        e, n, _ = fr.to_enu(float(c["point_lat"]), float(c["point_lon"]), 10.0)
        assert abs(e - p.east_m) < 0.06 and abs(n - p.north_m) < 0.06
    assert nest.handle(RxFrame(frame, -100, 5), now=NOW) is None              # replay rejected
    fix = s.encrypt_fix(P.FixBody(42.0, -71.0, 10, 4, P.CLS_WHISTLE, 0.8, NOW))
    rep2, xml2 = nest.handle(RxFrame(fix, -100, 5), now=NOW)                  # FIX path unchanged
    assert isinstance(rep2, P.FixReport) and parse_cot(xml2)["uid"].startswith("eagle-1-")
