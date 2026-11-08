"""Breathing and heart rate from a 1-D radar signal, a serial bridge reader, and a simulator.

Input kinds for :func:`estimate_vitals` (sample rate 20-100 Hz, >= ~20 s of data):

* ``displacement`` chest displacement (any unit; metres or mm from a phase radar)
* ``phase``        radar phase in radians (wrapped or not; it is unwrapped here)
* ``iq``           complex baseband I + jQ (static clutter removed by a circle fit, then phase)
* ``doppler_if``   single-channel analog Doppler IF / ADC samples (``RADAR <adc>`` lines).
  It is cos(phase), not phase: breathing rate is usable, heart rate is NOT estimated.

Processing: detrend -> motion-artifact detection (1 s velocity RMS far above its median:
the person or the sensor moved) -> flagged stretches replaced by interpolation (too many:
status ``motion``) -> Hann-windowed, zero-padded spectrum -> spectral peak in the breathing
band 0.1-0.6 Hz (6-36 /min; a strong 2nd harmonic is folded back to its fundamental) and,
after notching breathing harmonics, in the heart band 0.8-2.5 Hz (48-150 bpm), each with
parabolic sub-bin interpolation. Quality: peak power over the white-noise floor measured in
a noise band (3-8 Hz) must reach ``min_breath_snr_db`` / ``min_heart_snr_db``, and the heart
peak must also stand ``min_heart_prom_db`` above the rest of its band. A static warm object
(a jacket with hand warmers) gives only noise: status ``no_vitals``.

Serial bridge lines (see firmware/radar_bridge/radar_bridge.ino)::

    VITALS <breath_bpm> <heart_bpm> <range_m> <presence 0|1>   (a vendor module's own estimate)
    RADAR <adc>                                                  (raw analog Doppler IF sample)
    PIR <0|1>                                                    (PIR output changed -> MOTION)
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

BREATH_BAND = (0.1, 0.6)
HEART_BAND = (0.8, 2.5)
C_LIGHT = 299_792_458.0


@dataclass
class VitalsResult:
    breath_bpm: float | None
    heart_bpm: float | None
    breath_snr_db: float
    heart_snr_db: float
    breath_conf: float               # 0..1
    heart_conf: float
    motion_frac: float               # fraction of samples flagged as body/sensor motion
    status: str                      # ok | no_vitals | motion | short
    note: str = ""

    @property
    def breathing(self) -> bool:
        return self.breath_bpm is not None

    @property
    def has_vitals(self) -> bool:
        return self.breath_bpm is not None or self.heart_bpm is not None

    def __str__(self) -> str:
        b = f"{self.breath_bpm:.1f}/min" if self.breath_bpm else "-"
        h = f"{self.heart_bpm:.0f} bpm" if self.heart_bpm else "-"
        return (f"{self.status}: breath {b} (snr {self.breath_snr_db:.0f} dB), heart {h} "
                f"(snr {self.heart_snr_db:.0f} dB), motion {100 * self.motion_frac:.0f}%"
                + (f" [{self.note}]" if self.note else ""))


def wavelength_m(freq_ghz: float) -> float:
    return C_LIGHT / (freq_ghz * 1e9)


def _circle_centre(z: np.ndarray) -> complex:
    """Algebraic (Kasa) circle fit to I/Q points -> centre (removes static clutter / DC)."""
    x, y = z.real, z.imag
    A = np.stack([x, y, np.ones_like(x)], axis=1)
    b = x * x + y * y
    c, *_ = np.linalg.lstsq(A, b, rcond=None)
    return complex(c[0] / 2, c[1] / 2)


def _to_displacement(x, kind: str, freq_ghz: float | None):
    if kind == "iq":
        z = np.asarray(x, dtype=complex)
        z0 = z - z.mean()
        c = _circle_centre(z)
        r = np.abs(z - c)
        R = float(np.mean(r))
        spread = float(np.sqrt(np.mean(np.abs(z0) ** 2)))
        if R <= 0 or np.std(r) / R > 0.25 or R > 20.0 * spread:
            # no clean arc: a static target (noise cloud) or a very short arc. A circle fit would
            # turn noise into a phase random walk, so demodulate linearly along the principal axis.
            _, vec = np.linalg.eigh(np.cov(np.stack([z0.real, z0.imag])))
            return z0.real * vec[0, 1] + z0.imag * vec[1, 1]
        ph = np.unwrap(np.angle(z - c))
        return ph * (wavelength_m(freq_ghz) / (4 * np.pi) if freq_ghz else 1.0)
    x = np.asarray(x, dtype=float)
    if kind == "phase":
        return np.unwrap(x) * (wavelength_m(freq_ghz) / (4 * np.pi) if freq_ghz else 1.0)
    if kind in ("displacement", "doppler_if"):
        return x
    raise ValueError(f"unknown radar signal kind {kind!r}")


def _detrend(x):
    t = np.arange(x.size, dtype=float)
    A = np.stack([np.ones_like(t), t], axis=1)
    c, *_ = np.linalg.lstsq(A, x, rcond=None)
    return x - A @ c


def _motion_mask(x, fs, k: float, win_s: float = 1.0, pad_s: float = 1.0):
    """True where the 1 s velocity RMS exceeds k x its median (dilated by pad_s)."""
    v = np.diff(x, prepend=x[0]) * fs
    n = max(2, int(win_s * fs))
    nw = x.size // n
    if nw < 4:
        return np.zeros(x.size, bool)
    rms = np.sqrt(np.mean(v[:nw * n].reshape(nw, n) ** 2, axis=1))
    base = np.median(rms)
    bad_w = rms > k * max(base, 1e-12)
    bad = np.repeat(bad_w, n)
    bad = np.concatenate([bad, np.full(x.size - bad.size, bad_w[-1])])
    pad = int(pad_s * fs)
    if pad and bad.any():
        idx = np.flatnonzero(bad)
        for s in (idx - pad, idx + pad):
            bad[np.clip(s, 0, x.size - 1)] = True
        kernel = np.ones(2 * pad + 1)
        bad = np.convolve(bad.astype(float), kernel, mode="same") > 0
    return bad


def _peak(f, P, band, exclude=None):
    """(freq, power, index) of the strongest bin in ``band`` (parabolic interpolation)."""
    m = (f >= band[0]) & (f <= band[1])
    if exclude is not None:
        m &= ~exclude
    if not m.any():
        return None, 0.0, None
    idx = np.flatnonzero(m)
    k = idx[np.argmax(P[idx])]
    fk, pk = f[k], P[k]
    if 0 < k < len(P) - 1 and P[k - 1] > 0 and P[k + 1] > 0:
        a, b, c = np.log(P[k - 1]), np.log(P[k]), np.log(P[k + 1])
        den = a - 2 * b + c
        if den < 0:
            off = float(np.clip(0.5 * (a - c) / den, -0.5, 0.5))
            fk = f[k] + off * (f[1] - f[0])
            pk = float(np.exp(b - 0.25 * (a - c) * off))
    return float(fk), float(pk), int(k)


def estimate_vitals(x, fs: float, kind: str = "displacement", *, freq_ghz: float | None = None,
                    min_duration_s: float = 15.0, motion_k: float = 4.0, max_motion_frac: float = 0.4,
                    min_breath_snr_db: float = 13.0, min_heart_snr_db: float = 10.0,
                    min_heart_prom_db: float = 6.0, breath_band=BREATH_BAND, heart_band=HEART_BAND,
                    min_breath_amp: float | None = None) -> VitalsResult:
    """Breathing / heart rate from one radar channel (see module docstring).

    ``min_breath_amp``: optional floor on the breathing amplitude (same unit as the
    displacement signal, e.g. 0.2e-3 for metres) to reject tiny periodic vibrations.
    """
    d = _to_displacement(x, kind, freq_ghz)
    fs = float(fs)
    if d.size < min_duration_s * fs:
        return VitalsResult(None, None, 0.0, 0.0, 0.0, 0.0, 0.0, "short",
                            f"{d.size / fs:.0f} s of data, need {min_duration_s:.0f} s")
    d = _detrend(d - np.mean(d))
    bad = _motion_mask(d, fs, motion_k)
    frac = float(bad.mean())
    if frac > max_motion_frac:
        return VitalsResult(None, None, 0.0, 0.0, 0.0, 0.0, frac, "motion", "body or sensor motion")
    if bad.any():                                    # bridge motion stretches by interpolation
        good = ~bad
        d = d.copy()
        d[bad] = np.interp(np.flatnonzero(bad), np.flatnonzero(good), d[good])
        d = _detrend(d)
    n = d.size
    w = np.hanning(n)
    nfft = 1 << int(np.ceil(np.log2(n * 8)))
    P = np.abs(np.fft.rfft(d * w, nfft)) ** 2
    f = np.fft.rfftfreq(nfft, 1.0 / fs)
    nyq = fs / 2
    nb = (3.0, min(8.0, 0.95 * nyq)) if nyq > 4.0 else (0.75 * nyq, 0.95 * nyq)
    noise = float(np.median(P[(f >= nb[0]) & (f <= nb[1])])) + 1e-300

    fb, pb, _ = _peak(f, P, breath_band)
    if fb is not None and fb * 0.5 >= breath_band[0]:
        fh, ph, _ = _peak(f, P, (0.5 * fb - 0.03, 0.5 * fb + 0.03))
        # a strong 2nd harmonic: take the fundamental (cos() of a single-channel IF can all but
        # remove the fundamental, so be more permissive there)
        if fh is not None and ph > (0.05 if kind == "doppler_if" else 0.25) * pb:
            fb, pb = fh, ph
    b_snr = 10 * np.log10(pb / noise) if fb else 0.0
    amp = 2.0 * np.sqrt(pb) / np.sum(w) if fb else 0.0
    breath = fb is not None and b_snr >= min_breath_snr_db and (min_breath_amp is None or amp >= min_breath_amp)
    out = VitalsResult(60.0 * fb if breath else None, None, float(b_snr), 0.0,
                       float(np.clip((b_snr - min_breath_snr_db) / 20.0, 0, 1)) if breath else 0.0, 0.0, frac,
                       "ok" if breath else "no_vitals")
    if kind == "doppler_if":
        out.note = "single-channel Doppler IF: heart rate not estimated"
        return out
    excl = np.zeros(f.size, bool)
    if breath:
        for k in range(2, 5):                        # notch the strong low breathing harmonics
            excl |= np.abs(f - k * fb) <= 0.04 + 0.03 * k * fb    # wider: rate wander smears them
    Pw = P * (f / heart_band[0]) ** 2                # acceleration weighting: flattens the breathing tail
    fhr, phw, khr = _peak(f, Pw, heart_band, excl)
    if fhr is not None and fhr / 2 >= heart_band[0]:
        fs2, ps2, _ = _peak(f, Pw, (fhr / 2 - 0.02, fhr / 2 + 0.02), excl)
        if fs2 is not None and ps2 > 0.3 * phw:      # peak was the heartbeat's 2nd harmonic
            fhr, phw = fs2, ps2
    if fhr is not None:
        m = (f >= heart_band[0]) & (f <= heart_band[1]) & ~excl & (np.abs(f - fhr) > 0.1)
        ph = phw / (fhr / heart_band[0]) ** 2
        h_snr = 10 * np.log10(ph / noise)
        prom = 10 * np.log10(phw / (np.median(Pw[m]) + 1e-300)) if m.any() else 0.0
        out.heart_snr_db = float(h_snr)
        if h_snr >= min_heart_snr_db and prom >= min_heart_prom_db and (breath or h_snr >= min_heart_snr_db + 6):
            out.heart_bpm = 60.0 * fhr
            out.heart_conf = float(np.clip((h_snr - min_heart_snr_db) / 20.0, 0, 1) * np.clip(prom / 15.0, 0, 1))
            out.status = "ok"
    return out


# ---- serial bridge ------------------------------------------------------------------------
@dataclass
class VitalsReading:
    """One ``VITALS`` line: a vendor module's own estimate (0 = unknown)."""
    breath_bpm: float
    heart_bpm: float
    range_m: float
    presence: bool
    t: float = 0.0


def parse_vitals_line(line: str):
    """-> ('vitals', VitalsReading) | ('radar', int) | ('pir', int) | None for anything else."""
    parts = line.strip().split()
    if not parts:
        return None
    try:
        if parts[0] == "VITALS" and len(parts) == 5:
            return ("vitals", VitalsReading(float(parts[1]), float(parts[2]), float(parts[3]),
                                            parts[4] not in ("0", "0.0"), time.time()))
        if parts[0] == "RADAR" and len(parts) == 2:
            return ("radar", int(float(parts[1])))
        if parts[0] == "PIR" and len(parts) == 2:
            return ("pir", int(float(parts[1])))
    except ValueError:
        return None
    return None


class VitalsSerialReader:
    """Reads the bridge's serial lines in a thread (pyserial). ``feed_line`` works without a port."""

    def __init__(self, port: str | None = None, baud: int = 115200, *, fs: float = 50.0,
                 window_s: float = 30.0, ser=None, log=print):
        self.fs, self.window_s = float(fs), float(window_s)
        self.samples: deque = deque(maxlen=int(fs * window_s * 2))
        self.last: VitalsReading | None = None
        self.last_motion_t: float | None = None          # last PIR 1
        self.log = log or (lambda *a: None)
        self.lines = 0
        self._stop = threading.Event()
        self.ser = ser
        if port is not None and ser is None:
            try:
                import serial  # type: ignore
            except ImportError as e:
                raise RuntimeError("pip install pyserial") from e
            self.ser = serial.Serial(port, baud, timeout=0.2)
        if self.ser is not None:
            threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:  # pragma: no cover - hardware
        while not self._stop.is_set():
            try:
                raw = self.ser.readline()
            except Exception as e:
                self.log(f"[vitals] serial error: {e}")
                time.sleep(0.5)
                continue
            if raw:
                self.feed_line(raw.decode("ascii", errors="replace"))

    def feed_line(self, line: str) -> None:
        p = parse_vitals_line(line)
        self.lines += 1
        if p is None:
            return
        if p[0] == "vitals":
            self.last = p[1]
        elif p[0] == "pir":
            if p[1]:
                self.last_motion_t = time.time()
        else:
            self.samples.append(p[1])

    def estimate(self, **kw) -> VitalsResult | None:
        """Breathing estimate from the buffered RADAR samples (None until ~window_s of data)."""
        n = int(self.window_s * self.fs)
        if len(self.samples) < n:
            return None
        x = np.asarray(list(self.samples)[-n:], dtype=float)
        return estimate_vitals(x, self.fs, "doppler_if", **kw)

    def close(self) -> None:
        self._stop.set()
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:  # pragma: no cover
                pass


# ---- simulation -----------------------------------------------------------------------------
def chest_displacement(fs: float, dur: float, breath_bpm: float | None = 12.0, heart_bpm: float | None = 70.0, *,
                       breath_mm: float = 5.0, heart_mm: float = 0.3, rate_jitter: float = 0.02,
                       motion_bursts: int = 0, motion_mm: float = 30.0, seed: int = 0) -> np.ndarray:
    """Chest-wall displacement in metres. ``breath_bpm=None`` and ``heart_bpm=None`` = a static
    object (a warm decoy). Breathing: asymmetric waveform (2nd/3rd harmonics 15 % / 5 %), slow
    rate wander; heartbeat with a 2nd harmonic; optional body-motion bursts (1-3 s shifts)."""
    rng = np.random.default_rng(seed)
    n = int(round(fs * dur))
    t = np.arange(n) / fs
    d = np.zeros(n)

    def wander(f0):
        slow = np.cumsum(rng.standard_normal(n)) / np.sqrt(fs * 30.0)
        slow = slow - np.linspace(slow[0], slow[-1], n)         # keep the mean rate exact
        return f0 * (1.0 + rate_jitter * np.clip(slow, -2, 2))

    if breath_bpm:
        ph = 2 * np.pi * np.cumsum(wander(breath_bpm / 60.0)) / fs + rng.uniform(0, 2 * np.pi)
        wave = np.sin(ph) + 0.15 * np.sin(2 * ph + 0.6) + 0.05 * np.sin(3 * ph + 1.1)
        d += 0.5 * breath_mm * 1e-3 * wave / np.max(np.abs(wave))
    if heart_bpm:
        ph = 2 * np.pi * np.cumsum(wander(heart_bpm / 60.0)) / fs + rng.uniform(0, 2 * np.pi)
        wave = np.sin(ph) + 0.3 * np.sin(2 * ph + 0.4)
        d += 0.5 * heart_mm * 1e-3 * wave / np.max(np.abs(wave))
    for _ in range(motion_bursts):
        t0, L = rng.uniform(0.1, 0.8) * dur, rng.uniform(1.0, 3.0)
        m = (t >= t0) & (t < t0 + L)
        s = (t[m] - t0) / L
        d[m] += motion_mm * 1e-3 * rng.uniform(0.5, 1.0) * np.sin(np.pi * s) * (1 + 0.5 * np.sin(2 * np.pi * 2.5 * s * L))
    return d


def radar_signal(disp_m: np.ndarray, kind: str = "phase", *, freq_ghz: float = 60.0, noise_mm: float = 0.05,
                 clutter: complex = 0.6 + 0.3j, seed: int = 1) -> np.ndarray:
    """What a CW radar at ``freq_ghz`` reports for chest displacement ``disp_m``.

    phase: 4*pi*d/lambda + phase noise, wrapped to (-pi, pi]; iq: exp(j*phase) + static
    clutter + noise; displacement: d + noise (m); doppler_if: 10-bit ADC of cos(phase).
    """
    rng = np.random.default_rng(seed)
    lam = wavelength_m(freq_ghz)
    ph = 4 * np.pi * disp_m / lam + rng.uniform(0, 2 * np.pi)
    sig_ph = 4 * np.pi * noise_mm * 1e-3 / lam
    if kind == "displacement":
        return disp_m + rng.normal(0, noise_mm * 1e-3, disp_m.size)
    if kind == "phase":
        return np.angle(np.exp(1j * (ph + rng.normal(0, sig_ph, disp_m.size))))
    if kind == "iq":
        z = np.exp(1j * ph) + clutter
        return z + (rng.normal(0, sig_ph / 1.4, disp_m.size) + 1j * rng.normal(0, sig_ph / 1.4, disp_m.size))
    if kind == "doppler_if":
        x = 512 + 300 * np.cos(ph) + rng.normal(0, 300 * sig_ph, disp_m.size)
        return np.clip(np.round(x), 0, 1023)
    raise ValueError(kind)
