"""Life list: association, statuses, RF rings, track lifecycle."""
import numpy as np

from eagle import protocol as P
from eagle.fusion import LifeDetection, LifeList, iso_cov

P1, P2, P3 = np.array([2.0, 6.0]), np.array([-2.5, 4.5]), np.array([1.5, 9.0])


def heat(xy, t, rng, s=0.25):
    return LifeDetection("HEAT", t, xy=np.asarray(xy) + rng.normal(0, 0.1, 2), cov=iso_cov(s))


def az(xy):
    return float(np.degrees(np.arctan2(xy[1], xy[0])))


def scene():
    rng = np.random.default_rng(0)
    life = LifeList(perch_xy=(0, 0))
    for t in (0.0, 10.0, 20.0, 30.0, 40.0):
        life.update([heat(p, t, rng) for p in (P1, P2, P3)], now=t)
    # P1 whistles (point fix, a bit long in range) and then talks (bearing only)
    life.update([LifeDetection("SOUND", 12.0, xy=P1 + [0.3, 0.8], cov=np.diag([0.3, 0.8]) ** 2, origin=np.zeros(2),
                               az_deg=az(P1), category="SIGNAL")], now=12.0)
    life.update([LifeDetection("SOUND", 15.0, origin=np.zeros(2), az_deg=az(P1) + 1.0, category="VOICE")], now=15.0)
    # radar pellets: one 1.3 m from P2 (breathing), one ~1 m from P3 (nothing)
    life.update([LifeDetection("VITALS", 45.0, xy=P2 + [0.9, -0.9], cov=iso_cov(1.0), breath_bpm=10, heart_bpm=62,
                               vitals=True, update_position=False),
                 LifeDetection("VITALS", 45.0, xy=P3 + [-0.9, -0.6], cov=iso_cov(1.0), vitals=False,
                               update_position=False)], now=45.0)
    return life


def track_at(life, xy):
    return min(life.tracks, key=lambda t: np.linalg.norm(t.x - xy))


def test_multi_person_association_and_statuses():
    life = scene()
    life.evaluate(60.0)
    assert len(life.tracks) == 3 and not life.unassociated
    t1, t2, t3 = (track_at(life, p) for p in (P1, P2, P3))
    assert len({t1.id, t2.id, t3.id}) == 3
    assert t1.status == P.LS_RESPONSIVE and t2.status == P.LS_BREATHING and t3.status == P.LS_WARM_ONLY
    assert t2.breath_bpm == 10 and t2.heart_bpm == 62
    for t, p in ((t1, P1), (t2, P2), (t3, P3)):
        assert np.linalg.norm(t.x - p) < 0.3                          # pellets did not drag positions
    assert t3.evidence(60.0, 300) & P.EV_NOVITALS and t2.evidence(60.0, 300) & P.EV_BREATH
    ranked = life.ranked(60.0)
    assert ranked[0].id == t2.id                                      # unresponsive breather first
    assert all(0.0 <= t.score <= 1.0 for t in life.tracks) and t1.score > t2.score > t3.score


def test_warm_only_waits_for_timeout_without_radar():
    life = LifeList(perch_xy=(0, 0), warm_timeout_s=30)
    life.update([LifeDetection("HEAT", 0.0, xy=P1, cov=iso_cov(0.3))], now=0.0)
    assert life.evaluate(10.0)[0].status == P.LS_UNKNOWN
    assert life.evaluate(31.0)[0].status == P.LS_WARM_ONLY


def test_ambiguous_bearing_and_rf_rings():
    life = LifeList(perch_xy=(0, 0))
    a, b = np.array([3.0, 6.0]), np.array([4.5, 9.0])                  # same bearing, different range
    life.update([LifeDetection("HEAT", 0.0, xy=a, cov=iso_cov(0.2)), LifeDetection("HEAT", 0.0, xy=b, cov=iso_cov(0.2)),
                 LifeDetection("HEAT", 0.0, xy=P2, cov=iso_cov(0.2))], now=0.0)
    life.update([LifeDetection("SOUND", 5.0, origin=np.zeros(2), az_deg=az(a), category="VOICE")], now=5.0)
    assert life.unassociated.get("SOUND") == 1 and all(not t.sounds for t in life.tracks)
    # RF ring through exactly one track -> associated; a ring through nothing -> RF_ONLY entry
    r2 = float(np.hypot(*P2))
    life.update([LifeDetection("RF", 6.0, origin=np.zeros(2), range_m=r2, sigma_range_m=0.3, rf_id="TAG-7")], now=6.0)
    assert "TAG-7" in track_at(life, P2).rf_ids
    life.update([LifeDetection("RF", 7.0, origin=np.zeros(2), range_m=30.0, sigma_range_m=3.0, rf_id="RADIO-2")],
                now=7.0)
    rf_only = [t for t in life.tracks if t.rf_only]
    assert len(rf_only) == 1 and life.evaluate(8.0) and rf_only[0].status == P.LS_RF_ONLY
    assert rf_only[0].ce_m == 30.0 and np.allclose(rf_only[0].x, [0, 0])


def test_birth_limits_and_death():
    life = LifeList(perch_xy=(0, 0), max_tracks=3, death_s=100)
    life.update([LifeDetection("HEAT", 0.0, xy=np.array([k * 3.0, 5.0]), cov=iso_cov(0.2)) for k in range(5)], now=0.0)
    assert len(life.tracks) == 3 and life.dropped_births == 2
    life.update([LifeDetection("VITALS", 1.0, xy=np.array([50.0, 50.0]), cov=iso_cov(1.0), vitals=True)], now=1.0)
    assert len(life.tracks) == 3 and life.unassociated["VITALS"] == 1  # no birth from radar alone
    life.update([LifeDetection("HEAT", 150.0, xy=np.array([0.0, 5.0]), cov=iso_cov(0.2))], now=150.0)
    assert len(life.tracks) == 1


def test_life_persons_records():
    life = scene()
    recs = life.life_persons(60.0)
    assert [r.status for r in recs][0] == P.LS_BREATHING
    r2 = next(r for r in recs if r.status == P.LS_BREATHING)
    assert r2.breath_bpm == 10 and r2.heart_bpm == 62 and abs(r2.east_m - P2[0]) < 0.3
