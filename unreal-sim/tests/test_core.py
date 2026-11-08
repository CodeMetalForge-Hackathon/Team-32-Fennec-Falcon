import math
import socket
import xml.etree.ElementTree as ET

import pytest

from conftest import FakeEngine, make_cfg, scenario_for
from eagle_sim.core import cot, detect, flight, layout
from eagle_sim.core.mission import CONFIRM, HOLD, Mission


# ---------------------------------------------------------------- flight
def test_orbit_advances_by_arc_length():
    o = flight.Orbit(0, 0, 100, 0)
    for _ in range(100):
        o.advance(12.0, 0.1)             # 120 m of arc
    assert o.travelled == pytest.approx(1.2, rel=1e-6)
    assert o.laps == pytest.approx(1.2 / (2 * math.pi))


def test_bank_angle_matches_coordinated_turn():
    assert flight.bank_deg(12.0, 40.0) == pytest.approx(math.degrees(math.atan(144 / (9.81 * 40))))
    assert flight.bank_deg(12.0, 0) == 0.0


def test_step_toward_arrives_without_overshoot():
    x, y, arrived = flight.step_toward(0, 0, 3, 4, 10)
    assert (x, y, arrived) == (3, 4, True)
    x, y, arrived = flight.step_toward(0, 0, 30, 40, 5)
    assert arrived is False and math.hypot(x, y) == pytest.approx(5)


# ---------------------------------------------------------------- detection
def test_boson_640_32deg_detects_a_torso_to_about_172_m():
    r = detect.max_detect_range(0.45, 32.0, 640, 3.0)
    assert 165 < r < 180
    assert detect.pixels_on_target(r, 0.45, 32.0, 640) == pytest.approx(3.0)


def test_in_frame_center_and_outside():
    cam, look = (0, 0, 100), (100, 0, 0)
    assert detect.in_frame(cam, look, look, 32, 25.6)
    assert not detect.in_frame(cam, look, (100, 80, 0), 32, 25.6)    # far off to the side
    assert not detect.in_frame(cam, look, (-100, 0, 0), 32, 25.6)    # behind the camera


def test_view_accumulator_needs_spread_not_just_count():
    v = detect.ViewAccumulator(12)
    for _ in range(20):
        v.add(10.0)                       # lingering at one angle
    assert v.views == 1 and not v.confirmed(8, 4)
    for az in range(0, 360, 30):
        v.add(az + 5)
    assert v.confirmed(8, 4)


def test_glimpse_window_and_spacing():
    g = detect.GlimpseTracker(6.0, min_gap_s=1.5)
    assert g.add(0.0)
    assert not g.add(0.1)                 # same look, different frame: not a new glimpse
    assert g.add(5.0)
    assert g.count(5.0) == 2
    assert g.count(7.0) == 1


# ---------------------------------------------------------------- CoT
def test_cot_event_is_valid_xml_with_point_and_callsign():
    raw = cot.event("EAGLE-1", cot.EAGLE, 23.8, 121.1, 1100.0, callsign="EAGLE-1",
                    remarks="phase <SEARCH> & co")
    root = ET.fromstring(raw)
    assert root.tag == "event" and root.get("type") == "a-f-A-M-F-Q"
    pt = root.find("point")
    assert float(pt.get("lat")) == pytest.approx(23.8)
    assert root.find("detail/contact").get("callsign") == "EAGLE-1"
    assert "<SEARCH>" in root.find("detail/remarks").text


def test_cot_sender_delivers_over_udp_and_survives_bad_targets():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(2.0)
    port = rx.getsockname()[1]
    s = cot.CotSender([("127.0.0.1", port), ("256.256.256.256", 1)])
    assert s.send(b"<event/>") == 1
    data, _ = rx.recvfrom(4096)
    assert data == b"<event/>"
    assert s.errors                       # the bad target is reported, not raised
    s.close()
    rx.close()


# ---------------------------------------------------------------- layout
def test_layout_puts_cover_trees_close_and_keeps_the_person_clear():
    cfg = make_cfg()
    trees = layout.make_trees(cfg)
    ox, oy = cfg.OPERATOR_EN
    cover = [t for t in trees if t.cover]
    assert len(cover) == cfg.COVER_TREES
    assert all(4.0 <= math.hypot(t.x - ox, t.y - oy) <= 7.0 for t in cover)
    others = [t for t in trees if not t.cover]
    assert len(others) == cfg.TREES
    assert all(math.hypot(t.x - ox, t.y - oy) >= 9.0 for t in others)


# ---------------------------------------------------------------- end to end
def run(cfg, max_s=3000.0, dt=0.1):
    eng = FakeEngine(cfg)
    m = Mission(eng, scenario_for(cfg, eng), cfg)
    steps = 0
    while m.phase != HOLD and m.t < max_s:
        m.update(dt)
        steps += 1
    return m, eng


def phase_time(m, needle):
    for t, _, text in m.events:
        if needle in text:
            return t
    return None


def test_mission_detects_person_without_carried_equipment_and_holds():
    cfg = make_cfg()
    m, eng = run(cfg)
    assert m.phase == HOLD, m.events[-5:]
    confirmed = phase_time(m, "CONFIRMED")
    assert confirmed is not None and confirmed < 1500
    assert phase_time(m, "identity UNVERIFIED") is not None
    types = [t for t, _ in m.cot_sent]
    assert cot.UNKNOWN_GROUND in types and cot.EAGLE in types
    assert "a-f-G-U-C" not in types
    assert eng.labels["operator"] == "IP-1 PERSON DETECTED · ID UNVERIFIED"
    # every CoT payload parses
    for payload in eng.sent:
        ET.fromstring(payload)


def test_no_person_report_when_all_bird_sight_lines_are_blocked():
    cfg = make_cfg()
    eng = FakeEngine(cfg)
    eng.visible = lambda *args: False
    m = Mission(eng, scenario_for(cfg, eng), cfg)
    for _ in range(6000):
        m.update(0.1)
    assert m.phase != HOLD
    assert not any(kind == cot.UNKNOWN_GROUND for kind, _ in m.cot_sent)
    assert 'operator' not in eng.labels


def test_the_bird_stays_above_ground_and_never_teleports():
    cfg = make_cfg()
    eng = FakeEngine(cfg)
    m = Mission(eng, scenario_for(cfg, eng), cfg)
    last = None
    from conftest import ground
    while m.phase != HOLD and m.t < 3000:
        m.update(0.1)
        x, y, z = eng.eagle[:3]
        if last is not None:
            step = math.dist((x, y, z), last)
            assert step <= cfg.SPEED_MS * 0.1 * 1.6 + 1.0, (m.phase, step)
        assert z >= ground(x, y) - 0.5
        last = (x, y, z)


def test_confirm_needs_views_from_several_sides():
    cfg = make_cfg()
    m, _ = run(cfg)
    assert m.views is not None
    assert m.views.views >= cfg.CONFIRM_VIEWS and len(m.views.hit_bins) >= cfg.CONFIRM_BINS
