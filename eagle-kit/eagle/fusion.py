"""The life list: fuse every sensor's detections into one track per (possible) person.

Detection kinds (:class:`LifeDetection`)
----------------------------------------
* ``SOUND``  a life sound (VOICE, DISTRESS, BREATH, COUGH, SIGNAL, MOVE). With a floor point
  (bearing + elevation -> :func:`eagle.locate.fox_fix`) it is a point; otherwise bearing-only
  from the perch (``origin`` + ``az_deg``).
* ``HEAT``   warm body from the thermal camera: point + covariance.
* ``PERSON`` person box from a vision detector, projected to the floor: treated like HEAT.
* ``VITALS`` radar breathing / heart rate (or an explicit "no vital signs"): point = radar
  pellet position with its coverage as covariance, or perch bearing + range.
* ``TAG``    authenticated tag REPLY (tag id, covert-masked status) at the point that was polled.
* ``MOTION`` PIR pellet at a known position fired.
* ``RF``     allowlisted radio device: range-only ring around ``origin`` (the perch).
* ``IDENTITY`` match of a person box with the missing person's profile (:mod:`eagle.identity`):
  point + covariance of that box and ``match_level`` (P.ID_* 1..3). ADDITIVE ONLY: it is
  associated after this batch's births, never starts or moves a track, never changes a status,
  and only ever raises a track's score and its rank within its own status tier.

Association
-----------
* points: chi-square gate on the Mahalanobis distance with 2 dof (``gate_chi2`` = 9.21, 99 %),
  global greedy nearest-neighbour over each batch;
* bearing-only: innovation within +-3 sigma (bearing noise + track spread across the bearing),
  and only when exactly one track is inside the gate (two people on one bearing: unassociated);
  a SOUND point that fails its point gate falls back to this bearing test before it may
  start a new track;
* RF: the ring must contain exactly one track (|distance - range| <= 3 sigma); otherwise the
  detection is counted as unassociated and an allowlisted device gets an RF_ONLY entry
  (position = perch, ce = range), like the kit's tag-RSSI FIX.
Tracks are born only from HEAT / PERSON / SOUND points (``max_tracks``, default 12) and die
after ``death_s`` without evidence. Positions use an information filter (Y = P^-1, y = Y x)
with a small random-walk process noise.

Status (evaluated at ``now``)
-----------------------------
RESPONSIVE  voice, whistle/tap/knock signal or tag button within ``responsive_window_s``
BREATHING   radar breathing/heartbeat, breath, cough or distress sounds (alive, not responding)
MOVING      PIR motion or movement sounds only
WARM_ONLY   heat but no breathing/sound/motion after ``warm_timeout_s`` (or a radar pellet
            reported no vital signs): unconscious with undetected breathing, dead, or an object
RF_ONLY     only an allowlisted radio device
UNKNOWN     too early to tell
Flags: INJURED (tag status or a briefed code with status injured), DURESS (tag, Nest only).

Life score (0..1): 1 - prod_k (1 - w_k * exp(-age_k / tau)) over the evidence seen, with
weights voice/signal 0.95, radar breathing 0.9, tag button 0.9, distress 0.8, breath/cough
sound 0.7, motion 0.6, heat 0.35, tag reply 0.3, rf 0.15 and tau = ``score_tau_s`` (300 s);
halved when a radar reported no vital signs and nothing else shows life. An identity match
adds one more term (appearance candidate 0.3, strong appearance 0.5, face verified 0.8), so
it can only raise the score.

Triage priority (``summary`` order): BREATHING (alive but cannot help themselves) >
RESPONSIVE with INJURED/DURESS > MOVING > RESPONSIVE > WARM_ONLY > UNKNOWN > RF_ONLY.
Within a tier, a higher identity match level ranks first, then the score. Identity never
moves a track into a higher tier: an unresponsive stranger still outranks a matched person
who is responding.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import protocol as P

SOUND_CATEGORIES = ("VOICE", "DISTRESS", "BREATH", "COUGH", "SIGNAL", "MOVE")
POINT_KINDS = ("SOUND", "HEAT", "PERSON", "VITALS", "TAG", "MOTION")
BIRTH_KINDS = ("HEAT", "PERSON", "SOUND")
CHI2_2DOF_99 = 9.21

W = {"VOICE": 0.95, "SIGNAL": 0.95, "BREATH_RADAR": 0.9, "BUTTON": 0.9, "DISTRESS": 0.8, "BREATH": 0.7,
     "COUGH": 0.7, "MOTION": 0.6, "MOVE": 0.6, "HEAT": 0.35, "TAG": 0.3, "RF": 0.15}
W_IDENTITY = {P.ID_APPEARANCE: 0.3, P.ID_STRONG: 0.5, P.ID_FACE: 0.8}
PRIORITY = {P.LS_BREATHING: 0, P.LS_MOVING: 2, P.LS_RESPONSIVE: 3, P.LS_WARM_ONLY: 4,
            P.LS_UNKNOWN: 5, P.LS_RF_ONLY: 6}


@dataclass
class LifeDetection:
    kind: str                              # SOUND | HEAT | PERSON | VITALS | TAG | MOTION | RF
    t: float
    xy: np.ndarray | None = None           # ENU floor point (m)
    cov: np.ndarray | None = None          # 2x2 (m^2)
    origin: np.ndarray | None = None       # perch / pellet ENU xy (bearing-only, RF ring)
    az_deg: float | None = None            # world math azimuth for bearing-only SOUND
    sigma_az_deg: float = 2.0              # as doa.sigma_az_deg
    range_m: float | None = None           # RF ring radius
    sigma_range_m: float | None = None
    category: str | None = None            # SOUND category
    breath_bpm: float | None = None
    heart_bpm: float | None = None
    vitals: bool | None = None             # VITALS: True = vital signs found, False = checked, none
    temp_c: float | None = None
    tag_id: int | None = None
    tag_status_wire: int | None = None
    tag_chal16: int | None = None
    status_bits: int = 0                   # plaintext status known here (briefed code / tag at the Nest)
    rf_id: str | None = None               # allowlisted device label (never a stranger's id)
    match_level: int = 0                   # IDENTITY: P.ID_* (1 appearance, 2 strong, 3 face)
    match_appearance: float | None = None  # IDENTITY: appearance score 0..1
    match_face: float | None = None        # IDENTITY: ArcFace cosine (None = no usable face)
    source: str = ""
    update_position: bool = True           # False: gate/associate only (radar pellet, PIR, tag poll point)

    @property
    def is_point(self) -> bool:
        return self.xy is not None and self.cov is not None


def iso_cov(sigma_m: float) -> np.ndarray:
    return np.eye(2) * float(sigma_m) ** 2


@dataclass
class LifeTrack:
    id: int
    Y: np.ndarray                          # information matrix
    yv: np.ndarray                         # information vector
    born: float
    last: dict = field(default_factory=dict)        # kind -> last time seen
    sounds: dict = field(default_factory=dict)      # category -> last time
    counts: dict = field(default_factory=dict)      # kind -> number of detections
    breath_bpm: float | None = None
    heart_bpm: float | None = None
    vitals_t: float | None = None                   # last radar WITH vital signs
    novitals_t: float | None = None                 # last radar check WITHOUT vital signs
    temp_c: float | None = None
    tag_id: int | None = None
    tag_status_wire: int | None = None
    tag_chal16: int | None = None
    status_bits: int = 0
    rf_ids: set = field(default_factory=set)
    rf_only: bool = False
    rf_range: float | None = None
    identity_t: dict = field(default_factory=dict)  # match level -> last time (IDENTITY; never in `last`)
    t_pred: float = 0.0
    status: int = P.LS_UNKNOWN
    flags: int = 0
    score: float = 0.0
    id_level: int = 0                               # best identity level within life_window_s (evaluate)

    @property
    def x(self) -> np.ndarray:
        return np.linalg.solve(self.Y, self.yv)

    @property
    def P(self) -> np.ndarray:
        return np.linalg.inv(self.Y)

    @property
    def ce_m(self) -> float:
        if self.rf_only and self.rf_range is not None:
            return float(self.rf_range)
        return float(np.sqrt(max(np.linalg.eigvalsh(self.P).max(), 0.0)))

    @property
    def status_name(self) -> str:
        return P.LIFE_STATUS_NAMES[self.status]

    def evidence(self, now: float, window: float) -> int:
        ev = 0
        seen = lambda k: k in self.last and now - self.last[k] <= window     # noqa: E731
        snd = lambda c: c in self.sounds and now - self.sounds[c] <= window  # noqa: E731
        if seen("HEAT") or seen("PERSON"):
            ev |= P.EV_HEAT
        if any(snd(c) for c in ("SIGNAL", "COUGH", "MOVE", "BREATH", "DISTRESS")):
            ev |= P.EV_SOUND
        if snd("VOICE") or snd("DISTRESS"):
            ev |= P.EV_VOICE
        if (self.vitals_t is not None and now - self.vitals_t <= window) or snd("BREATH"):
            ev |= P.EV_BREATH
        if seen("MOTION") or snd("MOVE"):
            ev |= P.EV_MOTION
        if self.rf_ids or seen("RF"):
            ev |= P.EV_RF
        if self.tag_id:
            ev |= P.EV_TAG
        if self.novitals_t is not None and (self.vitals_t is None or self.novitals_t > self.vitals_t):
            ev |= P.EV_NOVITALS
        return ev


class LifeList:
    """Multi-sensor, multi-person track list (see module docstring)."""

    def __init__(self, *, perch_xy=(0.0, 0.0), max_tracks: int = 12, gate_chi2: float = CHI2_2DOF_99,
                 bearing_gate_sigma: float = 3.0, rf_gate_sigma: float = 3.0, q_m2_per_s: float = 0.0025,
                 death_s: float = 600.0, rf_death_s: float = 300.0, warm_timeout_s: float = 30.0,
                 responsive_window_s: float = 300.0, life_window_s: float = 300.0, score_tau_s: float = 300.0,
                 birth_sigma_floor_m: float = 0.3):
        self.perch = np.asarray(perch_xy, float)[:2]
        self.max_tracks, self.gate = int(max_tracks), float(gate_chi2)
        self.bgate, self.rgate = float(bearing_gate_sigma), float(rf_gate_sigma)
        self.q, self.death_s, self.rf_death_s = float(q_m2_per_s), float(death_s), float(rf_death_s)
        self.warm_timeout, self.resp_win = float(warm_timeout_s), float(responsive_window_s)
        self.life_win, self.tau = float(life_window_s), float(score_tau_s)
        self.birth_floor = float(birth_sigma_floor_m)
        self.tracks: list = []
        self.next_id = 1
        self.unassociated: dict = {}                     # kind -> count
        self.unlocated_sounds: list = []                 # bearing-only sounds that matched no track
        self.rf_anonymous = 0                            # non-allowlisted devices (count only)
        self.dropped_births = 0

    @classmethod
    def from_config(cls, d: dict, perch_xy=(0.0, 0.0)) -> "LifeList":
        import inspect
        keys = set(inspect.signature(cls.__init__).parameters) - {"self", "perch_xy"}
        return cls(perch_xy=perch_xy, **{k: v for k, v in (d or {}).items() if k in keys})

    # ---- filter steps ----------------------------------------------------------------------
    def _predict(self, tr: LifeTrack, t: float) -> None:
        dt = max(0.0, t - tr.t_pred)
        if dt > 0 and self.q > 0:
            x, Pm = tr.x, tr.P + np.eye(2) * self.q * dt
            tr.Y = np.linalg.inv(Pm)
            tr.yv = tr.Y @ x
        tr.t_pred = max(tr.t_pred, t)

    @staticmethod
    def _point_update(tr: LifeTrack, z, R) -> None:
        Ri = np.linalg.inv(R)
        tr.Y = tr.Y + Ri
        tr.yv = tr.yv + Ri @ np.asarray(z, float)

    @staticmethod
    def _bearing_update(tr: LifeTrack, origin, az_deg: float, sigma_deg: float) -> None:
        x = tr.x
        d = x - origin
        r2 = max(float(d @ d), 1e-6)
        H = np.array([-d[1], d[0]]) / r2
        h = np.arctan2(d[1], d[0])
        nu = (np.radians(az_deg) - h + np.pi) % (2 * np.pi) - np.pi
        s2 = np.radians(sigma_deg) ** 2
        tr.Y = tr.Y + np.outer(H, H) / s2
        tr.yv = tr.yv + H * (nu + H @ x) / s2

    @staticmethod
    def _range_update(tr: LifeTrack, origin, rng: float, sigma: float) -> None:
        x = tr.x
        d = x - origin
        r = max(float(np.hypot(*d)), 1e-6)
        H = d / r
        tr.Y = tr.Y + np.outer(H, H) / sigma ** 2
        tr.yv = tr.yv + H * (rng - r + H @ x) / sigma ** 2

    # ---- association ----------------------------------------------------------------------------
    def _person_tracks(self):
        return [t for t in self.tracks if not t.rf_only]

    def _d2(self, tr: LifeTrack, det: LifeDetection) -> float:
        v = det.xy - tr.x
        S = tr.P + det.cov
        return float(v @ np.linalg.solve(S, v))

    def _birth(self, det: LifeDetection) -> LifeTrack | None:
        if len(self._person_tracks()) >= self.max_tracks:
            self.dropped_births += 1
            return None
        R = det.cov + iso_cov(self.birth_floor)
        Y = np.linalg.inv(R)
        tr = LifeTrack(self.next_id, Y, Y @ np.asarray(det.xy, float), det.t, t_pred=det.t)
        self.next_id += 1
        self.tracks.append(tr)
        return tr

    def _attach(self, tr: LifeTrack, det: LifeDetection) -> None:
        """Copy a detection's attributes onto the track (position already updated)."""
        tr.last[det.kind] = max(tr.last.get(det.kind, det.t), det.t)
        tr.counts[det.kind] = tr.counts.get(det.kind, 0) + 1
        if det.kind == "SOUND" and det.category:
            tr.sounds[det.category] = max(tr.sounds.get(det.category, det.t), det.t)
        if det.kind == "VITALS":
            if det.vitals:
                tr.vitals_t = det.t
                tr.breath_bpm = det.breath_bpm or tr.breath_bpm
                tr.heart_bpm = det.heart_bpm or tr.heart_bpm
            elif det.vitals is False:
                tr.novitals_t = det.t
        if det.temp_c is not None:
            tr.temp_c = det.temp_c
        if det.kind == "TAG" and det.tag_id:
            tr.tag_id, tr.tag_status_wire, tr.tag_chal16 = det.tag_id, det.tag_status_wire, det.tag_chal16
        tr.status_bits |= det.status_bits
        if det.kind == "RF" and det.rf_id:
            tr.rf_ids.add(det.rf_id)

    def update(self, detections, now: float | None = None) -> None:
        """Fuse a batch of detections (any kinds, any order)."""
        everything = list(detections)
        idents = [d for d in everything if d.kind == "IDENTITY"]
        dets = [d for d in everything if d.kind != "IDENTITY"]
        if not dets:                         # identity alone never predicts or prunes (no side effects)
            for det in idents:
                self._identity(det)
            return
        now = max(d.t for d in dets) if now is None else now
        for tr in self.tracks:
            self._predict(tr, now)
        points = [d for d in dets if d.kind in POINT_KINDS and d.is_point]
        # global greedy nearest neighbour over all (detection, track) pairs inside the gate
        pairs = []
        for i, d in enumerate(points):
            for tr in self._person_tracks():
                d2 = self._d2(tr, d)
                if d2 <= self.gate:
                    pairs.append((d2, i, tr.id))
        pairs.sort()
        used_d, used_t = set(), set()
        by_id = {t.id: t for t in self.tracks}
        for d2, i, tid in pairs:
            if i in used_d or tid in used_t:
                continue
            tr, det = by_id[tid], points[i]
            if det.update_position:
                self._point_update(tr, det.xy, det.cov)
            self._attach(tr, det)
            used_d.add(i)
            used_t.add(tid)
        for i, det in enumerate(points):
            if i in used_d:
                continue
            if det.kind == "SOUND" and det.az_deg is not None and self._bearing(det, count_miss=False):
                continue                     # range was off but the bearing fits a track
            if det.kind in BIRTH_KINDS:
                tr = self._birth(det)
                if tr is not None:
                    self._attach(tr, det)
                    continue
            self.unassociated[det.kind] = self.unassociated.get(det.kind, 0) + 1
        for det in dets:
            if det.kind == "SOUND" and not det.is_point and det.az_deg is not None:
                self._bearing(det)
            elif det.kind == "RF" and det.range_m is not None:
                self._rf(det)
        for det in idents:                   # after births, so a person first seen in this batch gets it
            self._identity(det)
        self._prune(now)

    def _identity(self, det: LifeDetection) -> None:
        """Attach a match level to the nearest person track in the gate. Never births, never moves
        a track, never touches ``last`` (status and track death ignore identity)."""
        lvl = int(det.match_level or 0)
        cand = [(self._d2(tr, det), tr) for tr in self._person_tracks()] if det.is_point and lvl > 0 else []
        cand = [c for c in cand if c[0] <= self.gate]
        if not cand:
            if lvl > 0:
                self.unassociated["IDENTITY"] = self.unassociated.get("IDENTITY", 0) + 1
            return
        tr = min(cand, key=lambda c: c[0])[1]
        tr.identity_t[lvl] = max(tr.identity_t.get(lvl, det.t), det.t)
        tr.counts["IDENTITY"] = tr.counts.get("IDENTITY", 0) + 1

    def _bearing(self, det: LifeDetection, count_miss: bool = True) -> bool:
        o = np.asarray(det.origin if det.origin is not None else self.perch, float)[:2]
        inside = []
        for tr in self._person_tracks():
            d = tr.x - o
            r2 = max(float(d @ d), 1e-6)
            H = np.array([-d[1], d[0]]) / r2
            s = np.sqrt(np.radians(det.sigma_az_deg) ** 2 + H @ tr.P @ H)
            nu = (np.radians(det.az_deg) - np.arctan2(d[1], d[0]) + np.pi) % (2 * np.pi) - np.pi
            if abs(nu) / s <= self.bgate:
                inside.append(tr)
        # several tracks on the same bearing: ambiguous. Never guess - a wrong guess could
        # mark an unconscious person as RESPONSIVE.
        best = inside[0] if len(inside) == 1 else None
        if best is None:
            if count_miss:
                self.unassociated["SOUND"] = self.unassociated.get("SOUND", 0) + 1
                self.unlocated_sounds.append(det)
            return False
        self._bearing_update(best, o, det.az_deg, det.sigma_az_deg)
        self._attach(best, det)
        return True

    def _rf(self, det: LifeDetection) -> None:
        o = np.asarray(det.origin if det.origin is not None else self.perch, float)[:2]
        sr = float(det.sigma_range_m or max(1.0, 0.5 * det.range_m))
        inside = []
        for tr in self._person_tracks():
            d = tr.x - o
            r = float(np.hypot(*d))
            u = d / max(r, 1e-6)
            s = np.sqrt(sr ** 2 + u @ tr.P @ u)
            n = abs(r - det.range_m) / s
            if n <= self.rgate:
                inside.append((n, tr))
        if len(inside) == 1:
            tr = inside[0][1]
            self._range_update(tr, o, det.range_m, sr)
            self._attach(tr, det)
            return
        self.unassociated["RF"] = self.unassociated.get("RF", 0) + 1
        if not det.rf_id:
            return
        tr = next((t for t in self.tracks if t.rf_only and det.rf_id in t.rf_ids), None)
        if tr is None:
            Y = np.linalg.inv(iso_cov(max(det.range_m, 1.0)))
            tr = LifeTrack(self.next_id, Y, Y @ o, det.t, rf_only=True, t_pred=det.t)
            self.next_id += 1
            self.tracks.append(tr)
        tr.rf_range = float(det.range_m)
        self._attach(tr, det)

    def _prune(self, now: float) -> None:
        keep = []
        for tr in self.tracks:
            last = max(tr.last.values()) if tr.last else tr.born
            if now - last <= (self.rf_death_s if tr.rf_only else self.death_s):
                keep.append(tr)
        self.tracks = keep

    # ---- status ---------------------------------------------------------------------------------
    def evaluate(self, now: float) -> list:
        """Update status / flags / score of every track at time ``now``; returns the tracks."""
        for tr in self.tracks:
            snd = lambda c, w: c in tr.sounds and now - tr.sounds[c] <= w     # noqa: E731
            seen = lambda k, w: k in tr.last and now - tr.last[k] <= w         # noqa: E731
            button = bool(tr.status_bits & P.ST_BUTTON)
            resp = snd("VOICE", self.resp_win) or snd("SIGNAL", self.resp_win) or button
            radar_alive = tr.vitals_t is not None and now - tr.vitals_t <= self.life_win
            breathing = radar_alive or any(snd(c, self.life_win) for c in ("BREATH", "COUGH", "DISTRESS"))
            moving = seen("MOTION", self.life_win) or snd("MOVE", self.life_win)
            warm = "HEAT" in tr.last or "PERSON" in tr.last
            radar_none = tr.novitals_t is not None and (tr.vitals_t is None or tr.novitals_t > tr.vitals_t)
            if tr.rf_only:
                st = P.LS_RF_ONLY
            elif resp:
                st = P.LS_RESPONSIVE
            elif breathing:
                st = P.LS_BREATHING
            elif moving:
                st = P.LS_MOVING
            elif warm and (now - tr.born >= self.warm_timeout or radar_none):
                st = P.LS_WARM_ONLY
            elif not warm and set(tr.last) <= {"RF"}:
                st = P.LS_RF_ONLY
            else:
                st = P.LS_UNKNOWN
            tr.status = st
            tr.flags = (P.LF_INJURED if tr.status_bits & P.ST_INJURED else 0) | \
                       (P.LF_DURESS if tr.status_bits & P.ST_DURESS else 0)
            tr.id_level = max((lv for lv, t in tr.identity_t.items() if now - t <= self.life_win), default=0)
            tr.score = self._score(tr, now, radar_none and not (resp or breathing or moving))
        return self.tracks

    def _score(self, tr: LifeTrack, now: float, penalise: bool) -> float:
        terms = []
        for cat, t in tr.sounds.items():
            terms.append((W.get(cat, 0.5), now - t))
        if tr.vitals_t is not None:
            terms.append((W["BREATH_RADAR"], now - tr.vitals_t))
        for k in ("HEAT", "PERSON"):
            if k in tr.last:
                terms.append((W["HEAT"], now - tr.last[k]))
        if "MOTION" in tr.last:
            terms.append((W["MOTION"], now - tr.last["MOTION"]))
        if "TAG" in tr.last:
            terms.append((W["BUTTON"] if tr.status_bits & P.ST_BUTTON else W["TAG"], now - tr.last["TAG"]))
        if "RF" in tr.last:
            terms.append((W["RF"], now - tr.last["RF"]))
        for lv, t in tr.identity_t.items():         # one more factor < 1: can only raise the score
            terms.append((W_IDENTITY.get(lv, 0.0), now - t))
        miss = 1.0
        for w, age in terms:
            miss *= 1.0 - w * np.exp(-max(age, 0.0) / self.tau)
        s = 1.0 - miss
        return float(np.clip(s * (0.5 if penalise else 1.0), 0.0, 1.0))

    # ---- output ---------------------------------------------------------------------------------
    def ranked(self, now: float) -> list:
        self.evaluate(now)

        def key(tr):
            pr = PRIORITY[tr.status]
            if tr.status == P.LS_RESPONSIVE and tr.flags:
                pr = 1
            return (pr, -tr.id_level, -tr.score, tr.id)      # identity: tie-break inside a tier only
        return sorted(self.tracks, key=key)

    def summary(self, now: float) -> str:
        """Human-readable table, highest triage priority first."""
        rows = [f"{'id':>3} {'status':<10} {'flags':<14} {'east':>6} {'north':>6} {'ce':>5} "
                f"{'breath':>6} {'heart':>5} {'score':>5}  evidence"]
        for tr in self.ranked(now):
            x = tr.x
            fl = ",".join(n for b, n in ((P.LF_INJURED, "INJURED"), (P.LF_DURESS, "DURESS")) if tr.flags & b)
            ev = ",".join(P.evidence_names(tr.evidence(now, self.life_win)))
            br = f"{tr.breath_bpm:.0f}" if tr.breath_bpm else "-"
            hr = f"{tr.heart_bpm:.0f}" if tr.heart_bpm else "-"
            if tr.tag_id:
                ev += f" tag{tr.tag_id}"
            if tr.rf_only:
                ev += f" (ring {tr.rf_range:.0f} m around the perch)"
            if tr.id_level:
                ev += f" ID-{P.ID_LEVEL_NAMES[tr.id_level]}"
            rows.append(f"{tr.id:>3} {tr.status_name:<10} {fl or '-':<14} {x[0]:6.1f} {x[1]:6.1f} {tr.ce_m:5.1f} "
                        f"{br:>6} {hr:>5} {tr.score:5.2f}  {ev}")
        extra = []
        if self.unlocated_sounds:
            extra.append(f"{len(self.unlocated_sounds)} bearing-only sound(s) matched no track")
        if self.rf_anonymous:
            extra.append(f"{self.rf_anonymous} non-allowlisted radio device(s) (count only)")
        if self.unassociated:
            extra.append("unassociated: " + ", ".join(f"{k} {v}" for k, v in sorted(self.unassociated.items())))
        return "\n".join(rows + (["  " + "; ".join(extra)] if extra else []))

    def life_persons(self, now: float) -> list:
        """Tracks as :class:`eagle.protocol.LifePerson` records (offsets from the perch)."""
        out = []
        for tr in self.ranked(now):
            x = tr.x - self.perch
            ev = tr.evidence(now, self.life_win)
            out.append(P.LifePerson(tr.id, float(x[0]), float(x[1]), tr.ce_m, tr.status, tr.flags, tr.score,
                                    float(tr.breath_bpm or 0), float(tr.heart_bpm or 0), ev,
                                    int(tr.tag_id or 0), int(tr.tag_status_wire or 0), int(tr.tag_chal16 or 0)))
        return out
