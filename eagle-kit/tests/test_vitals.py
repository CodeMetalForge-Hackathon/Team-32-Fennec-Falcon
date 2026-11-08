"""Radar vital signs: rates, static decoy, motion, serial bridge lines."""
import numpy as np
import pytest

from eagle.vitals import (VitalsSerialReader, chest_displacement, estimate_vitals, parse_vitals_line,
                          radar_signal)


def _hr_for(br, rng):
    hr = rng.uniform(50, 120)
    while any(abs(hr / 60 - k * br / 60) < 0.08 for k in (2, 3, 4)):    # off the breathing harmonics
        hr = rng.uniform(50, 120)
    return hr


@pytest.mark.parametrize("kind,ghz,fs", [("phase", 60.0, 20), ("phase", 24.0, 50), ("iq", 24.0, 100),
                                         ("displacement", 60.0, 50)])
def test_rates_at_good_snr(kind, ghz, fs):
    rng = np.random.default_rng(int(ghz) + fs)
    for k, br in enumerate((6, 9, 12, 15, 18, 21, 24, 27, 30)):
        hr = _hr_for(br, rng)
        d = chest_displacement(fs, 40, br, hr, seed=k)
        r = estimate_vitals(radar_signal(d, kind, freq_ghz=ghz, seed=k + 50), fs, kind, freq_ghz=ghz)
        assert r.status == "ok" and abs(r.breath_bpm - br) <= 1.0, (br, str(r))
        assert r.heart_bpm is not None and abs(r.heart_bpm - hr) <= 3.0, (hr, str(r))


@pytest.mark.parametrize("kind", ["phase", "iq", "displacement", "doppler_if"])
def test_static_decoy_has_no_vital_signs(kind):
    for s in range(15):
        d = chest_displacement(50, 40, None, None, seed=s)          # jacket with hand warmers
        r = estimate_vitals(radar_signal(d, kind, freq_ghz=24.0, seed=s), 50, kind, freq_ghz=24.0)
        assert not r.has_vitals and r.status == "no_vitals", str(r)


def test_motion_artifacts():
    d = chest_displacement(50, 40, 12, 70, motion_bursts=1, seed=4)
    r = estimate_vitals(radar_signal(d, "phase", freq_ghz=60.0), 50, "phase", freq_ghz=60.0)
    assert r.motion_frac > 0.05 and abs(r.breath_bpm - 12) <= 1 and abs(r.heart_bpm - 70) <= 3
    d = chest_displacement(50, 40, 12, 70, motion_bursts=4, seed=6)
    assert estimate_vitals(radar_signal(d, "phase", freq_ghz=60.0), 50, "phase", freq_ghz=60.0).status == "motion"
    assert estimate_vitals(np.zeros(100), 50).status == "short"


def test_doppler_if_breathing_and_serial_reader():
    rd = VitalsSerialReader(fs=50, window_s=30)
    d = chest_displacement(50, 32, 14, 70, seed=2)
    for v in radar_signal(d, "doppler_if", freq_ghz=24.0, seed=2):
        rd.feed_line(f"RADAR {int(v)}\n")
    rd.feed_line("VITALS 13.0 72.0 0.9 1")
    rd.feed_line("garbage line")
    r = rd.estimate()
    assert r is not None and abs(r.breath_bpm - 14) <= 1 and r.heart_bpm is None
    assert rd.last.breath_bpm == 13 and rd.last.presence and rd.last.range_m == pytest.approx(0.9)
    assert parse_vitals_line("RADAR 512") == ("radar", 512)
    assert parse_vitals_line("VITALS 0 0 0 0")[1].presence is False
    assert parse_vitals_line("VITALS x") is None and parse_vitals_line("") is None
    assert parse_vitals_line("PIR 1") == ("pir", 1)
    rd.feed_line("PIR 1")
    assert rd.last_motion_t is not None
