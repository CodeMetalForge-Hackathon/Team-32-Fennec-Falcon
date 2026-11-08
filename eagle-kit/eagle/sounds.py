"""Life sounds: VOICE, DISTRESS, BREATH, COUGH, SIGNAL, MOVE - classified, then located.

Two classifiers with the same output (category, confidence):

(a) :class:`YamnetClassifier` - Google's YAMNet (AudioSet, 521 classes) as a TFLite model,
    used when ``ai_edge_litert``, ``tflite_runtime`` or ``tensorflow`` is installed and a model
    path is configured. 16 kHz mono, windows of 15600 samples (0.975 s), hop 7800; scores are
    averaged over the windows and each category takes the max over its AudioSet classes
    (:data:`YAMNET_MAP`, exact display names from ``eagle/data/yamnet_class_map.csv``).
(b) :class:`HeuristicClassifier` - NO machine learning, clearly a heuristic: burst
    segmentation on the 200-6000 Hz level, autocorrelation pitch / harmonicity (voiced
    frames), 3-8 Hz syllabic envelope modulation (speech), onset sharpness and burst length
    (cough vs tap), whistle tonality, 0.15-0.6 Hz envelope periodicity (breathing). Tuned on
    the synthetic signals below; expect confusions on real recordings (shouted speech vs
    screams, shuffling vs breathing in wind) and validate at the venue.

:func:`detect_life_sounds` cuts a stream into events, classifies each, and
:func:`locate_sounds` runs the kit's SRP-PHAT on each event's samples to attach a bearing,
elevation and (where the depression is usable) a floor point, ready for :mod:`eagle.fusion`.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

CATEGORIES = ("VOICE", "DISTRESS", "BREATH", "COUGH", "SIGNAL", "MOVE")
DEFAULT_CLASS_MAP = Path(__file__).resolve().parent / "data" / "yamnet_class_map.csv"
YAMNET_FS = 16000
YAMNET_WINDOW = 15600

# Category -> exact YAMNet display names (checked against the CSV at load time and in tests).
YAMNET_MAP = {
    "VOICE": ["Speech", "Child speech, kid speaking", "Conversation", "Narration, monologue", "Babbling",
              "Whispering"],
    "DISTRESS": ["Shout", "Bellow", "Yell", "Screaming", "Crying, sobbing", "Whimper", "Wail, moan", "Groan",
                 "Grunt"],
    "BREATH": ["Breathing", "Wheeze", "Snoring", "Gasp", "Pant", "Snort", "Sigh"],
    "COUGH": ["Cough", "Throat clearing", "Sneeze", "Sniff"],
    "SIGNAL": ["Whistling", "Whistle", "Clapping", "Finger snapping", "Knock", "Tap", "Thump, thud"],
    "MOVE": ["Walk, footsteps", "Shuffle", "Run", "Rustle", "Scratch"],
}


def load_class_names(path=DEFAULT_CLASS_MAP) -> list:
    """display_name of each YAMNet output index, from yamnet_class_map.csv."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [r["display_name"] for r in sorted(rows, key=lambda r: int(r["index"]))]


def resample(x, fs_in: float, fs_out: float) -> np.ndarray:
    """FFT resampling (band-limited); identity when the rates match."""
    x = np.asarray(x, dtype=float)
    if int(fs_in) == int(fs_out):
        return x
    n_out = int(round(x.size * fs_out / fs_in))
    X = np.fft.rfft(x)
    m = n_out // 2 + 1
    Y = np.zeros(m, dtype=complex)
    k = min(m, X.size)
    Y[:k] = X[:k]
    return np.fft.irfft(Y, n_out) * (n_out / x.size)


@dataclass
class SoundLabel:
    category: str | None
    confidence: float
    method: str                       # "yamnet" | "heuristic"
    detail: dict = field(default_factory=dict)


# ---- (a) YAMNet ------------------------------------------------------------------------------
def _tflite_interpreter(model_path: str):
    errors = []
    for mod, attr in (("ai_edge_litert.interpreter", "Interpreter"), ("tflite_runtime.interpreter", "Interpreter"),
                      ("tensorflow.lite", "Interpreter")):
        try:
            m = __import__(mod, fromlist=[attr])
            return getattr(m, attr)(model_path=str(model_path))
        except ImportError as e:
            errors.append(f"{mod}: {e}")
    raise ImportError("no TFLite runtime: pip install ai-edge-litert (or tflite-runtime / tensorflow); " +
                      "; ".join(errors))


class YamnetClassifier:
    """YAMNet TFLite model -> life-sound categories (see module docstring)."""

    def __init__(self, model_path, class_map=DEFAULT_CLASS_MAP, mapping=None, threshold: float = 0.15):
        self.names = load_class_names(class_map)
        mapping = mapping or YAMNET_MAP
        missing = [n for cls in mapping.values() for n in cls if n not in self.names]
        if missing:
            raise ValueError(f"class names not in {class_map}: {missing}")
        self.index = {cat: [self.names.index(n) for n in cls] for cat, cls in mapping.items()}
        self.threshold = float(threshold)
        self.interp = _tflite_interpreter(model_path)
        self.interp.allocate_tensors()
        inp = self.interp.get_input_details()[0]
        self.in_idx, self.in_shape = inp["index"], list(inp["shape"])
        if int(np.prod(self.in_shape)) != YAMNET_WINDOW:      # e.g. a variable-length export
            shape = [YAMNET_WINDOW] if len(self.in_shape) <= 1 else [1, YAMNET_WINDOW]
            self.interp.resize_tensor_input(self.in_idx, shape)
            self.interp.allocate_tensors()
            self.in_shape = shape
        self.outs = self.interp.get_output_details()

    def scores(self, x, fs: float) -> np.ndarray:
        """Mean class scores (521,) over 0.975 s windows (hop 0.4875 s)."""
        x = resample(np.asarray(x, float), fs, YAMNET_FS).astype(np.float32)
        x = np.clip(x / max(1e-9, np.max(np.abs(x))) * 0.5, -1.0, 1.0) if np.max(np.abs(x)) > 1.0 else x
        if x.size < YAMNET_WINDOW:
            x = np.pad(x, (0, YAMNET_WINDOW - x.size))
        acc = []
        for s in range(0, x.size - YAMNET_WINDOW + 1, YAMNET_WINDOW // 2):
            self.interp.set_tensor(self.in_idx, x[s:s + YAMNET_WINDOW].reshape(self.in_shape))
            self.interp.invoke()
            for o in self.outs:
                out = np.asarray(self.interp.get_tensor(o["index"]))
                if out.shape[-1] == len(self.names):
                    acc.append(out.reshape(-1, len(self.names)).mean(axis=0))
                    break
        return np.mean(acc, axis=0) if acc else np.zeros(len(self.names))

    def classify(self, x, fs: float) -> SoundLabel:
        sc = self.scores(x, fs)
        cat_scores = {c: float(sc[idx].max()) for c, idx in self.index.items()}
        best = max(cat_scores, key=cat_scores.get)
        top = [(self.names[i], float(sc[i])) for i in np.argsort(sc)[::-1][:3]]
        if cat_scores[best] < self.threshold:
            return SoundLabel(None, cat_scores[best], "yamnet", {"categories": cat_scores, "top": top})
        return SoundLabel(best, cat_scores[best], "yamnet", {"categories": cat_scores, "top": top})


# ---- (b) heuristic ----------------------------------------------------------------------------
def _frames(x, n, hop):
    if x.size < n:
        x = np.pad(x, (0, n - x.size))
    starts = np.arange(0, x.size - n + 1, hop)
    return x[starts[:, None] + np.arange(n)[None, :]], starts


def _bandpass(x, fs, lo, hi):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(x.size, 1 / fs)
    X[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(X, x.size)


class HeuristicClassifier:
    """Rule-based life-sound classifier (no ML). See the module docstring for its limits."""

    method = "heuristic"

    def __init__(self, *, active_db: float = 10.0, voiced_r: float = 0.45, f0_range=(70.0, 450.0),
                 syllabic_ratio: float = 0.2):
        self.active_db, self.voiced_r, self.f0_range = active_db, voiced_r, tuple(f0_range)
        self.syll = syllabic_ratio

    def features(self, x, fs: float) -> dict:
        x = np.asarray(x, dtype=float)
        x = x - x.mean()
        xb = _bandpass(x, fs, 200.0, min(6000.0, 0.45 * fs))
        hop, n = int(0.01 * fs), int(0.02 * fs)
        fr, _ = _frames(xb, n, hop)
        lev = 10 * np.log10(np.mean(fr ** 2, axis=1) + 1e-20)
        floor = float(np.percentile(lev, 10))
        active = lev > floor + self.active_db
        # bursts: active runs, gaps < 60 ms bridged
        runs, i = [], 0
        while i < active.size:
            if active[i]:
                j = i
                while j < active.size and (active[j] or (j + 6 < active.size and active[j:j + 6].any())):
                    j += 1
                runs.append((i, j))
                i = j
            else:
                i += 1
        dur = np.array([(b - a) * hop / fs for a, b in runs]) if runs else np.zeros(0)
        rise = []
        for a, b in runs:
            pk = a + int(np.argmax(lev[a:b]))
            pre = lev[max(0, a - 3):a + 1].min() if a > 0 else floor
            rise.append(((pk - a) * hop / fs, float(lev[pk] - pre)))
        # pitch / harmonicity on 40 ms frames (10 ms hop) over active frames
        n2 = int(0.04 * fs)
        fr2, st2 = _frames(_bandpass(x, fs, 60.0, 2000.0), n2, hop)
        nf = 1 << int(np.ceil(np.log2(2 * n2)))
        F = np.fft.rfft(fr2 * np.hanning(n2), nf, axis=1)
        ac = np.fft.irfft(np.abs(F) ** 2, nf, axis=1)[:, :n2]
        ac = ac / np.maximum(ac[:, :1], 1e-20)
        lo, hi = int(fs / self.f0_range[1]), int(fs / self.f0_range[0])
        lag = lo + np.argmax(ac[:, lo:hi], axis=1)
        harm = ac[np.arange(ac.shape[0]), lag]
        a2 = np.zeros(harm.size, bool)
        idx = np.minimum(st2 // hop, active.size - 1)
        a2[:] = active[idx]
        voiced = a2 & (harm >= self.voiced_r)
        voiced_frac = float(voiced.sum() / max(a2.sum(), 1))
        f0 = float(np.median(fs / lag[voiced])) if voiced.any() else 0.0
        # whistle tonality: share of the 300-6000 Hz energy in +-60 Hz around the strongest
        # 800-4000 Hz line (a whistle is one line; speech spreads over harmonics; noise is flat)
        n3 = int(0.032 * fs)
        fr3, st3 = _frames(x, n3, hop)
        S = np.abs(np.fft.rfft(fr3 * np.hanning(n3), axis=1)) ** 2
        f3 = np.fft.rfftfreq(n3, 1 / fs)
        wide = (f3 >= 300) & (f3 <= min(6000, 0.45 * fs))
        m3 = np.flatnonzero((f3 >= 800) & (f3 <= min(4000, 0.45 * fs)))
        kpk = m3[np.argmax(S[:, m3], axis=1)]
        near = np.abs(f3[None, :] - f3[kpk][:, None]) <= 60.0
        conc = (S * near).sum(axis=1) / (S[:, wide].sum(axis=1) + 1e-20)
        a3 = active[np.minimum(st3 // hop, active.size - 1)]
        tonal_frac = float(np.mean(conc[a3] >= 0.6)) if a3.any() else 0.0
        # syllabic modulation of the active span (envelope at 100 Hz)
        env = np.sqrt(np.mean(fr ** 2, axis=1))
        if runs:
            seg = env[runs[0][0]:runs[-1][1]]
        else:
            seg = env
        syl = 0.0
        if seg.size >= 50:
            E = np.abs(np.fft.rfft((seg - seg.mean()) * np.hanning(seg.size), 4 * seg.size)) ** 2
            fe = np.fft.rfftfreq(4 * seg.size, 0.01)
            syl = float(E[(fe >= 3) & (fe <= 8)].sum() / (E[(fe >= 0.5) & (fe <= 25)].sum() + 1e-20))
        # slow periodicity of the envelope (breathing 9-36 /min -> lag 1.7-6.7 s)
        per = 0.0
        e = env - env.mean()
        if e.size * 0.01 >= 8.0:
            r = np.correlate(e, e, "full")[e.size - 1:]
            r = r / max(r[0], 1e-20)
            l0, l1 = int(1.6 / 0.01), min(int(6.7 / 0.01), r.size - 1)
            if l1 > l0:
                per = float(r[l0:l1].max())
        span = (runs[-1][1] - runs[0][0]) * hop / fs if runs else 0.0
        return {"n_bursts": len(runs), "dur": dur, "rise": rise, "voiced_frac": voiced_frac, "f0": f0,
                "tonal_frac": tonal_frac, "syllabic": syl, "periodicity": per,
                "active_s": float(dur.sum()), "span_s": float(span),
                "fill": float(dur.sum() / span) if span > 0 else 0.0}

    def classify(self, x, fs: float) -> SoundLabel:
        ft = self.features(x, fs)
        dur, rise, nb = ft["dur"], ft["rise"], ft["n_bursts"]
        if nb == 0:
            return SoundLabel(None, 0.0, self.method, ft)

        def lab(cat, conf):
            return SoundLabel(cat, float(np.clip(conf, 0.05, 0.95)), self.method, ft)
        if ft["tonal_frac"] >= 0.5:
            return lab("SIGNAL", ft["tonal_frac"])                         # whistle
        sharp = [r for r in rise if r[0] <= 0.03 and r[1] >= 12.0]
        if nb >= 2 and np.all(dur <= 0.15) and ft["fill"] < 0.4 and len(sharp) >= 0.7 * nb:
            return lab("SIGNAL", 0.6 + 0.05 * nb)                          # taps / knocks / claps
        if ft["voiced_frac"] >= 0.4:
            if ft["syllabic"] >= self.syll and nb >= 2:                  # syllables with gaps
                return lab("VOICE", 0.5 + ft["syllabic"])
            return lab("DISTRESS", 0.5 + 0.5 * ft["voiced_frac"])          # groan / moan / wail
        if nb <= 4 and np.all((dur >= 0.12) & (dur <= 0.9)) and len(sharp) >= max(1, nb - 1):
            return lab("COUGH", 0.6)
        gradual = [d for d, r in zip(dur, rise) if 0.4 <= d <= 4.0 and r[0] > 0.05]
        if ft["periodicity"] >= 0.3 or (len(gradual) >= 3 and len(gradual) >= 0.6 * nb and ft["fill"] < 0.85):
            return lab("BREATH", 0.4 + 0.5 * max(ft["periodicity"], 0.2))
        return lab("MOVE", 0.4)


# ---- streams: events -> categories -> bearings -----------------------------------------------
@dataclass
class LifeSound:
    category: str | None
    confidence: float
    start: int                          # sample indices in the analysed audio
    end: int
    fs: float
    method: str = "heuristic"
    az_world: float | None = None       # math azimuth (CCW from east), ENU
    el_world: float | None = None
    bearing_deg: float | None = None    # compass
    xy: np.ndarray | None = None        # floor point if the depression allows one
    cov: np.ndarray | None = None
    doa_conf: float = 0.0
    note: str = ""

    @property
    def t0(self) -> float:
        return self.start / self.fs

    @property
    def t1(self) -> float:
        return self.end / self.fs

    def __str__(self) -> str:
        s = f"{self.category or '?'}({self.method} {self.confidence:.2f}) {self.t0:.2f}-{self.t1:.2f}s"
        if self.bearing_deg is not None:
            s += f" bearing {self.bearing_deg:.1f} el {self.el_world:.1f}"
        if self.xy is not None:
            s += f" -> E {self.xy[0]:.1f} N {self.xy[1]:.1f}"
        return s + (f" [{self.note}]" if self.note else "")


def segment_events(audio, fs: float, *, active_db: float = 10.0, join_gap_s: float = 0.8,
                   min_dur_s: float = 0.04, pad_s: float = 0.1) -> list:
    """(start, end) sample spans of sound activity; bursts closer than ``join_gap_s`` join."""
    x = np.atleast_2d(np.asarray(audio, float))
    xm = _bandpass(np.mean(x[:min(4, x.shape[0])], axis=0), fs, 200.0, min(6000.0, 0.45 * fs))
    hop, n = int(0.01 * fs), int(0.02 * fs)
    fr, st = _frames(xm, n, hop)
    lev = 10 * np.log10(np.mean(fr ** 2, axis=1) + 1e-20)
    act = lev > np.percentile(lev, 10) + active_db
    spans, i = [], 0
    while i < act.size:
        if act[i]:
            j = i
            while j < act.size and act[j]:
                j += 1
            spans.append([int(st[i]), int(st[j - 1] + n)])
            i = j
        else:
            i += 1
    merged = []
    for s, e in spans:
        if merged and (s - merged[-1][1]) / fs <= join_gap_s:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    pad = int(pad_s * fs)
    return [(max(0, s - pad), min(x.shape[1], e + pad)) for s, e in merged if (e - s) / fs >= min_dur_s]


def detect_life_sounds(audio, fs: float, classifier=None, **seg_kw) -> list:
    """Cut ``audio`` (channels, samples) into events and classify each -> [LifeSound]."""
    clf = classifier or HeuristicClassifier()
    x = np.atleast_2d(np.asarray(audio, float))
    mono = np.mean(x[:min(4, x.shape[0])], axis=0)
    out = []
    for s, e in segment_events(x, fs, **seg_kw):
        lab = clf.classify(mono[s:e], fs)
        out.append(LifeSound(lab.category, lab.confidence, s, e, fs, lab.method))
    return out


def locate_sounds(audio, sounds: list, locator) -> list:
    """Attach bearing / elevation (and a floor point when possible) to each event using the
    kit's :class:`eagle.pipeline.Locator` (its SRP-PHAT, perch pose and floor model)."""
    from .locate import body_to_world, compass_bearing, fox_fix, fox_fix_covariance
    for snd in sounds:
        try:
            d = locator.srp.localize(audio, [(snd.start, snd.end)])
        except ValueError as e:
            snd.note = f"no DOA: {e}"
            continue
        if locator.el_bias:
            d.el = float(np.clip(d.el + locator.el_bias, -90, 90))
        azw, elw = body_to_world(d.az, d.el, heading_deg=locator.heading, pitch_deg=locator.pitch,
                                 roll_deg=locator.roll, az_offset_deg=locator.az_offset)
        snd.az_world, snd.el_world, snd.doa_conf = float(azw), float(elw), float(d.confidence)
        snd.bearing_deg = float(compass_bearing(azw))
        if d.confidence < locator.min_conf:
            snd.note = f"DOA confidence {d.confidence:.2f}: bearing only"
            continue
        try:
            fx = fox_fix(azw, elw, locator.perch, floor_z=locator.floor_z, source_height=locator.src_h)
        except ValueError as e:
            snd.note = f"{e}: bearing only"
            continue
        snd.xy = fx.xyz[:2]
        snd.cov = fox_fix_covariance(fx, locator.sigma_az, locator.sigma_el)
    return sounds


def make_classifier(scfg: dict | None, log=print):
    """YAMNet if configured and loadable, else the heuristic (logged)."""
    scfg = scfg or {}
    path = scfg.get("yamnet_model")
    if path:
        try:
            return YamnetClassifier(Path(str(path)).expanduser(), scfg.get("class_map") or DEFAULT_CLASS_MAP,
                                    scfg.get("mapping"), float(scfg.get("threshold", 0.15)))
        except (ImportError, ValueError, OSError) as e:
            log(f"SOUNDS  YAMNet unavailable ({e}); using the heuristic classifier")
    return HeuristicClassifier()


# ---- synthetic life sounds (tests / demo) ----------------------------------------------------
def _formant_filter(seg, fs, formants, bw=120.0):
    X = np.fft.rfft(seg)
    f = np.fft.rfftfreq(seg.size, 1 / fs)
    H = np.zeros_like(f)
    for k, F in enumerate(formants):
        H += (0.8 ** k) / (1 + ((f - F) / bw) ** 2)
    return np.fft.irfft(X * (H + 0.02), seg.size)


def _voiced(fs, dur, f0_start, f0_end, rng, jitter=0.01):
    n = int(dur * fs)
    t = np.arange(n) / fs
    f0 = np.linspace(f0_start, f0_end, n) * (1 + jitter * np.sin(2 * np.pi * 5.5 * t + rng.uniform(0, 6)))
    ph = 2 * np.pi * np.cumsum(f0) / fs
    sig = np.zeros(n)
    for k in range(1, int(4000 / max(f0_start, f0_end)) + 1):
        sig += np.sin(k * ph) / k
    return sig


def synth_speech(fs: float, dur: float = 2.5, f0: float = 130.0, rng=None) -> np.ndarray:
    """Speech-like: voiced syllables (0.12-0.28 s, ~4/s) with moving pitch and formants."""
    rng = rng or np.random.default_rng(0)
    x = np.zeros(int(dur * fs))
    t = 0.1
    vowels = [(700, 1200, 2600), (300, 2300, 3000), (500, 1000, 2500), (400, 1900, 2600), (600, 1700, 2500)]
    while t < dur - 0.3:
        d = rng.uniform(0.12, 0.28)
        p0 = f0 * rng.uniform(0.85, 1.2)
        seg = _formant_filter(_voiced(fs, d, p0, p0 * rng.uniform(0.85, 1.05), rng), fs,
                              vowels[rng.integers(len(vowels))])
        env = np.sin(np.pi * np.linspace(0, 1, seg.size)) ** 0.7
        i0 = int(t * fs)
        x[i0:i0 + seg.size] += seg * env
        t += d + rng.uniform(0.04, 0.12)
    return x / max(1e-9, np.abs(x).max())


def synth_groan(fs: float, dur: float = 2.0, f0: float = 110.0, rng=None) -> np.ndarray:
    """Sustained moan / groan: one long voiced sound, slow pitch fall, smooth envelope."""
    rng = rng or np.random.default_rng(1)
    seg = _formant_filter(_voiced(fs, dur, f0, f0 * 0.8, rng, jitter=0.02), fs, (500, 900, 2400))
    n = seg.size
    env = np.minimum(1, np.minimum(np.arange(n), n - np.arange(n)) / (0.25 * fs))
    x = np.concatenate([np.zeros(int(0.1 * fs)), seg * env, np.zeros(int(0.1 * fs))])
    return x / max(1e-9, np.abs(x).max())


def _noise(fs, n, lo, hi, rng):
    return _bandpass(rng.standard_normal(n), fs, lo, hi)


def synth_cough(fs: float, n: int = 1, rng=None) -> np.ndarray:
    """1-2 coughs: explosive onset, broadband noise, ~0.3 s decay."""
    rng = rng or np.random.default_rng(2)
    parts = [np.zeros(int(0.15 * fs))]
    for _ in range(n):
        L = int(rng.uniform(0.25, 0.45) * fs)
        t = np.arange(L) / fs
        env = (1 - np.exp(-t / 0.004)) * np.exp(-t / rng.uniform(0.07, 0.11))
        parts += [_noise(fs, L, 300, 4500, rng) * env, np.zeros(int(rng.uniform(0.25, 0.4) * fs))]
    x = np.concatenate(parts)
    return x / max(1e-9, np.abs(x).max())


def synth_breathing(fs: float, dur: float = 14.0, rate_bpm: float = 15.0, rng=None) -> np.ndarray:
    """Audible breathing: smooth unvoiced inhale / exhale noise bursts at ``rate_bpm``."""
    rng = rng or np.random.default_rng(3)
    n = int(dur * fs)
    x = np.zeros(n)
    T = 60.0 / rate_bpm
    t = rng.uniform(0.2, 0.8)
    done = False
    while not done and t < dur - 0.5:
        for frac, amp in ((0.38, 0.6), (0.48, 1.0)):              # inhale, exhale
            L = int(frac * T * fs)
            i0 = int(t * fs)
            if i0 + L > n:
                done = True
                break
            x[i0:i0 + L] += amp * _noise(fs, L, 300, 2500, rng) * np.hanning(L)
            t += frac * T + 0.07 * T
    return x / max(1e-9, np.abs(x).max())


def synth_signal(fs: float, kind: str = "whistles", rng=None) -> np.ndarray:
    """Cooperative signals: three whistles or a knock/tap pattern."""
    from .sim import clap, whistle
    rng = rng or np.random.default_rng(4)
    if kind == "whistles":
        parts = []
        for k in range(3):
            parts += [np.zeros(int(0.3 * fs)), whistle(fs, rng.uniform(0.35, 0.6), rng.uniform(1800, 2800), rng=rng)]
        x = np.concatenate(parts + [np.zeros(int(0.3 * fs))])
    else:
        parts = [np.zeros(int(0.2 * fs))]
        for _ in range(int(rng.integers(3, 6))):
            parts += [clap(fs, rng=rng), np.zeros(int(rng.uniform(0.25, 0.45) * fs))]
        x = np.concatenate(parts)
    return x / max(1e-9, np.abs(x).max())


def synth_movement(fs: float, dur: float = 3.0, rng=None) -> np.ndarray:
    """Shuffling / footsteps on debris: continuous rustle plus irregular crunches."""
    rng = rng or np.random.default_rng(5)
    n = int(dur * fs)
    env = np.abs(np.convolve(rng.standard_normal(n // 400 + 2), np.ones(3) / 3, "same"))
    env = np.interp(np.arange(n), np.linspace(0, n, env.size), env)
    x = 0.35 * _noise(fs, n, 1000, 6000, rng) * env
    t = rng.uniform(0.1, 0.3)
    while t < dur - 0.1:
        L = int(0.03 * fs)
        i0 = int(t * fs)
        x[i0:i0 + L] += rng.uniform(0.5, 1.0) * _noise(fs, L, 200, 5000, rng) * np.exp(-np.arange(L) / (0.008 * fs))
        t += rng.uniform(0.15, 0.6)
    pad = np.zeros(int(0.1 * fs))
    x = np.concatenate([pad, x, pad])
    return x / max(1e-9, np.abs(x).max())


def synth_example(category: str, fs: float, rng=None) -> np.ndarray:
    """One clean synthetic example of a category (randomised by ``rng``)."""
    rng = rng or np.random.default_rng(0)
    if category == "VOICE":
        return synth_speech(fs, rng.uniform(1.8, 3.0), rng.uniform(100, 220), rng)
    if category == "DISTRESS":
        return synth_groan(fs, rng.uniform(1.2, 2.5), rng.uniform(90, 180), rng)
    if category == "BREATH":
        return synth_breathing(fs, rng.uniform(12, 16), rng.uniform(10, 24), rng)
    if category == "COUGH":
        return synth_cough(fs, int(rng.integers(1, 3)), rng)
    if category == "SIGNAL":
        return synth_signal(fs, "whistles" if rng.uniform() < 0.5 else "taps", rng)
    if category == "MOVE":
        return synth_movement(fs, rng.uniform(2.0, 4.0), rng)
    raise ValueError(category)
