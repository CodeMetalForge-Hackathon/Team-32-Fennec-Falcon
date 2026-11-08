"""Acoustic event detection for briefed, cooperative signals.

* :func:`detect_energy`   adaptive noise-floor energy detector (anything loud)
* :func:`detect_whistles` tonal detector: a spectral peak in 800-4000 Hz that stands well
  above the band median (peak-to-median ratio), lasting >= ~0.15 s
* :func:`detect_taps`     transient detector for claps / knocks / taps on wood or metal
* :func:`match_codes`     finds briefed patterns ("3 whistles within 5 s", long-short-short,
  tap-code groups) in the event list, with tolerances

Every :class:`Event` carries start/end *sample indices* so DOA runs on exactly that audio.
Detectors work on a mono power spectrogram averaged over up to four channels, so channel
delays can never cancel the signal (summing waveforms could).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

from .doa import next_pow2

_TINY = 1e-20


@dataclass
class Event:
    kind: str                 # 'whistle' | 'tap' | 'energy'
    start: int                # first sample (inclusive)
    end: int                  # last sample (exclusive)
    fs: float
    peak_db: float = 0.0      # band level at the loudest frame, dB re full scale
    snr_db: float = 0.0       # loudest frame above the tracked noise floor
    f0_hz: float | None = None
    tonality_db: float | None = None

    @property
    def t0(self) -> float:
        return self.start / self.fs

    @property
    def t1(self) -> float:
        return self.end / self.fs

    @property
    def duration(self) -> float:
        return (self.end - self.start) / self.fs

    def __str__(self) -> str:
        extra = f" f0={self.f0_hz:.0f}Hz" if self.f0_hz else ""
        return f"{self.kind}@{self.t0:.2f}-{self.t1:.2f}s snr={self.snr_db:.0f}dB{extra}"


@dataclass
class Spectrogram:
    power: np.ndarray       # (F, K) mean power over channels, per frame
    freqs: np.ndarray       # (K,)
    starts: np.ndarray      # (F,) first sample of each frame
    frame_len: int
    hop: int
    fs: float

    def band_level_db(self, band) -> np.ndarray:
        m = (self.freqs >= band[0]) & (self.freqs <= band[1])
        return 10.0 * np.log10(self.power[:, m].sum(axis=1) + _TINY)


def power_spectrogram(audio, fs: float, frame_s: float, hop_s: float,
                      max_channels: int = 4, chunk_frames: int = 2048) -> Spectrogram:
    """Mean (over channels) power spectrogram with a Hann window, computed in chunks."""
    x = np.atleast_2d(np.asarray(audio, dtype=float))
    if x.shape[0] > max_channels:
        x = x[np.linspace(0, x.shape[0] - 1, max_channels).round().astype(int)]
    L = max(16, int(round(frame_s * fs)))
    hop = max(1, int(round(hop_s * fs)))
    if x.shape[1] < L:
        x = np.pad(x, ((0, 0), (0, L - x.shape[1])))
    nfft = next_pow2(L)
    win = np.hanning(L)
    scale = 1.0 / np.sum(win ** 2)
    starts = np.arange(0, x.shape[1] - L + 1, hop)
    out = np.empty((starts.size, nfft // 2 + 1))
    for c0 in range(0, starts.size, chunk_frames):
        st = starts[c0:c0 + chunk_frames]
        frames = x[:, st[:, None] + np.arange(L)[None, :]] * win
        out[c0:c0 + st.size] = np.mean(np.abs(np.fft.rfft(frames, nfft, axis=-1)) ** 2, axis=0) * scale
    return Spectrogram(out, np.fft.rfftfreq(nfft, 1.0 / fs), starts, L, hop, float(fs))


class FloorTracker:
    """Asymmetric noise-floor tracker on a dB level: falls fast, rises slowly.

    ``rise_db_per_s`` limits how fast the floor can climb (a 0.5 s whistle lifts it by
    about 1.5 dB at the default 3 dB/s); callers freeze it while an event is active.
    """

    def __init__(self, hop_s: float, rise_db_per_s: float = 3.0, fall_alpha: float = 0.3,
                 init_db: float | None = None):
        self.rise = rise_db_per_s * hop_s
        self.fall = fall_alpha
        self.value = init_db

    def update(self, level_db: float, freeze: bool = False) -> float:
        if self.value is None:
            self.value = level_db
        elif level_db < self.value:
            self.value += self.fall * (level_db - self.value)
        elif not freeze:
            self.value = min(level_db, self.value + self.rise)
        return self.value


def _init_floor(level: np.ndarray) -> float:
    return float(np.percentile(level, 10)) if level.size else 0.0


def detect_energy(audio, fs: float, *, band=(300.0, 6000.0), frame_s: float = 0.02,
                  hop_s: float = 0.01, on_db: float = 10.0, off_db: float = 6.0,
                  min_dur_s: float = 0.05, merge_gap_s: float = 0.1,
                  rise_db_per_s: float = 3.0, spec: Spectrogram | None = None) -> list[Event]:
    """Generic loudness detector with an adaptive floor and on/off hysteresis."""
    spec = spec or power_spectrogram(audio, fs, frame_s, hop_s)
    level = spec.band_level_db(band)
    trk = FloorTracker(spec.hop / fs, rise_db_per_s, init_db=_init_floor(level))
    events: list[Event] = []
    active, i0, peak, peak_snr = False, 0, -999.0, 0.0
    for i, v in enumerate(level):
        fl = trk.value
        if not active and v - fl >= on_db:
            active, i0, peak, peak_snr = True, i, v, v - fl
        elif active:
            peak, peak_snr = max(peak, v), max(peak_snr, v - fl)
            if v - fl < off_db:
                events.append(Event("energy", int(spec.starts[i0]), int(spec.starts[i] + spec.frame_len),
                                    fs, peak, peak_snr))
                active = False
        trk.update(v, freeze=active)
    if active:
        events.append(Event("energy", int(spec.starts[i0]), int(spec.starts[-1] + spec.frame_len),
                            fs, peak, peak_snr))
    merged: list[Event] = []
    for ev in events:
        if merged and (ev.start - merged[-1].end) / fs <= merge_gap_s:
            last = merged[-1]
            last.end, last.peak_db = ev.end, max(last.peak_db, ev.peak_db)
            last.snr_db = max(last.snr_db, ev.snr_db)
        else:
            merged.append(ev)
    return [e for e in merged if e.duration >= min_dur_s]


def detect_whistles(audio, fs: float, *, band=(800.0, 4000.0), frame_s: float = 0.032,
                    hop_s: float = 0.01, tonal_db: float = 12.0, snr_db: float = 3.0,
                    min_dur_s: float = 0.15, max_dur_s: float = 4.0, max_gap_s: float = 0.05,
                    max_jump_hz: float = 300.0, spec: Spectrogram | None = None) -> list[Event]:
    """Whistle detector.

    A frame is *tonal* when the strongest bin in ``band`` is ``tonal_db`` above the band
    median (noise gives ~8 dB) and the band level is ``snr_db`` above the tracked floor.
    Tonal frames whose peak frequency moves by <= ``max_jump_hz`` per hop are chained,
    bridging gaps up to ``max_gap_s``; chains of ``min_dur_s``..``max_dur_s`` are whistles.
    """
    spec = spec or power_spectrogram(audio, fs, frame_s, hop_s)
    m = (spec.freqs >= band[0]) & (spec.freqs <= band[1])
    Pb, fb = spec.power[:, m], spec.freqs[m]
    if Pb.shape[1] < 8:
        raise ValueError("whistle band too narrow for this frame length")
    kpk = np.argmax(Pb, axis=1)
    peak = Pb[np.arange(Pb.shape[0]), kpk]
    tonality = 10.0 * np.log10((peak + _TINY) / (np.median(Pb, axis=1) + _TINY))
    level = 10.0 * np.log10(Pb.sum(axis=1) + _TINY)
    df = fb[1] - fb[0]
    f0 = fb[kpk].astype(float)
    for i, k in enumerate(kpk):  # parabolic refinement of the peak frequency (log power)
        if 0 < k < len(fb) - 1:
            a, b, c = np.log(Pb[i, k - 1:k + 2] + _TINY)
            den = a - 2 * b + c
            if den < 0:
                f0[i] += df * 0.5 * (a - c) / den
    trk = FloorTracker(spec.hop / fs, init_db=_init_floor(level))
    tonal = np.zeros(level.size, dtype=bool)
    for i, v in enumerate(level):
        tonal[i] = tonality[i] >= tonal_db and v - trk.value >= snr_db
        trk.update(v, freeze=tonal[i])
    max_gap = int(round(max_gap_s * fs / spec.hop))
    events: list[Event] = []
    i, n = 0, level.size
    while i < n:
        if not tonal[i]:
            i += 1
            continue
        idx = [i]
        gap = 0
        j = i + 1
        while j < n:
            if tonal[j] and abs(f0[j] - f0[idx[-1]]) <= max_jump_hz * (j - idx[-1]):
                idx.append(j)
                gap = 0
            else:
                gap += 1
                if gap > max_gap:
                    break
            j += 1
        start = int(spec.starts[idx[0]])
        end = int(spec.starts[idx[-1]] + spec.frame_len)
        dur = (end - start) / fs
        if min_dur_s <= dur <= max_dur_s:
            sel = np.array(idx)
            k = sel[np.argmax(level[sel])]
            events.append(Event("whistle", start, end, fs, float(level[k]),
                                float(level[k] - _init_floor(level)),
                                float(np.median(f0[sel])), float(np.median(tonality[sel]))))
        i = idx[-1] + 1
    return events


def detect_taps(audio, fs: float, *, band=(300.0, 6000.0), frame_s: float = 0.008,
                hop_s: float = 0.004, on_db: float = 12.0, rise_db: float = 9.0,
                off_db: float = 6.0, max_dur_s: float = 0.15, refractory_s: float = 0.12,
                pre_s: float = 0.004, post_s: float = 0.05,
                spec: Spectrogram | None = None) -> list[Event]:
    """Clap / knock / tap detector: a sharp rise above the floor that dies away quickly.

    Onset when the band level is ``on_db`` above the floor *and* ``rise_db`` above the
    minimum of the previous ~16 ms. Anything still loud after ``max_dur_s`` (a whistle,
    speech, an engine) is rejected. The returned segment is the onset (-``pre_s``,
    +``post_s``): the direct sound, before most reflections arrive -- best for DOA.
    """
    spec = spec or power_spectrogram(audio, fs, frame_s, hop_s)
    level = spec.band_level_db(band)
    trk = FloorTracker(spec.hop / fs, init_db=_init_floor(level))
    look = max(2, int(round(0.016 * fs / spec.hop)))
    max_frames = int(np.ceil(max_dur_s * fs / spec.hop))
    refr = int(np.ceil(refractory_s * fs / spec.hop))
    events: list[Event] = []
    i, n = 0, level.size
    while i < n:
        fl = trk.value
        v = level[i]
        prev_min = level[max(0, i - look):i].min() if i > 0 else v
        if v - fl >= on_db and v - prev_min >= rise_db:
            j = i
            while j < n and level[j] - fl >= off_db and j - i <= max_frames:
                j += 1
            if j - i <= max_frames:
                k = i + int(np.argmax(level[i:j + 1]))
                onset = int(spec.starts[i])
                events.append(Event("tap", max(0, onset - int(pre_s * fs)),
                                    onset + int(post_s * fs), fs, float(level[k]), float(level[k] - fl)))
            # freeze the floor over the event / refractory window
            i = max(j, i + refr)
            continue
        trk.update(v)
        i += 1
    return events


# --------------------------------------------------------------------------------------
# Briefed codes
# --------------------------------------------------------------------------------------
@dataclass
class CodeSpec:
    """A briefed signal. Exactly one of ``count``, ``rhythm`` or ``groups`` is used.

    count  : N events of ``kind`` whose first start and last end fit in ``within_s``
    rhythm : string of L/S, e.g. "LSS" = long-short-short (L >= long_min_s, S <= short_max_s)
    groups : tap-code groups, e.g. [2, 3] = two taps, pause (> group_gap_s), three taps
    tolerance scales every threshold (e.g. 0.15 = 15 % slack).
    """
    name: str
    kind: str = "whistle"
    count: int | None = None
    within_s: float = 5.0
    min_gap_s: float = 0.08
    rhythm: str | None = None
    long_min_s: float = 0.6
    short_max_s: float = 0.45
    max_gap_s: float = 1.5
    groups: list | None = None
    group_gap_s: float = 0.6
    tolerance: float = 0.15
    exact: bool = False          # count codes: no extra event within max_gap_s on either side
    status: list | None = None   # optional status names reported with this code, e.g. [injured]

    @classmethod
    def from_dict(cls, d: dict) -> "CodeSpec":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in allowed})


@dataclass
class Match:
    code: str
    kind: str
    events: list = field(default_factory=list)

    @property
    def start(self) -> int:
        return self.events[0].start

    @property
    def end(self) -> int:
        return self.events[-1].end

    @property
    def segments(self) -> list:
        return [(e.start, e.end) for e in self.events]

    def __str__(self) -> str:
        fs = self.events[0].fs
        return f"{self.code} ({len(self.events)} x {self.kind}, t={self.start / fs:.2f}-{self.end / fs:.2f}s)"


def _gap(a: Event, b: Event) -> float:
    return (b.start - a.end) / a.fs


def _match_count(ev: list, spec: CodeSpec) -> list:
    out, i, n = [], 0, spec.count
    tol = 1.0 + spec.tolerance
    while i + n <= len(ev):
        w = ev[i:i + n]
        span = (w[-1].end - w[0].start) / w[0].fs
        gaps_ok = all(_gap(a, b) >= spec.min_gap_s for a, b in zip(w, w[1:]))
        exact_ok = True
        if spec.exact:
            if i > 0 and _gap(ev[i - 1], w[0]) <= spec.max_gap_s:
                exact_ok = False
            if i + n < len(ev) and _gap(w[-1], ev[i + n]) <= spec.max_gap_s:
                exact_ok = False
        if span <= spec.within_s * tol and gaps_ok and exact_ok:
            out.append(Match(spec.name, spec.kind, list(w)))
            i += n
        else:
            i += 1
    return out


def _symbol(e: Event, spec: CodeSpec) -> str:
    if e.duration >= spec.long_min_s * (1.0 - spec.tolerance):
        return "L"
    if e.duration <= spec.short_max_s * (1.0 + spec.tolerance):
        return "S"
    return "?"


def _match_rhythm(ev: list, spec: CodeSpec) -> list:
    pat = spec.rhythm.upper().replace("-", "").replace(" ", "")
    out, i, n = [], 0, len(pat)
    while i + n <= len(ev):
        w = ev[i:i + n]
        ok = all(_gap(a, b) <= spec.max_gap_s * (1 + spec.tolerance) for a, b in zip(w, w[1:]))
        if ok and "".join(_symbol(e, spec) for e in w) == pat:
            out.append(Match(spec.name, spec.kind, list(w)))
            i += n
        else:
            i += 1
    return out


def _match_groups(ev: list, spec: CodeSpec) -> list:
    groups: list[list] = []
    for e in ev:
        if groups and _gap(groups[-1][-1], e) <= spec.group_gap_s:
            groups[-1].append(e)
        else:
            groups.append([e])
    want = list(spec.groups)
    out, i, n = [], 0, len(want)
    while i + n <= len(groups):
        w = groups[i:i + n]
        gaps_ok = all(_gap(a[-1], b[0]) <= spec.max_gap_s * (1 + spec.tolerance) for a, b in zip(w, w[1:]))
        if gaps_ok and [len(g) for g in w] == want:
            out.append(Match(spec.name, spec.kind, [e for g in w for e in g]))
            i += n
        else:
            i += 1
    return out


def match_codes(events: Iterable[Event], codes: Sequence) -> list[Match]:
    """Find every briefed code in ``events`` (non-overlapping per code), sorted by time."""
    events = sorted(events, key=lambda e: e.start)
    found: list[Match] = []
    for c in codes:
        spec = c if isinstance(c, CodeSpec) else CodeSpec.from_dict(c)
        ev = [e for e in events if e.kind == spec.kind]
        if spec.count:
            found += _match_count(ev, spec)
        elif spec.rhythm:
            found += _match_rhythm(ev, spec)
        elif spec.groups:
            found += _match_groups(ev, spec)
    return sorted(found, key=lambda m: m.start)


# Classic 5x5 tap code (K is sent as C): row taps, pause, column taps.
TAP_CODE = ["ABCDE", "FGHIJ", "LMNOP", "QRSTU", "VWXYZ"]


def tap_code_letter(row: int, col: int) -> str | None:
    """Letter for a (row, col) tap-code pair, e.g. (2, 3) -> 'H'."""
    if 1 <= row <= 5 and 1 <= col <= 5:
        return TAP_CODE[row - 1][col - 1]
    return None


def detect_all(audio, fs: float, cfg: dict | None = None) -> list[Event]:
    """Run the whistle and tap detectors with parameters from a config dict."""
    cfg = cfg or {}
    ev = []
    if cfg.get("whistle", {}).get("enabled", True):
        ev += detect_whistles(audio, fs, **{k: v for k, v in cfg.get("whistle", {}).items() if k != "enabled"})
    if cfg.get("tap", {}).get("enabled", True):
        ev += detect_taps(audio, fs, **{k: v for k, v in cfg.get("tap", {}).items() if k != "enabled"})
    if cfg.get("energy", {}).get("enabled", False):
        ev += detect_energy(audio, fs, **{k: v for k, v in cfg.get("energy", {}).items() if k != "enabled"})
    return sorted(ev, key=lambda e: e.start)
