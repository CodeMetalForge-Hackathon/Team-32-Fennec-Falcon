"""Synthetic audio for tests and the demo: whistles, claps, multichannel rendering.

The renderer is exact for point sources in free field plus image sources (mirror
reflections): every path is applied as a fractional delay in the frequency domain with
1/r spreading and a reflection coefficient. Noise is independent per microphone and scaled
to a requested in-band SNR measured over the active (signal) samples.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .doa import SPEED_OF_SOUND, next_pow2


# ---- source signals -----------------------------------------------------------------
def whistle(fs: float, dur: float, f0: float = 2200.0, *, glide_hz: float = 150.0,
            vibrato_hz: float = 6.0, vibrato_cents: float = 30.0,
            harmonics: Sequence[float] = (1.0, 0.3, 0.1), attack_s: float = 0.03,
            rng: np.random.Generator | None = None) -> np.ndarray:
    """Human-like whistle: slight glide + vibrato + weak harmonics + a little breath noise."""
    rng = rng or np.random.default_rng(0)
    n = int(round(dur * fs))
    t = np.arange(n) / fs
    dev = f0 * (2.0 ** (vibrato_cents / 1200.0) - 1.0)
    finst = f0 + glide_hz * (t / max(dur, 1e-9) - 0.5) + dev * np.sin(2 * np.pi * vibrato_hz * t)
    phase = 2 * np.pi * np.cumsum(finst) / fs
    sig = np.zeros(n)
    for h, a in enumerate(harmonics, start=1):
        if h * (f0 + glide_hz) < 0.45 * fs:
            sig += a * np.sin(h * phase)
    sig *= 1.0 + 0.05 * rng.standard_normal(n).cumsum() / np.sqrt(np.arange(1, n + 1))  # slow AM
    na = max(1, int(attack_s * fs))
    env = np.ones(n)
    ramp = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, na))
    env[:na] = ramp
    env[-na:] = ramp[::-1]
    return sig * env


def clap(fs: float, *, dur: float = 0.06, decay_s: float = 0.007,
         rng: np.random.Generator | None = None) -> np.ndarray:
    """Hand clap / knock: exponentially decaying broadband noise burst."""
    rng = rng or np.random.default_rng(1)
    n = int(round(dur * fs))
    t = np.arange(n) / fs
    return rng.standard_normal(n) * np.exp(-t / decay_s)


@dataclass
class CodeSignal:
    signal: np.ndarray            # mono source waveform
    fs: float
    events: list                  # [(t0, t1)] seconds of each element
    name: str


def code_signal(fs: float, code: str = "three_whistles", *, t_start: float = 1.0,
                total_s: float = 7.0, f0: float = 2200.0,
                rng: np.random.Generator | None = None) -> CodeSignal:
    """Render a briefed code as a mono waveform.

    codes: 'three_whistles' (3 x 0.45 s, 1.2 s apart), 'long_short_short' (0.9/0.3/0.3 s),
    'taps3' (3 claps 0.45 s apart), 'tap_2_3' (tap-code groups 2 then 3).
    """
    rng = rng or np.random.default_rng(2)
    x = np.zeros(int(round(total_s * fs)))
    events = []

    def put(seg, t0):
        i0 = int(round(t0 * fs))
        x[i0:i0 + seg.size] += seg[: max(0, x.size - i0)]
        events.append((t0, t0 + seg.size / fs))

    if code == "three_whistles":
        for k in range(3):
            put(whistle(fs, 0.45, f0 * (1 + 0.02 * k), rng=rng), t_start + 1.2 * k)
    elif code == "long_short_short":
        t = t_start
        for d in (0.9, 0.3, 0.3):
            put(whistle(fs, d, f0, rng=rng), t)
            t += d + 0.35
    elif code == "taps3":
        for k in range(3):
            put(0.8 * clap(fs, rng=rng), t_start + 0.45 * k)
    elif code == "tap_2_3":
        t = t_start
        for group in (2, 3):
            for _ in range(group):
                put(0.8 * clap(fs, rng=rng), t)
                t += 0.25
            t += 0.9
    else:
        raise ValueError(f"unknown code {code!r}")
    return CodeSignal(x, fs, events, code)


# ---- geometry helpers ---------------------------------------------------------------
def image_source(src, normal, offset: float = 0.0) -> np.ndarray:
    """Mirror ``src`` across the plane n.x = offset (n need not be unit length)."""
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    s = np.asarray(src, float)
    return s - 2.0 * (s @ n - offset) * n


def mic_positions_world(mic_body, perch_xyz, yaw_deg: float = 0.0) -> np.ndarray:
    """Body-frame mic positions -> world ENU, for a level array rotated by ``yaw_deg``."""
    a = np.radians(yaw_deg)
    R = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
    return np.asarray(perch_xyz, float)[None, :] + np.asarray(mic_body, float) @ R.T


# ---- rendering ------------------------------------------------------------------------
def render(source: np.ndarray, fs: float, mics_world, paths, *, c: float = SPEED_OF_SOUND,
           n_out: int | None = None) -> np.ndarray:
    """Render ``source`` to every mic over ``paths`` = [(position(3,), gain), ...].

    Each path contributes gain/r * source(t - r/c); fractional delays are exact (FFT).
    """
    mics = np.asarray(mics_world, float)
    n_out = n_out or source.size
    dists = [np.linalg.norm(mics - np.asarray(p, float)[None, :], axis=1) for p, _ in paths]
    max_delay = max(float(d.max()) for d in dists) / c
    nfft = next_pow2(source.size + max_delay * fs + 64)
    S = np.fft.rfft(source, nfft)
    f = np.fft.rfftfreq(nfft, 1.0 / fs)
    Y = np.zeros((mics.shape[0], f.size), dtype=complex)
    for (pos, gain), d in zip(paths, dists):
        tau = d / c
        Y += (gain / np.maximum(d, 0.1))[:, None] * S[None, :] * np.exp(-2j * np.pi * f[None, :] * tau[:, None])
    return np.fft.irfft(Y, n=nfft, axis=1)[:, :n_out]


def band_noise(shape, fs: float, band=(100.0, 7000.0), rng: np.random.Generator | None = None):
    rng = rng or np.random.default_rng(3)
    n = shape[-1]
    X = np.fft.rfft(rng.standard_normal(shape), axis=-1)
    f = np.fft.rfftfreq(n, 1.0 / fs)
    X[..., (f < band[0]) | (f > min(band[1], 0.49 * fs))] = 0.0
    return np.fft.irfft(X, n=n, axis=-1)


def band_power(x: np.ndarray, fs: float, band=(300.0, 6000.0)) -> float:
    X = np.fft.rfft(x, axis=-1)
    f = np.fft.rfftfreq(x.shape[-1], 1.0 / fs)
    m = (f >= band[0]) & (f <= band[1])
    return float(np.sum(np.abs(X[..., m]) ** 2) / x.shape[-1] ** 2 * 2 / max(np.prod(x.shape[:-1]), 1))


def add_noise(audio: np.ndarray, fs: float, snr_db: float, active_segments=None,
              band=(300.0, 6000.0), rng: np.random.Generator | None = None) -> np.ndarray:
    """Add independent per-mic noise at ``snr_db`` (in ``band``, over the active samples)."""
    rng = rng or np.random.default_rng(4)
    if active_segments:
        sig = np.concatenate([audio[:, int(a * fs):int(b * fs)] for a, b in active_segments], axis=1)
    else:
        sig = audio
    p_sig = band_power(sig, fs, band)
    noise = band_noise(audio.shape, fs, rng=rng)
    p_noise = band_power(noise, fs, band)
    return audio + noise * np.sqrt(p_sig / (p_noise * 10 ** (snr_db / 10.0)))


def simulate_array_capture(mic_body, fs: float, perch_xyz, yaw_deg: float, src_xyz,
                           code: str = "three_whistles", *, snr_db: float = 15.0,
                           reflections: Sequence[tuple] = (), total_s: float = 7.0,
                           c: float = SPEED_OF_SOUND, seed: int = 0):
    """Full synthetic capture: briefed code from ``src_xyz`` heard by a perched array.

    ``reflections``: [(normal(3,), offset, coefficient)] mirror planes, e.g. the ground
    ((0,0,1), 0.0, 0.5) or a wall ((1,0,0), x_wall, 0.3).
    Returns (audio (M, N), CodeSignal).
    """
    rng = np.random.default_rng(seed)
    cs = code_signal(fs, code, total_s=total_s, rng=rng)
    mics = mic_positions_world(mic_body, perch_xyz, yaw_deg)
    paths = [(np.asarray(src_xyz, float), 1.0)]
    for normal, offset, coef in reflections:
        paths.append((image_source(src_xyz, normal, offset), coef))
    audio = render(cs.signal, fs, mics, paths, c=c)
    # signal is delayed by ~r/c: shift the "active" windows accordingly for the SNR measure
    delay = np.linalg.norm(np.asarray(src_xyz, float) - np.asarray(perch_xyz, float)) / c
    active = [(a + delay, b + delay) for a, b in cs.events]
    audio = add_noise(audio, fs, snr_db, active, rng=rng)
    cs.events = active
    return audio, cs
