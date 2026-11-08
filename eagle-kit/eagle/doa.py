"""Direction of arrival (DOA): GCC-PHAT for a mic pair and SRP-PHAT for any array.

Frame and angle conventions (used everywhere in Eagle)
------------------------------------------------------
* Body frame of the array: x forward (the array's 0 deg mark), y left, z up; metres.
* Azimuth ``az``: degrees counter-clockwise from +x towards +y, wrapped to [0, 360).
* Elevation ``el``: degrees above the x-y plane; negative = below (a perched array looks down).
* Unit vector towards the source: ``u = [cos(el)cos(az), cos(el)sin(az), sin(el)]``.
* Far-field time difference of arrival of mic j relative to mic i:
  ``tau_ij = (p_i - p_j) . u / c``   (positive: mic j hears the wavefront later).

Planar arrays (all mics in one plane, e.g. ReSpeaker, UMA-16) give identical delays for
``el`` and ``-el``: pass ``hemisphere='lower'`` when the array is level and looking down.
Their elevation accuracy also degrades near the horizon (delays scale with cos(el)).
A non-planar array (e.g. the DIY tetrahedron preset) resolves elevation directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

SPEED_OF_SOUND = 343.0  # m/s at about 20 C


def speed_of_sound(temp_c: float = 20.0) -> float:
    """Speed of sound in dry air, m/s (331.3 * sqrt(1 + T/273.15))."""
    return float(331.3 * np.sqrt(1.0 + temp_c / 273.15))


def next_pow2(n: float) -> int:
    return 1 << int(np.ceil(np.log2(max(int(np.ceil(n)), 1))))


def unit_vector(az_deg, el_deg) -> np.ndarray:
    """Unit vector(s) for azimuth/elevation in degrees; shape (..., 3)."""
    az = np.radians(np.asarray(az_deg, dtype=float))
    el = np.radians(np.asarray(el_deg, dtype=float))
    return np.stack([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)], axis=-1)


def vector_to_az_el(v) -> tuple:
    """Inverse of :func:`unit_vector` (vector need not be normalised)."""
    v = np.asarray(v, dtype=float)
    x, y, z = v[..., 0], v[..., 1], v[..., 2]
    az = np.degrees(np.arctan2(y, x)) % 360.0
    el = np.degrees(np.arctan2(z, np.hypot(x, y)))
    if np.ndim(az) == 0:
        return float(az), float(el)
    return az, el


def angular_distance_deg(az1, el1, az2, el2):
    """Great-circle angle between two directions, degrees."""
    d = np.sum(unit_vector(az1, el1) * unit_vector(az2, el2), axis=-1)
    return np.degrees(np.arccos(np.clip(d, -1.0, 1.0)))


def wrap180(a):
    """Wrap an angle (or difference) to [-180, 180)."""
    return (np.asarray(a, dtype=float) + 180.0) % 360.0 - 180.0


def _parabolic_offset(y: np.ndarray, k: int) -> float:
    """Sub-sample offset of a peak at index k from a 3-point parabola fit."""
    if 0 < k < len(y) - 1:
        y0, y1, y2 = float(y[k - 1]), float(y[k]), float(y[k + 1])
        denom = y0 - 2.0 * y1 + y2
        if denom < 0.0:
            return float(np.clip(0.5 * (y0 - y2) / denom, -0.5, 0.5))
    return 0.0


# --------------------------------------------------------------------------------------
# GCC-PHAT for one pair
# --------------------------------------------------------------------------------------
@dataclass
class GccResult:
    tau: float              # seconds; > 0 means `sig` arrives later than `ref`
    delay_samples: float    # tau * fs
    peak: float             # normalised correlation at the peak (1.0 = perfectly coherent)
    lags: np.ndarray = field(repr=False)  # seconds
    cc: np.ndarray = field(repr=False)


def gcc_phat(sig, ref, fs: float, *, max_tau: float | None = None,
             band: tuple | None = (300.0, 6000.0), interp: int = 16, beta: float = 1.0) -> GccResult:
    """Band-limited GCC-PHAT delay estimate with sub-sample peak interpolation.

    The cross-spectrum is whitened (``|R|**beta``, beta=1 is classic PHAT), restricted to
    ``band`` Hz, inverse-transformed with ``interp``-times zero padding (sinc upsampling)
    and the peak is refined with a 3-point parabola.
    """
    sig = np.asarray(sig, dtype=float)
    ref = np.asarray(ref, dtype=float)
    nfft = next_pow2(sig.size + ref.size)
    R = np.fft.rfft(sig, nfft) * np.conj(np.fft.rfft(ref, nfft))
    freqs = np.fft.rfftfreq(nfft, 1.0 / fs)
    w = np.ones(freqs.size)
    if band is not None:
        w = ((freqs >= band[0]) & (freqs <= band[1])).astype(float)
    w[0] = 0.0
    mag = np.abs(R)
    mag = np.maximum(mag, 1e-12 * (mag.max() + 1e-300))
    G = R / mag ** beta * w
    nf = nfft * interp
    norm = 2.0 * np.sum(w * mag ** (1.0 - beta))
    cc = np.fft.irfft(G, n=nf) * (nf / max(norm, 1e-300))
    max_shift = nf // 2
    if max_tau is not None:
        max_shift = min(int(np.ceil(interp * fs * max_tau)) + interp, max_shift)
    cc = np.concatenate((cc[-max_shift:], cc[:max_shift + 1]))
    k = int(np.argmax(cc))
    shift = (k - max_shift + _parabolic_offset(cc, k)) / interp
    lags = (np.arange(cc.size) - max_shift) / (interp * fs)
    return GccResult(tau=shift / fs, delay_samples=shift, peak=float(cc[k]), lags=lags, cc=cc)


# --------------------------------------------------------------------------------------
# SRP-PHAT for any geometry
# --------------------------------------------------------------------------------------
@dataclass
class DoaResult:
    az: float            # degrees, body frame
    el: float            # degrees, body frame
    power: float         # normalised SRP-PHAT at the peak (mean pair coherence, <= 1)
    confidence: float    # 0..1: peak height above the median of the search map
    second: float        # best coarse-map power >= 20 deg away from the peak
    n_frames: int

    @property
    def peak_ratio(self) -> float:
        return self.power / self.second if self.second > 1e-9 else float("inf")

    def unit(self) -> np.ndarray:
        return unit_vector(self.az, self.el)

    def __str__(self) -> str:
        return (f"az={self.az:6.1f} el={self.el:6.1f} power={self.power:.2f} "
                f"conf={self.confidence:.2f} peak/2nd={min(self.peak_ratio, 99):.1f} frames={self.n_frames}")


class SrpPhat:
    """Steered-response-power PHAT localiser for an arbitrary array (N >= 3 mics).

    Parameters
    ----------
    mic_pos : (N, 3) mic positions in metres, body frame (x fwd, y left, z up).
    fs : sample rate, Hz.
    band : analysis band in Hz (default 300-6000 Hz).
    frame_s : STFT frame length (rounded up to a power of two in samples).
    interp : upsampling factor of the pair cross-correlations (lag resolution 1/(interp*fs)).
    az_step, el_step : coarse grid steps in degrees (refined locally to ~0.05 deg).
    el_range : elevation search limits, degrees (default -90..+20).
    hemisphere : None/'both', 'lower' (el <= 0) or 'upper' (el >= 0).
    snr_weighting : 'auto' (noise PSD from ``noise_segments`` if given, else the in-band
        median power is taken as the floor), 'noise' (only with noise segments) or 'off'.
        The weight per bin is the Wiener gain snr/(1+snr); this keeps PHAT from being
        swamped by noise-only bins when the source is tonal (a whistle).
    """

    def __init__(self, mic_pos, fs: float, *, c: float = SPEED_OF_SOUND,
                 band: tuple = (300.0, 6000.0), frame_s: float = 0.064, interp: int = 16,
                 az_step: float = 3.0, el_step: float = 3.0, el_range: tuple = (-90.0, 20.0),
                 hemisphere: str | None = None, beta: float = 1.0, snr_weighting: str = "auto",
                 n_candidates: int = 3):
        pos = np.asarray(mic_pos, dtype=float)
        if pos.ndim != 2 or pos.shape[1] != 3 or pos.shape[0] < 3:
            raise ValueError("mic_pos must have shape (N>=3, 3)")
        self.pos = pos
        self.n_mics = pos.shape[0]
        self.fs = float(fs)
        self.c = float(c)
        self.band = tuple(band)
        self.interp = int(interp)
        self.beta = float(beta)
        self.snr_weighting = snr_weighting
        self.n_candidates = int(n_candidates)
        self.nfft = next_pow2(frame_s * fs)
        self.hop = self.nfft // 2
        freqs = np.fft.rfftfreq(self.nfft, 1.0 / fs)
        band_mask = (freqs >= band[0]) & (freqs <= band[1])
        band_mask[0] = False
        self.band_idx = np.flatnonzero(band_mask)
        if self.band_idx.size < 4:
            raise ValueError("analysis band contains too few FFT bins")
        self.pi, self.pj = np.triu_indices(self.n_mics, 1)
        self.baselines = pos[self.pi] - pos[self.pj]          # (P, 3)
        self.max_tau = float(np.max(np.linalg.norm(self.baselines, axis=1)) / self.c)
        self.nlag = int(np.ceil(self.max_tau * self.fs * self.interp)) + 2 * self.interp
        sv = np.linalg.svd(pos - pos.mean(axis=0), compute_uv=False)
        self.planar = bool(sv[2] < 1e-3 * sv[0])
        self.linear = bool(sv[1] < 1e-3 * sv[0])

        lo, hi = float(el_range[0]), float(el_range[1])
        if hemisphere == "lower":
            hi = min(hi, 0.0)
        elif hemisphere == "upper":
            lo = max(lo, 0.0)
        elif hemisphere not in (None, "both"):
            raise ValueError("hemisphere must be None, 'both', 'lower' or 'upper'")
        self.hemisphere = hemisphere
        self.el_lo, self.el_hi = max(lo, -90.0), min(hi, 90.0)
        if self.el_lo > self.el_hi:
            raise ValueError("empty elevation range")
        self.az_step, self.el_step = float(az_step), float(el_step)
        az = np.arange(0.0, 360.0, self.az_step)
        el = np.arange(self.el_lo, self.el_hi + 1e-9, self.el_step)
        if el.size == 0 or el[-1] < self.el_hi - 1e-9:
            el = np.append(el, self.el_hi)
        AZ, EL = np.meshgrid(az, el)
        self.grid_shape = AZ.shape                       # (n_el, n_az)
        self.grid_az, self.grid_el = AZ.ravel(), EL.ravel()
        self._grid_tdoa = self.tdoa(self.grid_az, self.grid_el)

    # ---- geometry --------------------------------------------------------------------
    def tdoa(self, az_deg, el_deg) -> np.ndarray:
        """Pair delays (D, P) in seconds for directions (D,)."""
        u = unit_vector(np.atleast_1d(az_deg), np.atleast_1d(el_deg))  # (D, 3)
        return u @ self.baselines.T / self.c

    # ---- spectra ---------------------------------------------------------------------
    def _frame_starts(self, n: int, segments) -> np.ndarray:
        nfft, hop = self.nfft, self.hop
        if segments is None:
            segments = [(0, n)]
        starts = set()
        for s, e in segments:
            s, e = max(0, int(s)), min(n, int(e))
            if e - s >= nfft:
                starts.update(range(s, e - nfft + 1, hop))
            elif e > s:  # short segment: one frame centred on it
                starts.add(int(np.clip((s + e) // 2 - nfft // 2, 0, n - nfft)))
        return np.array(sorted(starts), dtype=int)

    def spectra(self, audio, segments=None) -> np.ndarray:
        """In-band STFT of all mics over the frames covering ``segments``: (M, F, K)."""
        x = np.asarray(audio, dtype=float)
        if x.ndim != 2 or x.shape[0] != self.n_mics:
            raise ValueError(f"audio must be (n_mics={self.n_mics}, n_samples)")
        if x.shape[1] < self.nfft:
            x = np.pad(x, ((0, 0), (0, self.nfft - x.shape[1])))
        starts = self._frame_starts(x.shape[1], segments)
        if starts.size == 0:
            raise ValueError("no audio frames in the requested segments")
        idx = starts[:, None] + np.arange(self.nfft)[None, :]
        frames = x[:, idx] * np.hanning(self.nfft)
        return np.fft.rfft(frames, axis=-1)[..., self.band_idx]

    def _weights(self, psd, audio, noise_segments, noise_psd):
        if self.snr_weighting == "off":
            return np.ones_like(psd)
        if noise_psd is None and noise_segments:
            try:
                Xn = self.spectra(audio, noise_segments)
                noise_psd = np.mean(np.abs(Xn) ** 2, axis=(0, 1))
            except ValueError:
                noise_psd = None
        if noise_psd is None:
            if self.snr_weighting == "noise":
                return np.ones_like(psd)
            noise_psd = np.full_like(psd, np.median(psd))
        snr = np.maximum(psd / np.maximum(noise_psd, 1e-300) - 1.0, 0.0)
        w = snr / (1.0 + snr)
        if w.sum() < 1e-3:          # nothing above the floor: fall back to plain PHAT
            w = np.ones_like(psd)
        return w

    def _gcc(self, S: np.ndarray, w: np.ndarray) -> np.ndarray:
        """Normalised, upsampled GCC for every pair: (P, 2*nlag+1), lag 0 at index nlag."""
        mag = np.abs(S)
        mag = np.maximum(mag, 1e-12 * (mag.max() + 1e-300))
        G = S / mag ** self.beta * w
        full = np.zeros((S.shape[0], self.nfft // 2 + 1), dtype=complex)
        full[:, self.band_idx] = G
        nf = self.nfft * self.interp
        norm = 2.0 * np.sum(w * mag ** (1.0 - self.beta), axis=1, keepdims=True)
        cc = np.fft.irfft(full, n=nf, axis=1) * (nf / np.maximum(norm, 1e-300))
        L = self.nlag
        return np.concatenate([cc[:, -L:], cc[:, :L + 1]], axis=1)

    def _power(self, cc: np.ndarray, tdoa: np.ndarray) -> np.ndarray:
        x = tdoa * (self.fs * self.interp) + self.nlag
        i0 = np.clip(np.floor(x).astype(int), 0, cc.shape[1] - 2)
        fr = x - i0
        p = np.arange(cc.shape[0])[None, :]
        return np.mean(cc[p, i0] * (1.0 - fr) + cc[p, i0 + 1] * fr, axis=1)

    def _refine(self, cc, az0, el0):
        az, el = float(az0), float(el0)
        best = float(self._power(cc, self.tdoa([az], [el]))[0])
        for frac in (1.0, 0.25, 0.0625, 0.015625):
            a = az + np.linspace(-1.0, 1.0, 9) * self.az_step * frac
            e = el + np.linspace(-1.0, 1.0, 9) * self.el_step * frac
            A, E = np.meshgrid(a, e)
            A, E = A.ravel(), np.clip(E.ravel(), self.el_lo, self.el_hi)
            pw = self._power(cc, self.tdoa(A, E))
            k = int(np.argmax(pw))
            if pw[k] >= best:
                best, az, el = float(pw[k]), float(A[k]), float(E[k])
        return az % 360.0, el, best

    # ---- public API ------------------------------------------------------------------
    def correlations(self, audio, segments=None, noise_segments=None, noise_psd=None):
        X = self.spectra(audio, segments)
        # X_j X_i^*: its correlation peaks at +tau_ij (mic j later than mic i), as in gcc_phat
        S = np.einsum("pfk,pfk->pk", X[self.pj], np.conj(X[self.pi]))
        psd = np.mean(np.abs(X) ** 2, axis=(0, 1))
        w = self._weights(psd, audio, noise_segments, noise_psd)
        return self._gcc(S, w), X.shape[1]

    def power_map(self, audio, segments=None, noise_segments=None):
        """Coarse SRP map for plotting: (az_grid, el_grid, power) each shaped (n_el, n_az)."""
        cc, _ = self.correlations(audio, segments, noise_segments)
        pw = self._power(cc, self._grid_tdoa)
        return (self.grid_az.reshape(self.grid_shape), self.grid_el.reshape(self.grid_shape),
                pw.reshape(self.grid_shape))

    def localize(self, audio, segments=None, noise_segments=None, noise_psd=None) -> DoaResult:
        """Estimate the source direction from ``audio`` (n_mics, n_samples).

        ``segments``: list of (start, end) sample indices containing the source (e.g. the
        detector's events); ``noise_segments``: noise-only stretches for SNR weighting.
        """
        cc, n_frames = self.correlations(audio, segments, noise_segments, noise_psd)
        p0 = self._power(cc, self._grid_tdoa)
        min_sep = 2.5 * max(self.az_step, self.el_step)
        cands: list[int] = []
        for k in np.argsort(p0)[::-1]:
            if len(cands) >= self.n_candidates:
                break
            if all(angular_distance_deg(self.grid_az[k], self.grid_el[k],
                                        self.grid_az[j], self.grid_el[j]) > min_sep for j in cands):
                cands.append(int(k))
        best = max((self._refine(cc, self.grid_az[k], self.grid_el[k]) for k in cands),
                   key=lambda r: r[2])
        az, el, pw = best
        med = float(np.median(p0))
        conf = float(np.clip((pw - med) / max(1.0 - med, 1e-9), 0.0, 1.0))
        far = angular_distance_deg(self.grid_az, self.grid_el, az, el) >= 20.0
        second = float(p0[far].max()) if np.any(far) else 0.0
        return DoaResult(az=az, el=el, power=pw, confidence=conf, second=second, n_frames=n_frames)


def srp_phat(audio, fs: float, mic_pos, segments=None, noise_segments=None, **kwargs) -> DoaResult:
    """One-shot convenience wrapper around :class:`SrpPhat`."""
    return SrpPhat(mic_pos, fs, **kwargs).localize(audio, segments, noise_segments)


def pair_doa_check(audio, fs: float, mic_pos, pair: Sequence[int] = (0, 1), **kw) -> float:
    """Measured delay (s) of mic pair[1] relative to pair[0] -- handy for wiring checks."""
    i, j = pair
    max_tau = np.linalg.norm(np.asarray(mic_pos)[i] - np.asarray(mic_pos)[j]) / SPEED_OF_SOUND
    return gcc_phat(audio[j], audio[i], fs, max_tau=max_tau, **kw).tau
