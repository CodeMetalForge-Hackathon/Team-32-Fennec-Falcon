"""detect -> match code -> DOA -> world bearing -> position -> lat/lon.

Shared by apps/eagle_node.py (live / WAV) and apps/sim_demo.py so the demo exercises the
exact code that runs at the venue.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

from . import protocol as P
from .config import ArrayGeometry, array_geometry
from .detect import CodeSpec, Match, detect_all, match_codes
from .doa import DoaResult, SrpPhat, speed_of_sound
from .geo import LocalFrame
from .locate import (FoxFix, body_to_world, circular_error, compass_bearing, dem_fix, error_ellipse,
                     fox_fix, fox_fix_covariance)


@dataclass
class Detection:
    match: Match
    doa: DoaResult
    az_world: float                  # math azimuth in ENU (CCW from east)
    el_world: float
    bearing_deg: float               # compass bearing (CW from true north)
    cls: int
    status: int = 0
    xyz: np.ndarray | None = None    # ENU position of the source, if a fix was possible
    fix: FoxFix | None = None
    cov: np.ndarray | None = None    # 2x2 ENU covariance (m^2)
    ce_m: float | None = None        # 1-sigma (worst axis), for CoT / FIX
    ellipse95: tuple | None = None
    lat: float | None = None
    lon: float | None = None
    hae: float | None = None
    note: str = ""

    @property
    def has_fix(self) -> bool:
        return self.xyz is not None


def dedupe_matches(matches: list, code_order: list) -> list:
    """Overlapping matches (sharing events) keep the larger one, then the earlier code."""
    rank = {c: i for i, c in enumerate(code_order)}
    chosen, used = [], set()
    for m in sorted(matches, key=lambda m: (-len(m.events), rank.get(m.code, 99), m.start)):
        ids = {(e.kind, e.start) for e in m.events}
        if ids & used:
            continue
        chosen.append(m)
        used |= ids
    return sorted(chosen, key=lambda m: m.start)


def noise_segments_for(match: Match, events: list, n_samples: int, fs: float,
                       guard_s: float = 0.1, lookback_s: float = 3.0) -> list:
    """Noise-only stretches near a match (between/around events) for SNR weighting."""
    lo = max(0, match.start - int(lookback_s * fs))
    hi = min(n_samples, match.end + int(0.5 * fs))
    busy = sorted((max(lo, e.start - int(guard_s * fs)), min(hi, e.end + int(guard_s * fs)))
                  for e in events if e.end > lo and e.start < hi)
    segs, cur = [], lo
    for s, e in busy:
        if s > cur:
            segs.append((cur, s))
        cur = max(cur, e)
    if cur < hi:
        segs.append((cur, hi))
    return [s for s in segs if s[1] - s[0] >= int(0.05 * fs)]


class Locator:
    """Holds the array, perch pose and site frame; turns audio into :class:`Detection` s."""

    def __init__(self, cfg: dict, *, geometry: ArrayGeometry | None = None, fs: float | None = None,
                 perch_enu=None, heading_deg: float | None = None):
        self.cfg = cfg
        self.geom = geometry or array_geometry(cfg, fs=fs)
        self.fs = float(fs or self.geom.fs)
        d = cfg.get("doa", {})
        a = cfg.get("audio", {})
        self.c = speed_of_sound(a.get("temperature_c", 20.0))
        self.srp = SrpPhat(self.geom.positions, self.fs, c=self.c, band=tuple(d.get("band_hz", (300, 6000))),
                           frame_s=d.get("frame_s", 0.064), az_step=d.get("az_step_deg", 3.0),
                           el_step=d.get("el_step_deg", 3.0), el_range=tuple(d.get("el_range_deg", (-90, 20))),
                           hemisphere=d.get("hemisphere", "lower"))
        self.min_conf = float(d.get("min_confidence", 0.3))
        self.onset_s = d.get("onset_s")              # None = whole events; e.g. 0.1 in reverberant halls
        self.el_bias = float(d.get("el_bias_deg", 0.0) or 0.0)
        self.sigma_az, self.sigma_el = float(d.get("sigma_az_deg", 2.0)), float(d.get("sigma_el_deg", 3.0))
        self.code_dicts = list(cfg.get("codes", []))
        self.codes = [CodeSpec.from_dict(c) for c in self.code_dicts]
        self.detect_cfg = cfg.get("detect", {})
        perch = cfg.get("perch", {})
        self.perch = np.asarray(perch_enu if perch_enu is not None else perch.get("enu_m", (0, 0, 5)), float)
        self.heading = float(heading_deg if heading_deg is not None else perch.get("heading_deg", 0.0))
        self.az_offset = float(perch.get("az_offset_deg", 0.0))
        self.pitch, self.roll = float(perch.get("pitch_deg", 0.0)), float(perch.get("roll_deg", 0.0))
        site = cfg.get("site", {})
        o = site.get("origin", {"lat": 0.0, "lon": 0.0, "hae_m": 0.0})
        self.frame = LocalFrame(o["lat"], o["lon"], o.get("hae_m", 0.0))
        self.floor_z = float(site.get("floor_z", 0.0))
        self.src_h = float(site.get("source_height_m", 0.0))
        self.dem = None
        if site.get("dem"):
            self.dem = np.load(os.path.expanduser(site["dem"]))
            self.dem_origin = tuple(site.get("dem_origin", (0.0, 0.0)))
            self.dem_spacing = site.get("dem_spacing_m", 1.0)

    # ---- steps ---------------------------------------------------------------------------
    def detect(self, audio) -> tuple:
        events = detect_all(audio, self.fs, self.detect_cfg)
        matches = dedupe_matches(match_codes(events, self.codes), [c.name for c in self.codes])
        return events, matches

    def segments(self, match: Match) -> list:
        """Audio used for DOA: whole events, or only the first ``onset_s`` of each whistle
        (precedence effect: the direct sound arrives before the room's reflections build up)."""
        if not self.onset_s:
            return match.segments
        pre, on = int(0.02 * self.fs), int(float(self.onset_s) * self.fs)
        return [(max(0, e.start - pre), min(e.end, e.start + on)) if e.kind == "whistle" else (e.start, e.end)
                for e in match.events]

    def doa(self, audio, match: Match, events: list) -> DoaResult:
        noise = noise_segments_for(match, events, np.shape(audio)[1], self.fs)
        res = self.srp.localize(audio, self.segments(match), noise_segments=noise)
        if self.el_bias:
            res.el = float(np.clip(res.el + self.el_bias, -90.0, 90.0))
        return res

    def locate(self, doa: DoaResult, match: Match) -> Detection:
        azw, elw = body_to_world(doa.az, doa.el, heading_deg=self.heading, pitch_deg=self.pitch,
                                 roll_deg=self.roll, az_offset_deg=self.az_offset)
        spec = next((c for c in self.codes if c.name == match.code), None)
        status = P.status_from_names(spec.status) if spec and spec.status else 0
        cls = P.CLS_WHISTLE if match.kind == "whistle" else P.CLS_TAP if match.kind == "tap" else P.CLS_UNKNOWN
        det = Detection(match, doa, azw, elw, float(compass_bearing(azw)), cls, status)
        if doa.confidence < self.min_conf:
            det.note = f"confidence {doa.confidence:.2f} < {self.min_conf}: bearing only"
            return det
        try:
            if self.dem is not None:
                hit = dem_fix(self.perch, (azw, elw), self.dem, self.dem_origin, self.dem_spacing,
                              source_height=self.src_h)
                if hit is None:
                    det.note = "ray did not hit the DEM: bearing only"
                    return det
                ground = float(np.hypot(*(hit[:2] - self.perch[:2])))
                h = float(self.perch[2] - hit[2])
                det.fix = FoxFix(hit, ground, float(np.hypot(ground, h)), -elw, h, azw)
            else:
                det.fix = fox_fix(azw, elw, self.perch, floor_z=self.floor_z, source_height=self.src_h)
        except ValueError as e:
            det.note = f"{e}: bearing only"
            return det
        det.xyz = det.fix.xyz
        det.cov = fox_fix_covariance(det.fix, self.sigma_az, self.sigma_el)
        det.ce_m = circular_error(det.cov)
        det.ellipse95 = error_ellipse(det.cov)
        det.lat, det.lon, det.hae = self.frame.to_geodetic(*det.xyz)
        return det

    def process(self, audio) -> tuple:
        """-> (events, [Detection]) for every briefed code found in ``audio``."""
        events, matches = self.detect(audio)
        out = []
        for m in matches:
            out.append(self.locate(self.doa(audio, m, events), m))
        return events, out

    def fix_body(self, det: Detection, t_unix: int, *, sim: bool = False) -> P.FixBody:
        """FIX payload for an acoustic detection (tag fields filled in by the poll step)."""
        return P.FixBody(lat=det.lat, lon=det.lon, alt_m=det.hae or 0.0, ce_m=det.ce_m or 0.0,
                         cls=det.cls | (P.CLS_SIM if sim else 0), confidence=det.doa.confidence,
                         t_unix=t_unix, status=det.status)


def describe(det: Detection) -> str:
    s = (f"bearing {det.bearing_deg:5.1f} deg true, el {det.el_world:5.1f} deg "
         f"(body az {det.doa.az:5.1f} el {det.doa.el:5.1f}, conf {det.doa.confidence:.2f})")
    if det.has_fix:
        a, b, ang = det.ellipse95
        s += (f" -> range {det.fix.ground_range:.1f} m, E {det.xyz[0]:.1f} N {det.xyz[1]:.1f}"
              f" -> {det.lat:.6f}, {det.lon:.6f}  ce {det.ce_m:.1f} m (95%: {a:.1f} x {b:.1f} m)")
    elif det.note:
        s += f" [{det.note}]"
    return s
