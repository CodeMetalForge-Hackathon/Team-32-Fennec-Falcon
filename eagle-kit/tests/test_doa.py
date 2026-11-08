import numpy as np
import pytest

from eagle.config import array_geometry
from eagle.doa import (SrpPhat, gcc_phat, speed_of_sound, srp_phat, unit_vector,
                       vector_to_az_el, wrap180)
from eagle.sim import band_noise, simulate_array_capture

PRESETS = ["respeaker_usb_v2", "respeaker_6mic_hat", "minidsp_uma16_v2", "diy_tetra_4", "diy_cross_4"]
DIRECTIONS = [(37.0, -25.0), (205.0, -40.0), (300.0, -15.0)]


def _delayed(x, d, fs):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(x.size, 1 / fs)
    return np.fft.irfft(X * np.exp(-2j * np.pi * f * d / fs), n=x.size)


@pytest.mark.parametrize("delay", [7.0, -5.0, 0.0])
def test_gcc_phat_integer_delay(delay):
    fs = 16000
    x = band_noise((4096,), fs, rng=np.random.default_rng(1))
    y = np.roll(x, int(delay))
    r = gcc_phat(y, x, fs, max_tau=20 / fs)
    assert abs(r.delay_samples - delay) < 0.02
    assert r.peak > 0.9


@pytest.mark.parametrize("delay", [3.37, -2.61, 0.5])
def test_gcc_phat_fractional_delay(delay):
    fs = 48000
    x = band_noise((8192,), fs, rng=np.random.default_rng(2))
    r = gcc_phat(_delayed(x, delay, fs), x, fs, max_tau=10 / fs)
    assert abs(r.delay_samples - delay) < 0.05


def test_gcc_phat_sign_convention():
    """tau > 0 means the first argument arrives later."""
    fs = 16000
    x = band_noise((4096,), fs, rng=np.random.default_rng(3))
    assert gcc_phat(np.roll(x, 4), x, fs).tau > 0
    assert gcc_phat(x, np.roll(x, 4), fs).tau < 0


def test_unit_vector_roundtrip():
    for az, el in [(0, 0), (90, -30), (271.5, 12.0), (180, -89)]:
        a, e = vector_to_az_el(unit_vector(az, el))
        assert abs(wrap180(a - az)) < 1e-9 and abs(e - el) < 1e-9


@pytest.mark.parametrize("fs", [16000, 48000])
@pytest.mark.parametrize("preset", PRESETS)
def test_srp_phat_presets_whistle(cfg, preset, fs):
    geom = array_geometry(cfg, preset, fs=fs)
    srp = SrpPhat(geom.positions, fs, hemisphere="lower")
    for k, (az, el) in enumerate(DIRECTIONS):
        src = 20.0 * unit_vector(az, el)
        audio, cs = simulate_array_capture(geom.positions, fs, [0, 0, 0], 0.0, src, "three_whistles",
                                           snr_db=15, total_s=5.0, seed=10 + k)
        segs = [(int(a * fs), int(b * fs)) for a, b in cs.events]
        res = srp.localize(audio, segs)
        assert abs(wrap180(res.az - az)) <= 3.0, (preset, fs, az, el, res)
        assert abs(res.el - el) <= 4.0, (preset, fs, az, el, res)
        assert res.confidence > 0.5


@pytest.mark.parametrize("preset", ["respeaker_usb_v2", "minidsp_uma16_v2"])
def test_srp_phat_claps_with_reflections(cfg, preset):
    geom = array_geometry(cfg, preset)
    fs = geom.fs
    perch, src = np.array([0, 0, 8.0]), np.array([12.0, -9.0, 0.5])
    audio, cs = simulate_array_capture(geom.positions, fs, perch, 0.0, src, "taps3", snr_db=20,
                                       reflections=[((0, 0, 1), 0.0, 0.5)], seed=4)
    segs = [(int((a - 0.004) * fs), int((a + 0.05) * fs)) for a, _ in cs.events]
    res = srp_phat(audio, fs, geom.positions, segs, hemisphere="lower")
    az, el = vector_to_az_el(src - perch)
    assert abs(wrap180(res.az - az)) <= 3.0
    assert abs(res.el - el) <= 4.0


def test_hemisphere_lower_resolves_planar_mirror(cfg):
    geom = array_geometry(cfg, "respeaker_usb_v2")
    fs = geom.fs
    az, el = 120.0, -12.0
    audio, cs = simulate_array_capture(geom.positions, fs, [0, 0, 0], 0.0, 20 * unit_vector(az, el),
                                       "three_whistles", snr_db=20, total_s=5.0, seed=5)
    segs = [(int(a * fs), int(b * fs)) for a, b in cs.events]
    both = SrpPhat(geom.positions, fs, hemisphere=None).localize(audio, segs)
    assert SrpPhat(geom.positions, fs).planar
    assert abs(abs(both.el) - abs(el)) <= 4.0           # sign is ambiguous for a planar array
    lower = SrpPhat(geom.positions, fs, hemisphere="lower").localize(audio, segs)
    assert lower.el < 0 and abs(lower.el - el) <= 4.0
    assert abs(wrap180(lower.az - az)) <= 3.0


def test_tetrahedron_resolves_elevation_sign_without_hemisphere(cfg):
    geom = array_geometry(cfg, "diy_tetra_4", fs=48000)
    srp = SrpPhat(geom.positions, 48000, el_range=(-90, 90))
    assert not srp.planar
    for el in (-20.0, 20.0):
        audio, cs = simulate_array_capture(geom.positions, 48000, [0, 0, 0], 0.0, 20 * unit_vector(70, el),
                                           "three_whistles", snr_db=20, total_s=5.0, seed=6)
        segs = [(int(a * 48000), int(b * 48000)) for a, b in cs.events]
        res = srp.localize(audio, segs)
        assert abs(res.el - el) <= 4.0


def test_noise_only_has_low_confidence(cfg):
    geom = array_geometry(cfg, "respeaker_usb_v2")
    noise = band_noise((4, 16000 * 3), 16000, rng=np.random.default_rng(7))
    res = SrpPhat(geom.positions, 16000, hemisphere="lower").localize(noise)
    assert res.confidence < 0.35
    assert res.peak_ratio < 2.0


def test_bad_geometry_rejected():
    with pytest.raises(ValueError):
        SrpPhat(np.zeros((2, 3)), 16000)


def test_speed_of_sound():
    assert abs(speed_of_sound(20.0) - 343.2) < 0.2


def test_cross_check_with_pyroomacoustics_room(cfg):
    """Independent check in a reverberant room rendered by pyroomacoustics (if installed):
    azimuth matches the truth and pyroomacoustics' own SRP; onset-only frames reduce the
    reverberation bias of planar-array elevation (see doa.onset_s in the config)."""
    pra = pytest.importorskip("pyroomacoustics")
    from eagle.detect import detect_whistles
    from eagle.sim import code_signal
    geom = array_geometry(cfg, "respeaker_6mic_hat")
    fs = 16000
    room = pra.ShoeBox([30, 25, 15], fs=fs, materials=pra.Material(0.5), max_order=8)
    centre, src = np.array([8.0, 6.0, 8.0]), np.array([20.0, 16.0, 1.0])
    rng = np.random.default_rng(8)
    room.add_source(src, signal=code_signal(fs, "three_whistles", total_s=5.0, rng=rng).signal)
    room.add_microphone_array((centre[None, :] + geom.positions).T)
    room.simulate()
    audio = room.mic_array.signals
    audio = audio + 0.03 * np.std(audio) * rng.standard_normal(audio.shape)
    events = detect_whistles(audio, fs)
    assert len(events) == 3
    srp = SrpPhat(geom.positions, fs, hemisphere="lower")
    full = srp.localize(audio, [(e.start, e.end) for e in events])
    onset = srp.localize(audio, [(e.start - 320, e.start + int(0.1 * fs)) for e in events])
    az, el = vector_to_az_el(src - centre)
    assert abs(wrap180(full.az - az)) <= 4.0 and abs(wrap180(onset.az - az)) <= 4.0
    assert abs(onset.el - el) <= 5.0
    assert abs(onset.el - el) < abs(full.el - el)
    X = np.array([pra.transform.stft.analysis(s_, 1024, 512).T for s_ in audio])
    doa = pra.doa.algorithms["SRP"](geom.positions.T, fs, 1024, c=343.0, num_src=1,
                                    azimuth=np.radians(np.arange(360)))
    doa.locate_sources(X, freq_range=[800, 4000])
    # pyroomacoustics searches azimuth in the horizontal plane only (source is ~25 deg below),
    # so allow it a little more slack; both must land near the truth
    assert abs(wrap180(np.degrees(doa.azimuth_recon[0]) - az)) <= 6.0
