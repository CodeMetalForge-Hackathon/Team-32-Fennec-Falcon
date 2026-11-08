import numpy as np
import pytest

from eagle.config import array_geometry
from eagle.detect import (CodeSpec, Event, detect_energy, detect_taps, detect_whistles, match_codes,
                          tap_code_letter)
from eagle.sim import band_noise, simulate_array_capture, whistle

FS = 16000


def capture(cfg, code, snr=12, fs=FS, seed=3):
    geom = array_geometry(cfg, "respeaker_usb_v2", fs=fs)
    return simulate_array_capture(geom.positions, fs, [0, 0, 8], 0.0, [14, 9, 0.5], code, snr_db=snr,
                                  reflections=[((0, 0, 1), 0.0, 0.5)], seed=seed)


@pytest.mark.parametrize("fs", [16000, 48000])
@pytest.mark.parametrize("snr", [15, 5, 0])
def test_three_whistles_found_and_matched(cfg, fs, snr):
    audio, cs = capture(cfg, "three_whistles", snr, fs)
    ev = detect_whistles(audio, fs)
    assert len(ev) == 3
    for e, (t0, t1) in zip(ev, cs.events):          # sample indices bracket the true whistles
        assert abs(e.t0 - t0) < 0.06 and abs(e.t1 - t1) < 0.06
        assert 1800 < e.f0_hz < 2800
    m = match_codes(ev, [CodeSpec("three_whistles", "whistle", count=3, within_s=5.0)])
    assert len(m) == 1 and len(m[0].segments) == 3


@pytest.mark.parametrize("fs", [16000, 48000])
def test_noise_only_is_rejected(fs):
    noise = band_noise((4, fs * 10), fs, rng=np.random.default_rng(5)) * 0.01
    assert detect_whistles(noise, fs) == []
    assert detect_taps(noise, fs) == []
    assert detect_energy(noise, fs) == []
    assert match_codes([], [CodeSpec("x", count=3)]) == []


def test_short_chirp_is_not_a_whistle():
    rng = np.random.default_rng(1)
    x = band_noise((1, FS * 3), FS, rng=rng) * 0.01
    chirp = whistle(FS, 0.08, 2500, rng=rng)
    x[0, FS:FS + chirp.size] += 0.2 * chirp           # a bird "tweet": loud but too short
    assert detect_whistles(x, FS) == []


def test_taps_found(cfg):
    audio, cs = capture(cfg, "taps3", 15)
    ev = detect_taps(audio, FS)
    assert len(ev) == 3
    for e, (t0, _) in zip(ev, cs.events):
        assert e.start / FS <= t0 + 0.01 <= e.end / FS        # onset is inside the DOA segment
    assert detect_whistles(audio, FS) == []
    m = match_codes(ev, [CodeSpec("three_taps", "tap", count=3, within_s=3.0)])
    assert len(m) == 1


def test_whistles_are_not_taps(cfg):
    audio, _ = capture(cfg, "three_whistles", 15)
    assert detect_taps(audio, FS) == []


def test_long_short_short_rhythm(cfg):
    audio, _ = capture(cfg, "long_short_short", 10)
    ev = detect_whistles(audio, FS)
    assert [round(e.duration, 1) >= 0.6 for e in ev] == [True, False, False]
    m = match_codes(ev, [CodeSpec("lss", "whistle", rhythm="LSS")])
    assert len(m) == 1
    assert match_codes(ev, [CodeSpec("ssl", "whistle", rhythm="SSL")]) == []


def test_tap_code_groups(cfg):
    audio, _ = capture(cfg, "tap_2_3", 15)
    ev = detect_taps(audio, FS)
    assert len(ev) == 5
    m = match_codes(ev, [CodeSpec("tc", "tap", groups=[2, 3], group_gap_s=0.6, max_gap_s=2.0)])
    assert len(m) == 1 and len(m[0].events) == 5
    assert match_codes(ev, [CodeSpec("tc", "tap", groups=[3, 2])]) == []
    assert tap_code_letter(2, 3) == "H" and tap_code_letter(6, 1) is None


def _ev(t0, t1, kind="whistle"):
    return Event(kind, int(t0 * FS), int(t1 * FS), FS)


def test_count_window_and_tolerance():
    spec = CodeSpec("w3", "whistle", count=3, within_s=5.0, tolerance=0.1)
    ok = [_ev(0, 0.4), _ev(2, 2.4), _ev(5.0, 5.4)]          # span 5.4 s: inside 5 s + 10 %
    too_slow = [_ev(0, 0.4), _ev(3, 3.4), _ev(6.0, 6.4)]
    assert len(match_codes(ok, [spec])) == 1
    assert match_codes(too_slow, [spec]) == []
    assert match_codes(ok[:2], [spec]) == []


def test_exact_count_rejects_extra_events():
    spec = CodeSpec("w3", "whistle", count=3, within_s=5.0, exact=True, max_gap_s=1.5)
    four = [_ev(0, 0.4), _ev(1, 1.4), _ev(2, 2.4), _ev(3, 3.4)]
    assert match_codes(four, [spec]) == []
    assert len(match_codes(four[:3], [spec])) == 1


def test_kinds_do_not_mix():
    ev = [_ev(0, 0.4), _ev(1, 1.05, "tap"), _ev(2, 2.4)]
    assert match_codes(ev, [CodeSpec("w3", "whistle", count=3)]) == []
