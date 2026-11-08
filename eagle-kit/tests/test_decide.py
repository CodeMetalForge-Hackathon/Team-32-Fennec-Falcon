"""Decision engine: the gate opens for him, stays shut for everyone else, and respects EMCON."""
import json
import math

import numpy as np
import pytest

from eagle import protocol as P
from eagle.decide import (HOLD, REPORTED, SEND, WATCH, DecisionEngine, DecisionLog, LedgerLifeList,
                          code_rule, false_report_rate_per_h, intercept_radius_m, prior_log_odds)
from eagle.fusion import LifeDetection, iso_cov

PERCH = np.zeros(2)
HIM = np.array([12.0, 5.0])
AREA = {"center_enu_m": [10.0, 0.0], "radius_m": 50.0}


def az_to(xy, origin=PERCH):
    d = np.asarray(xy, float) - origin
    return math.degrees(math.atan2(d[1], d[0]))


def heat(t, xy, shape=None):
    d = LifeDetection("HEAT", t, xy=np.asarray(xy, float), cov=iso_cov(0.4), temp_c=31.0, source="thermal")
    if shape is not None:
        d.shape = shape
    return d


def code(t, xy=HIM, name="tap_2_3", point=True, az_err=0.0, conf=None):
    d = LifeDetection("SOUND", t, xy=np.asarray(xy, float) if point else None, cov=iso_cov(1.0) if point else None,
                      origin=PERCH, az_deg=az_to(xy) + az_err, sigma_az_deg=2.0, category="SIGNAL",
                      status_bits=P.ST_INJURED, source=f"code:{name}")
    if conf is not None:
        d.conf = conf
    return d


def sound(t, cat, xy=HIM):
    return LifeDetection("SOUND", t, xy=np.asarray(xy, float), cov=iso_cov(1.0), origin=PERCH,
                         az_deg=az_to(xy), sigma_az_deg=2.0, category=cat, source="sounds")


def engine(**over):
    cfg = {"mission": {"others_expected": 6, "search_area": AREA}, "log": {"path": None},
           "emcon": {"relocated_flag": None}}
    for k, v in over.items():
        cfg[k] = {**cfg.get(k, {}), **v} if isinstance(v, dict) else v
    return DecisionEngine(cfg, perch_xy=PERCH, log=lambda *_: None,
                          codes=[{"name": "tap_2_3", "kind": "tap"}, {"name": "three_whistles", "kind": "whistle"}])


def run(eng, scene, t_end, step=10.0, life=None, stop_on_send=True, t0=0.0):
    """Drive life list + engine; returns (life, first SEND decision or None, all decisions by t)."""
    life = life or LedgerLifeList(perch_xy=PERCH, death_s=900.0)
    hist = []
    t = float(t0)
    while t <= t_end + 1e-9:
        dets = scene(t)
        for kind in dict.fromkeys(d.kind for d in dets):     # one batch per sensor, as the runtime does
            life.update([d for d in dets if d.kind == kind], now=t)
        life.evaluate(t)
        eng.ingest(life)
        ds = eng.tick(t, life)
        hist.append((t, ds))
        send = next((d for d in ds if d.action == SEND), None)
        if send is not None and stop_on_send:
            return life, send, hist
        t += step
    return life, None, hist


# ---------------------------------------------------------------------------------- math
def test_prior_is_one_in_n_plus_one():
    assert math.isclose(1 / (1 + math.exp(-prior_log_odds(6))), 1 / 7)
    assert 1 / (1 + math.exp(-prior_log_odds(0))) == pytest.approx(0.5)     # clamped: never certain a priori


def test_code_rule_bearing_count_and_confidence():
    ok, n = code_rule([(0, 40.0, None), (30, 45.0, None), (60, 36.0, None)])
    assert ok and n == 3
    ok, n = code_rule([(0, 40.0, None), (30, 45.0, None), (60, 80.0, None)])
    assert not ok and n == 2
    assert not code_rule([(0, 40.0, 0.5), (30, 41.0, 0.6), (60, 42.0, 0.7)])[0]       # confident? no
    assert code_rule([(0, 359.0, 0.95), (30, 3.0, 0.92), (60, 1.0, None)])[0]          # wraps through north


def test_false_reports_scale_with_the_cube_of_false_alarms():
    r1, r2 = false_report_rate_per_h(2.0), false_report_rate_per_h(4.0)
    assert r2 / r1 == pytest.approx(8.0)
    assert false_report_rate_per_h(6.0, bearing_tol_deg=None) > false_report_rate_per_h(6.0)


def test_intercept_radius_matches_log_distance_model():
    r = intercept_radius_m(-9.0, -115.0, 31.7, 3.0, 1.0)
    pl = 31.7 + 30.0 * math.log10(r)
    assert -9.0 - pl == pytest.approx(-115.0)


# ---------------------------------------------------------------------------------- him
def test_night_briefed_code_sends_after_the_code_rule():
    """Sim v2 02:00 case: clear thermal shape, alone, inside the area; he taps the code 3 times."""
    eng = engine()

    def scene(t):
        out = [heat(t, HIM, shape=0.8)] if t % 20 == 0 else []
        if t in (600.0, 650.0, 700.0):
            out.append(code(t))
        return out

    _, send, hist = run(eng, scene, 1200)
    assert send is not None
    assert send.t == pytest.approx(700.0)               # not before the third tap
    assert send.p >= 0.99 and set(send.modalities) == {"thermal", "acoustic"}
    assert send.cls == P.CLS_TAP and send.status_bits & P.ST_INJURED
    assert send.false_alarm.startswith("LOW")
    before = [d for t, ds in hist if t < 700 for d in ds]
    assert all(d.action == WATCH for d in before)


def test_one_false_code_match_does_not_report():
    eng = engine()
    scene = lambda t: ([heat(t, HIM, 0.8)] if t % 20 == 0 else []) + ([code(t)] if t == 300 else [])  # noqa: E731
    _, send, hist = run(eng, scene, 1500)
    assert send is None
    last = hist[-1][1][0]
    assert 0.8 < last.p < 0.95 and last.action == WATCH


def test_coughs_and_heat_decide_at_80_min_like_the_simulator():
    """Sim v2 'silent, only coughs': faint shape, coughs at 20/50/80/110 min -> TRANSMIT at 80 min."""
    eng = engine()
    coughs = {1200.0, 3000.0, 4800.0, 6600.0}
    scene = lambda t: ([heat(t, HIM)] if t % 60 == 0 else []) + ([sound(t, "COUGH")] if t in coughs else [])  # noqa: E731
    _, send, _ = run(eng, scene, 7200, step=30.0)
    assert send is not None and send.t == pytest.approx(4800.0)
    assert send.false_alarm.startswith("MEDIUM")


def test_unconscious_thermal_only_never_reports_on_one_sensor():
    """Warm, still 2 h, alone, in the area: P may pass 0.95 but one modality never reports."""
    eng = engine()
    scene = lambda t: [heat(t, HIM, 0.8)] if t % 60 == 0 else []  # noqa: E731
    _, send, hist = run(eng, scene, 7500, step=60.0)
    assert send is None
    last = hist[-1][1][0]
    assert last.p > 0.95 and "one modality" in last.reason


def test_radar_breathing_is_the_second_modality_for_the_unconscious():
    eng = engine()

    def scene(t):
        out = [heat(t, HIM)] if t % 60 == 0 else []
        if t % 600 == 0 and t >= 2700:
            out.append(LifeDetection("VITALS", t, xy=HIM, cov=iso_cov(1.0), breath_bpm=10, vitals=True,
                                     update_position=False, source="radar"))
        return out

    _, send, _ = run(eng, scene, 7500, step=60.0)
    assert send is not None and "radar" in send.modalities and send.false_alarm.startswith("HIGH")


def test_face_verified_identity_raises_p_and_lowers_false_alarm():
    eng = engine()

    def scene(t):
        out = [heat(t, HIM, 0.8)] if t % 10 == 0 else []
        if t >= 60 and t % 10 == 0:
            out.append(LifeDetection("PERSON", t, xy=HIM, cov=iso_cov(0.5), source="vision:lying"))
            out.append(LifeDetection("IDENTITY", t, xy=HIM, cov=iso_cov(0.5), match_level=P.ID_FACE,
                                     match_appearance=0.9, match_face=0.6, update_position=False))
        return out

    life = LedgerLifeList(perch_xy=PERCH)
    # update per sensor, like the runtime, so one person seen by two sensors is one track
    _, send, _ = run(eng, lambda t: [d for d in scene(t) if d.kind == "HEAT"], 50, life=life)
    t = 60.0
    send = None
    while t < 400 and send is None:
        for kind in ("HEAT", "PERSON", "IDENTITY"):
            ds = [d for d in scene(t) if d.kind == kind]
            if ds:
                life.update(ds, now=t)
        life.evaluate(t)
        eng.ingest(life)
        send = next((d for d in eng.tick(t, life) if d.action == SEND), None)
        t += 10.0
    assert send is not None and send.id_level == P.ID_FACE
    assert send.false_alarm == "LOW: face verified" and set(send.modalities) >= {"thermal", "vision"}


# ---------------------------------------------------------------------------------- not him
def test_patrol_walking_together_is_never_reported():
    eng = engine()

    def scene(t):
        base = np.array([-30.0 + 1.2 * t, 20.0])
        out = []
        if t % 10 == 0:
            for k in range(3):
                out.append(heat(t, base + [k * 3.0, 0.0], 0.9))
        if t % 30 == 0:
            out.append(sound(t, "VOICE", base))
        return out

    _, send, hist = run(eng, scene, 60, step=5.0)
    assert send is None
    assert all(d.p < 0.5 for _, ds in hist[-3:] for d in ds)
    assert any(c.key == "group" for d in hist[-1][1] for c in d.clues)


def test_warm_decoy_with_no_vital_signs_is_pulled_down():
    eng = engine()

    def scene(t):
        out = [heat(t, HIM, 0.8)] if t % 30 == 0 else []
        if t % 300 == 0 and t > 0:
            out.append(LifeDetection("VITALS", t, xy=HIM, cov=iso_cov(1.0), vitals=False,
                                     update_position=False, source="radar"))
        return out

    _, send, hist = run(eng, scene, 3600, step=30.0)
    assert send is None
    assert any(c.key == "no_vitals" for c in hist[-1][1][0].clues)


# ---------------------------------------------------------------------------------- EMCON
def _decided_scene(stranger_at=None):
    def scene(t):
        out = [heat(t, HIM, 0.8)] if t % 20 == 0 else []
        if stranger_at is not None and t % 20 == 0:
            out.append(heat(t, stranger_at, 0.3))
        if t in (100.0, 130.0, 160.0):
            out.append(code(t))
        return out
    return scene


def test_unknown_person_near_the_perch_holds_then_relocation_sends():
    eng = engine()
    eng.set_link(-9.0)                                   # minimum power: still hundreds of metres
    scene = _decided_scene(stranger_at=[-25.0, -10.0])
    life, send, hist = run(eng, scene, 600, stop_on_send=False)
    held = [d for t, ds in hist for d in ds if d.subject == "track:1" and t >= 160]
    assert held and all(d.action == HOLD for d in held)
    assert "intercept radius" in held[0].reason
    eng.mark_relocated(610.0)                            # autopilot: moved away from him
    _, send, _ = run(eng, scene, 700, life=life, t0=620.0)
    assert send is not None and send.subject == "track:1" and "relocated" in send.reason


def test_default_mode_sends_after_the_injured_hold_timeout_strict_never():
    for mode, expect in (("default", SEND), ("strict", HOLD)):
        eng = engine(mode=mode, emcon={"max_hold_injured_s": 300.0, "relocated_flag": None})
        eng.set_link(-9.0)
        if mode == "strict":
            eng.m["p_send"] = 0.95                        # same evidence for both modes
        _, send, hist = run(eng, _decided_scene(stranger_at=[-25.0, -10.0]), 900, stop_on_send=False)
        acts = [d.action for t, ds in hist for d in ds if d.subject == "track:1" and t > 160 + 300]
        assert acts and acts[0] == expect, mode


def test_briefed_threat_inside_intercept_radius_blocks_the_perch_burst():
    eng = engine(mission={"others_expected": 6, "search_area": AREA, "threat_dist_m": 230.0})
    eng.set_link(-9.0)                                   # ~300 m intercept radius under the kit's model
    assert eng.intercept_radius() > 230.0
    _, send, hist = run(eng, _decided_scene(), 400, stop_on_send=False)
    d = [d for t, ds in hist for d in ds if t >= 160][0]
    assert d.action == HOLD and "briefed threat" in d.reason


def test_duplicate_track_of_him_is_not_a_stranger():
    eng = engine()
    eng.set_link(-9.0)
    life = LedgerLifeList(perch_xy=PERCH)
    life.update([heat(0.0, HIM, 0.8), heat(0.0, HIM + [1.0, 0.0], 0.8)], now=0.0)   # one batch: two births
    eng.ingest(life)
    ds = eng.tick(2.0, life)
    assert len(life.tracks) == 2 and len(ds) == 1          # the duplicate feeds track 1's belief
    assert eng.alias == {2: 1}
    assert any(c.key == "alone" for c in ds[0].clues)
    assert eng._unknowns(eng.beliefs["track:1"]) == []


# ---------------------------------------------------------------------------------- after sending
def test_one_report_per_person_then_resend_only_when_he_moves():
    eng = engine()
    pos = {"xy": HIM.copy()}

    def scene(t):
        out = [heat(t, pos["xy"], 0.8)] if t % 20 == 0 else []
        if t in (100.0, 130.0, 160.0):
            out.append(code(t, pos["xy"]))
        return out

    life, send, _ = run(eng, scene, 400)
    eng.mark_sent(send.subject, send.t)
    _, again, hist = run(eng, scene, 300, life=life)
    assert again is None and hist[-1][1][0].action == REPORTED
    pos["xy"] = HIM + [30.0, 0.0]                         # he crawled 30 m (after the resend gap)
    for t in np.arange(500.0, 1500.0, 20.0):
        life.update([heat(t, pos["xy"] - [30.0 * max(0.0, 1500 - t) / 1000, 0.0], 0.8)], now=t)
        life.evaluate(t)
        eng.ingest(life)
        ds = eng.tick(t, life)
        if any(d.action == SEND for d in ds):
            break
    d = next(d for d in ds if d.subject == send.subject)
    assert d.action == SEND and d.resend and "moved" in d.reason


def test_strict_mode_reports_once_and_wipes():
    eng = engine(mode="strict")
    eng.m["p_send"] = 0.95
    life, send, _ = run(eng, _decided_scene(), 400)
    eng.mark_sent(send.subject, send.t)
    eng.wipe(send.subject)
    assert eng.beliefs[send.subject].reported["xy"] is None
    assert not eng.log_positions


def test_report_budget_per_hour():
    eng = engine(report={"max_per_hour": 1})
    life, send, _ = run(eng, _decided_scene(), 400)
    eng.mark_sent(send.subject, send.t)
    eng.beliefs[send.subject].reported = None            # pretend it is someone new
    ds = eng.tick(send.t + 10, life)
    assert ds[0].action == HOLD and "budget" in ds[0].reason


def test_training_mode_decides_but_never_transmits():
    eng = engine(mode="training")
    _, send, _ = run(eng, _decided_scene(), 400)
    assert send is not None and not eng.transmit_allowed


# ---------------------------------------------------------------------------------- bearing only
def test_bearing_only_code_reports_the_ray_after_a_stable_span():
    eng = engine(mission={"others_expected": 6, "search_area": None})
    far = np.array([60.0, 50.0])                          # heard, never seen
    times = [0.0, 40.0, 80.0] + [float(t) for t in range(120, 420, 60)]
    scene = lambda t: [code(t, far, point=False, az_err=2.0 * ((t // 40) % 2))] if t in times else []  # noqa: E731
    _, send, hist = run(eng, scene, 600)
    assert send is not None and send.subject.startswith("bearing:")
    assert send.t >= 300.0                                 # code confirmed, then 5 min on the same bearing
    assert send.ce_m == pytest.approx(75.0)
    assert abs(az_to(send.xy) - az_to(far)) < 3.0


def test_bearing_candidate_defers_to_a_track_on_that_bearing():
    eng = engine()
    scene = _decided_scene()
    life, send, _ = run(eng, scene, 400)
    assert send.subject == "track:1"
    eng.observe_bearing(code(500.0, HIM, point=False))
    ds = eng.tick(500.0, life)
    bear = [d for d in ds if d.subject.startswith("bearing:")]
    assert bear and bear[0].action == WATCH and "track on this bearing" in bear[0].reason


# ---------------------------------------------------------------------------------- inherit + log
def test_reborn_track_at_the_same_spot_keeps_its_evidence():
    eng = engine()
    life = LedgerLifeList(perch_xy=PERCH, death_s=60.0)
    for t in (0.0, 30.0):
        life.update([heat(t, HIM, 0.8)], now=t)
        life.update([code(t + 1.0)], now=t + 1.0)
        eng.ingest(life)
        eng.tick(t + 1.0, life)
    life.update([heat(200.0, [80.0, 80.0])], now=200.0)   # he is hidden; the old track dies
    eng.ingest(life)
    eng.tick(200.0, life)
    assert "track:1" not in eng.beliefs
    life.update([heat(260.0, HIM + [0.5, 0.0], 0.8)], now=260.0)
    eng.ingest(life)
    eng.tick(260.0, life)
    reborn = [b for b in eng.beliefs.values() if b.xy is not None and np.hypot(*(b.xy - HIM)) < 2.0]
    assert len(reborn) == 1 and reborn[0].track_id != 1 and reborn[0].code_best_n == 2


def test_decision_log_writes_changes_and_hides_positions_in_strict(tmp_path):
    eng = engine(mode="strict")
    _, _, hist = run(eng, _decided_scene(), 200, stop_on_send=False)
    path = tmp_path / "d.jsonl"
    log = DecisionLog(str(path), every_s=10.0, positions=eng.log_positions)
    for t, ds in hist:
        log.write(ds, t)
    recs = [json.loads(x) for x in path.read_text().splitlines()]
    assert recs and all("enu_m" not in r for r in recs)
    assert {r["action"] for r in recs} >= {WATCH}
