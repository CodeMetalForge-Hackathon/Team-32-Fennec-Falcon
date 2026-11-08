"""On-board decision: is this the person we are looking for, and may the Eagle break silence?

Runs on the Pi after the life list (:mod:`eagle.fusion`). Every tick it answers two questions
for every track, and writes the answer and its reasons to the decision log.

1. Is it him?
   Log-odds that the track is the briefed person rather than anyone or anything else that may
   come into view. Prior = 1 / (1 + ``mission.others_expected``). Each clue multiplies the odds
   by a likelihood ratio LR = P(clue | him) / P(clue | someone else). The default LRs are the
   simulator v2 values (docs/simulator-v2.md) plus a few new ones, all *assumed* until they are
   calibrated on field recordings (``apps/eagle_runtime.py --record`` and the decision log).

   Clues come in two kinds. State clues are re-evaluated every tick and can switch off (alone,
   in a group, still for 30 min / 2 h, moving, inside the search area). Sensor clues keep the
   best value seen for the track (thermal person shape, camera person box, identity match
   level, radar breathing, authenticated tag) or add one factor per event up to a cap (cough,
   distress, breath sound, briefed code). Clues with LR < 1 (in a group, moving, radar says
   no vital signs) pull P down: this is what keeps a resting patrol member or a warm decoy
   from being reported.

2. May it transmit?
   SEND only when every gate holds:
   * P >= ``p_send`` for the mode (strict 0.99, default 0.95, training 0.95 but radio muted);
   * corroboration: the briefed-code rule (>= 3 code events within 180 s on bearings within
     +-10 deg, mean confidence >= 0.9 when the detector reports one) OR >= 2 independent sensor
     modalities (thermal, acoustic, vision, radar, tag), so one detector's false alarm can
     never report on its own;
   * dwell: the track is >= ``min_dwell_s`` old and was seen within ``fresh_s``;
   * fix: circular error <= ``max_ce_m``;
   * EMCON: no unknown person within the burst's intercept radius of the perch and no briefed
     threat inside it (``mission.threat_dist_m``). Otherwise HOLD and ask to relocate
     (``REQUEST_RELOCATE`` in the decision log; ``touch ~/eagle/relocated`` or the autopilot
     signals it is done). In default mode an injured person's report goes anyway after
     ``max_hold_injured_s`` (15 min); strict mode never sends from the perch while blocked.

   One report per person: a report goes out as two bursts of the same sealed frame 30-120 s
   apart (the Nest's replay window keeps one; one marker in ATAK), then the subject is
   REPORTED. It is re-sent only if he moved > ``resend_move_m``, the identity level rose or
   the flags changed, at most ``max_per_hour`` reports in all. Strict mode never re-sends,
   never logs positions and drops the fix from memory after the second burst.

Bearing-only: briefed-code events heard on a bearing with no track (thermal fails in the
afternoon, Fennec V without elevation) form bearing candidates. A confirmed code on a stable
bearing can be reported as the circle that covers the ray from the perch out to
``bearing_only.range_m``.

Everything here is pure logic: no hardware, no radio. :mod:`apps.eagle_runtime` feeds it and
does the sending. Numbers in this file are *modelled* or *assumed*, never measured.
"""
from __future__ import annotations

import json
import math
import os
import random
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import protocol as P
from .config import deep_merge
from .fusion import LifeDetection, LifeList

MODES = ("strict", "default", "training")
MODALITIES = ("thermal", "acoustic", "vision", "radar", "tag")

DEFAULTS: dict = {
    "mode": "default",
    "modes": {
        "strict": {"p_send": 0.99, "min_modalities": 2, "on_hold_timeout": "hold", "resend": False,
                   "wipe_after_send": True, "log_positions": False, "transmit": True},
        "default": {"p_send": 0.95, "min_modalities": 2, "on_hold_timeout": "send", "resend": True,
                    "wipe_after_send": False, "log_positions": True, "transmit": True},
        "training": {"p_send": 0.95, "min_modalities": 2, "on_hold_timeout": "send", "resend": True,
                     "wipe_after_send": False, "log_positions": True, "transmit": False},
    },
    "mission": {
        "others_expected": 6,          # people who may come into view besides him (sim v2: patrol 4 + locals 2)
        "target_state": "injured",     # injured | mobile: how long a decided report may be held
        "target_mobile": False,        # False: sustained movement counts against a track
        "search_area": None,           # {center_enu_m: [E, N], radius_m} or {lat, lon, radius_m}
        "threat_dist_m": None,         # briefed distance to the nearest listener (patrol, outpost); None = unknown
    },
    "lr": {                            # P(clue | him) / P(clue | someone else); assumed until calibrated
        "shape_clear": 3.0, "shape_faint": 1.5, "shape_clear_score": 0.6,
        "vision_lying": 2.0, "vision_upright": 1.0,
        "identity": {1: 2.0, 2: 5.0, 3: 30.0},       # appearance candidate, strong appearance, face verified
        "alone": 2.0, "location": 2.0, "still30": 4.0, "still120": 3.0,
        "group": 0.3, "moving": 0.5,
        "code_event": 3.0, "code": 100.0,            # per unconfirmed code event; total once the code rule holds
        "signal": 1.5, "signal_max": 2,              # whistle / knock that is not the briefed pattern
        "cough": 2.0, "cough_max": 4,
        "distress": 2.0, "distress_max": 2,
        "breath_sound": 1.5, "breath_sound_max": 2,
        "voice": 1.0, "voice_max": 0,                # talking: human, not him in particular (patrols talk)
        "breathing": 2.0, "no_vitals": 0.5, "tag": 1000.0,
    },
    "code": {"window_s": 180.0, "min_events": 3, "bearing_tol_deg": 10.0, "min_conf": 0.9},
    "gate": {"min_dwell_s": 20.0, "fresh_s": 120.0, "max_ce_m": 200.0},
    "geometry": {"merge_radius_m": 1.5, "alone_radius_m": 30.0, "group_radius_m": 5.0,
                 "still_radius_m": 3.0, "still_sample_s": 10.0, "moving_window_s": 60.0, "moving_m": 10.0,
                 "inherit_radius_m": 5.0, "inherit_s": 1800.0},
    "emcon": {"min_radius_m": 30.0, "eavesdropper_dbm": -115.0, "clear_s": 120.0,
              "max_hold_s": 7200.0, "max_hold_injured_s": 900.0, "relocated_flag": "~/eagle/relocated"},
    "report": {"repeat_after_s": [30.0, 120.0], "resend_move_m": 25.0, "min_resend_gap_s": 300.0,
               "max_per_hour": 4,
               "own_position_ce_m": 10.0},     # the Eagle's own position error without GPS (sim v2, VIO: assumed)
    "bearing_only": {"enabled": True, "range_m": 150.0, "stable_span_s": 300.0, "lr_stable": 2.0},
    "log": {"path": "~/eagle/decisions.jsonl", "every_s": 10.0},
}

# actions
WATCH, HOLD, SEND, REPORTED = "WATCH", "HOLD", "SEND", "REPORTED"


# ---------------------------------------------------------------------------------- helpers
def _odds_to_p(lo: float) -> float:
    if lo >= 0:
        return 1.0 / (1.0 + math.exp(-lo))
    e = math.exp(lo)
    return e / (1.0 + e)


def prior_log_odds(others_expected: float) -> float:
    """Prior = 1 / (1 + N): he is one of N + 1 people who may come into view (clamped to [1e-4, 0.5])."""
    p0 = min(max(1.0 / (1.0 + max(float(others_expected), 0.0)), 1e-4), 0.5)
    return math.log(p0 / (1.0 - p0))


def ang_diff_deg(a: float, b: float) -> float:
    return (a - b + 180.0) % 360.0 - 180.0


def circ_mean_deg(angles) -> float:
    r = np.radians(np.asarray(list(angles), float))
    return float(np.degrees(np.arctan2(np.sin(r).mean(), np.cos(r).mean())))


def code_rule(events, min_events: int = 3, tol_deg: float = 10.0, min_conf: float = 0.9) -> tuple:
    """Briefed-code corroboration on (t, az_deg, conf) events already inside the time window.

    Returns (holds, n_best): the largest set of events whose bearings lie within +-tol_deg of
    their circular mean, and whether it has >= min_events with mean confidence >= min_conf
    (events without a confidence are not held to it)."""
    ev = [e for e in events if e[1] is not None]
    best: list = []
    for _, az0, _ in ev:                          # each event's bearing as a seed, then refine once
        sub = [e for e in ev if abs(ang_diff_deg(e[1], az0)) <= tol_deg]
        m = circ_mean_deg(e[1] for e in sub)
        sub = [e for e in ev if abs(ang_diff_deg(e[1], m)) <= tol_deg]
        if len(sub) > len(best):
            best = sub
    confs = [e[2] for e in best if e[2] is not None]
    conf_ok = not confs or float(np.mean(confs)) >= min_conf
    return len(best) >= min_events and conf_ok, len(best)


def intercept_radius_m(tx_dbm: float, eavesdropper_dbm: float = -115.0, pl_d0_db: float = 31.7,
                       exponent: float = 3.0, d0_m: float = 1.0) -> float:
    """Distance at which a listener with sensitivity ``eavesdropper_dbm`` still hears a burst at
    ``tx_dbm`` under the kit's log-distance model (radio.path_loss). Modelled, not measured:
    terrain and canopy can make it much shorter or, with line of sight, longer."""
    budget = float(tx_dbm) - float(eavesdropper_dbm) - float(pl_d0_db)
    return float(d0_m) * 10.0 ** (budget / (10.0 * float(exponent)))


def planned_tx_from_config(radio: dict) -> float:
    """FIX power the radio's power control would pick with no Nest BEACON heard: sensitivity +
    margin + path loss to ``radio.nest_link`` (configured dB, or the log-distance model at its
    distance), clamped to the radio's limits. Same rule as :class:`eagle.node.EagleRadio`."""
    sens = P.lora_sensitivity(int(radio.get("sf", 7)), float(radio.get("bw_khz", 250)),
                              float(radio.get("noise_figure_db", 6.0)))
    pm, nl = radio.get("path_loss") or {}, radio.get("nest_link") or {}
    pl = nl.get("path_loss_db")
    if pl is None:
        d = max(float(nl.get("distance_m", 1000.0)), 1.0)
        pl = float(pm.get("pl_d0_db", 31.7)) + 10 * float(pm.get("exponent", 3.0)) * math.log10(d / float(pm.get("d0_m", 1.0)))
    tx = P.required_tx_dbm(sens, float(pl), float(radio.get("margin_db", 10.0)), int(radio.get("min_dbm", -9)),
                           int(radio.get("max_dbm", 22)))
    if radio.get("force_dbm") is not None:
        tx = min(max(int(radio["force_dbm"]), int(radio.get("min_dbm", -9))), int(radio.get("max_dbm", 22)))
    return float(tx)


def false_report_rate_per_h(false_events_per_h: float, k: int = 3, window_s: float = 180.0,
                            bearing_tol_deg: float | None = 10.0) -> float:
    """Expected false briefed-code confirmations per hour per Eagle (Poisson model).

    k coincident false events inside a sliding window T occur at about
    lam^k T^(k-1) / (k-1)! per unit time (lam T << 1). Requiring the other k-1 events on the
    first one's bearing +-tol multiplies by (2 tol / 360)^(k-1) for bearings spread uniformly.
    Because it scales with lam^k, halving the detector's false-alarm rate cuts false reports
    8-fold at k = 3. Modelled; real false alarms cluster (a bird repeating a call)."""
    lam, T = float(false_events_per_h), float(window_s) / 3600.0
    rate = lam ** k * T ** (k - 1) / math.factorial(k - 1)
    if bearing_tol_deg is not None:
        rate *= (2.0 * float(bearing_tol_deg) / 360.0) ** (k - 1)
    return rate


# ---------------------------------------------------------------------------------- beliefs
@dataclass
class Clue:
    key: str
    lr: float
    modality: str          # thermal | acoustic | vision | radar | tag | context
    note: str = ""


@dataclass
class Belief:
    """What the Eagle believes about one subject: a life-list track or a bearing candidate."""
    subject: str
    track_id: int | None
    born: float
    prior_lo: float
    last_seen: float = 0.0
    xy: np.ndarray | None = None
    ce_m: float | None = None
    status_bits: int = 0
    cls: int = P.CLS_UNKNOWN
    # sensor clues (best so far)
    best_shape: float = 0.0
    shape_seen: bool = False
    vision: str | None = None              # None | upright | lying
    id_level: int = 0
    breathing: bool = False
    no_vitals: bool = False
    tag: bool = False
    events: dict = field(default_factory=dict)          # key -> count of event clues
    code_events: deque = field(default_factory=deque)   # (t, az_deg, conf) inside the window
    code_best_n: int = 0
    code_confirmed_t: float | None = None
    az_deg: float | None = None                          # bearing candidates
    first_t: float = 0.0
    # geometry clues (re-evaluated every tick)
    hist: deque = field(default_factory=deque)          # (t, x, y)
    geo: dict = field(default_factory=dict)             # key -> Clue
    # decision state
    action: str = WATCH
    reason: str = ""
    hold_t: float | None = None
    blocked_t: float | None = None
    relocate_requested: bool = False
    decided_t: float | None = None
    reported: dict | None = None
    n_reports: int = 0

    def clues(self, lr: dict) -> list:
        out = []
        if self.shape_seen:
            clear = self.best_shape >= float(lr["shape_clear_score"])
            out.append(Clue("shape", lr["shape_clear"] if clear else lr["shape_faint"], "thermal",
                            f"thermal person shape {self.best_shape:.2f}" if self.best_shape else "warm, human-sized"))
        if self.vision:
            out.append(Clue("vision", lr["vision_lying"] if self.vision == "lying" else lr["vision_upright"],
                            "vision", f"camera person box ({self.vision})"))
        if self.id_level:
            ids = {int(k): float(v) for k, v in lr["identity"].items()}
            out.append(Clue("identity", ids.get(self.id_level, 1.0), "vision",
                            f"identity {P.ID_LEVEL_NAMES.get(self.id_level, self.id_level)}"))
        if self.breathing:
            out.append(Clue("breathing", lr["breathing"], "radar", "radar: breathing"))
        elif self.no_vitals:
            out.append(Clue("no_vitals", lr["no_vitals"], "radar", "radar: no vital signs"))
        if self.tag:
            out.append(Clue("tag", lr["tag"], "tag", "authenticated tag reply"))
        if self.code_confirmed_t is not None:
            out.append(Clue("code", lr["code"], "acoustic", "briefed code confirmed"))
        elif self.code_best_n:
            out.append(Clue("code", lr["code_event"] ** self.code_best_n, "acoustic",
                            f"briefed code heard x{self.code_best_n} (not yet confirmed)"))
        for k, n in self.events.items():
            if n:
                out.append(Clue(k, lr[k] ** n, "acoustic", f"{k.replace('_', ' ')} x{n}"))
        out += list(self.geo.values())
        return [c for c in out if c.lr != 1.0]

    def log_odds(self, lr: dict) -> float:
        return self.prior_lo + sum(math.log(c.lr) for c in self.clues(lr))

    def p(self, lr: dict) -> float:
        return _odds_to_p(self.log_odds(lr))

    def modalities(self, lr: dict) -> list:
        return sorted({c.modality for c in self.clues(lr) if c.lr > 1.0 and c.modality in MODALITIES})


@dataclass
class Decision:
    t: float
    subject: str
    track_id: int | None
    action: str
    p: float
    reason: str
    gates: dict
    clues: list
    modalities: list
    xy: np.ndarray | None
    ce_m: float | None
    status_bits: int
    id_level: int
    cls: int
    false_alarm: str = ""
    resend: bool = False

    def as_log(self, positions: bool = True) -> dict:
        d = {"t": round(self.t, 2), "subject": self.subject, "action": self.action, "p": round(self.p, 4),
             "reason": self.reason, "gates": self.gates, "modalities": self.modalities,
             "clues": [[c.key, round(c.lr, 3), c.note] for c in self.clues],
             "id_level": self.id_level, "status_bits": self.status_bits, "cls": P.class_name(self.cls)}
        if self.false_alarm:
            d["false_alarm"] = self.false_alarm
        if positions and self.xy is not None:
            d["enu_m"] = [round(float(self.xy[0]), 1), round(float(self.xy[1]), 1)]
            d["ce_m"] = None if self.ce_m is None else round(float(self.ce_m), 1)
        return d


# ---------------------------------------------------------------------------------- life list
class LedgerLifeList(LifeList):
    """The kit's :class:`LifeList`, unchanged, plus a ledger of which track each detection
    joined, so the decision engine sees every event (code bearings, cough counts) per track."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.ledger: list = []
        self._unlocated_seen = 0

    def _attach(self, tr, det) -> None:
        super()._attach(tr, det)
        self.ledger.append((tr.id, det))

    def _identity(self, det) -> None:
        before = {t.id: t.counts.get("IDENTITY", 0) for t in self.tracks}
        super()._identity(det)
        for t in self.tracks:
            if t.counts.get("IDENTITY", 0) > before.get(t.id, 0):
                self.ledger.append((t.id, det))
                break

    def take_ledger(self) -> tuple:
        """-> ([(track_id, detection)], [new bearing-only sounds]) since the last call."""
        out, self.ledger = self.ledger, []
        new = self.unlocated_sounds[self._unlocated_seen:]
        self._unlocated_seen = len(self.unlocated_sounds)
        return out, new


# ---------------------------------------------------------------------------------- recordings
_DET_FIELDS = set(LifeDetection.__dataclass_fields__)
_DET_EXTRAS = ("shape", "conf")             # attributes the runtime adds to a detection (not dataclass fields)


def det_to_json(d: LifeDetection) -> dict:
    """A detection as JSON, including the runtime's extra scores (thermal shape, code confidence)."""
    out = {}
    for k in list(_DET_FIELDS) + list(_DET_EXTRAS):
        v = getattr(d, k, None)
        if v is None:
            continue
        out[k] = v.tolist() if isinstance(v, np.ndarray) else (v.item() if isinstance(v, np.generic) else v)
    return out


def det_from_json(j: dict) -> LifeDetection:
    """Inverse of :func:`det_to_json`; also reads life_node's ``detection`` events (eagle/record.py).
    Unknown keys (event, sensor, ...) are ignored."""
    kw = {k: v for k, v in j.items() if k in _DET_FIELDS}
    for k in ("xy", "cov", "origin"):
        if kw.get(k) is not None:
            kw[k] = np.asarray(kw[k], float)
    d = LifeDetection(**kw)
    for k in _DET_EXTRAS:
        if j.get(k) is not None:
            setattr(d, k, j[k])
    return d


def sensor_of(d: LifeDetection) -> str:
    """Which sensor a detection came from (for recordings that do not say)."""
    src = d.source or ""
    if d.kind == "HEAT" or (d.kind == "PERSON" and src.startswith("thermal")):
        return "thermal"
    if d.kind in ("PERSON", "IDENTITY"):
        return "vision"
    if d.kind == "SOUND":
        return "audio"
    if d.kind in ("VITALS", "MOTION"):
        return "vitals"
    return d.kind.lower()


# ---------------------------------------------------------------------------------- engine
class DecisionEngine:
    """Per-subject belief, gates and send policy. See the module docstring."""

    def __init__(self, cfg: dict | None = None, *, perch_xy=(0.0, 0.0), frame=None, codes=None,
                 path_loss: dict | None = None, log=print, rng: random.Random | None = None):
        self.c = deep_merge(DEFAULTS, cfg or {})
        self.mode = str(self.c["mode"]).lower()
        if self.mode not in MODES:
            raise ValueError(f"decide.mode must be one of {MODES}, not {self.mode!r}")
        self.m = self.c["modes"][self.mode]
        self.lr = self.c["lr"]
        self.perch = np.asarray(perch_xy, float)[:2]
        self.log = log
        self.rng = rng or random.Random()
        self.prior_lo = prior_log_odds(self.c["mission"]["others_expected"])
        self.beliefs: dict = {}
        self.alias: dict = {}                    # duplicate track id -> the track id whose belief it feeds
        self.graveyard: deque = deque()
        self.sent_t: deque = deque()
        self.relocated_t: float | None = None
        self.tx_dbm: float | None = None
        self.path_loss = dict(path_loss or {"pl_d0_db": 31.7, "exponent": 3.0, "d0_m": 1.0})
        self.code_kind = {c.get("name"): c.get("kind", "whistle") for c in (codes or []) if isinstance(c, dict)}
        self.area = self._area(self.c["mission"].get("search_area"), frame)
        self._next_bearing = 1

    # ---- configuration helpers ------------------------------------------------------------------
    @staticmethod
    def _area(a, frame):
        if not a:
            return None
        if a.get("center_enu_m") is not None:
            c = np.asarray(a["center_enu_m"], float)[:2]
        elif a.get("lat") is not None and frame is not None:
            e, n, _ = frame.to_enu(float(a["lat"]), float(a["lon"]), None)
            c = np.array([e, n], float)
        else:
            return None
        return c, float(a["radius_m"])

    def set_link(self, tx_dbm: float | None) -> None:
        """Planned burst power from the perch (the runtime asks the radio); sets the intercept radius."""
        self.tx_dbm = tx_dbm

    def intercept_radius(self) -> float:
        e = self.c["emcon"]
        if self.tx_dbm is None:
            return float(e["min_radius_m"])
        pl = self.path_loss
        r = intercept_radius_m(self.tx_dbm, e["eavesdropper_dbm"], pl.get("pl_d0_db", 31.7),
                               pl.get("exponent", 3.0), pl.get("d0_m", 1.0))
        return max(float(e["min_radius_m"]), r)

    def mark_relocated(self, t: float | None = None) -> None:
        """The Eagle has moved away from him (autopilot callback or the relocated flag file)."""
        self.relocated_t = time.time() if t is None else t

    def _relocated_since(self, t0: float) -> bool:
        flag = self.c["emcon"].get("relocated_flag")
        if flag:
            p = Path(os.path.expanduser(str(flag)))
            try:
                if p.exists() and p.stat().st_mtime >= t0:
                    return True
            except OSError:
                pass
        return self.relocated_t is not None and self.relocated_t >= t0

    # ---- evidence ---------------------------------------------------------------------------------
    def _resolve(self, track_id: int) -> int:
        seen = set()
        while track_id in self.alias and track_id not in seen:
            seen.add(track_id)
            track_id = self.alias[track_id]
        return track_id

    def _near_live(self, xy) -> Belief | None:
        merge = float(self.c["geometry"]["merge_radius_m"])
        best, bd = None, merge
        for b in self.beliefs.values():
            if b.track_id is None or b.xy is None:
                continue
            d = float(np.hypot(*(np.asarray(xy, float)[:2] - b.xy)))
            if d <= bd:
                best, bd = b, d
        return best

    def _belief(self, track_id: int, t: float, xy=None) -> Belief:
        tid = self._resolve(track_id)
        key = f"track:{tid}"
        b = self.beliefs.get(key)
        if b is None:
            if xy is not None:
                prim = self._near_live(xy)
                if prim is not None:      # the life list started a second track on the same person
                    self.alias[track_id] = prim.track_id
                    return prim
            b = Belief(key, tid, t, self.prior_lo, last_seen=t, first_t=t,
                       xy=None if xy is None else np.asarray(xy, float)[:2].copy())
            self._inherit(b, xy)
            self.beliefs[key] = b
        return b

    def _inherit(self, b: Belief, xy) -> None:
        """A track that died and is reborn at the same spot (occlusion) keeps its sensor evidence."""
        if xy is None:
            return
        g = self.c["geometry"]
        for i, (t_dead, old) in enumerate(list(self.graveyard)):
            if old.xy is None or b.born - t_dead > float(g["inherit_s"]):
                continue
            if float(np.hypot(*(np.asarray(xy, float)[:2] - old.xy))) <= float(g["inherit_radius_m"]):
                for k in ("best_shape", "shape_seen", "vision", "id_level", "breathing", "no_vitals", "tag",
                          "events", "code_best_n", "code_confirmed_t", "status_bits", "cls", "reported", "n_reports"):
                    setattr(b, k, getattr(old, k))
                b.code_events = deque(old.code_events)
                del self.graveyard[i]
                self.log(f"DECIDE  track {b.track_id} inherits the evidence of {old.subject} (same spot)")
                return

    def observe(self, track_id: int, det: LifeDetection) -> None:
        """One detection the life list attached to ``track_id``."""
        b = self._belief(track_id, det.t, det.xy if det.is_point else None)
        b.last_seen = max(b.last_seen, det.t)
        b.status_bits |= int(det.status_bits or 0)
        k, src = det.kind, det.source or ""
        if k == "HEAT":
            b.shape_seen = True
            b.best_shape = max(b.best_shape, float(getattr(det, "shape", None) or 0.0))
        elif k == "PERSON":
            if src.startswith("thermal-model"):
                b.shape_seen = True
                b.best_shape = max(b.best_shape, float(getattr(det, "shape", None) or 0.0))
            else:
                b.vision = "lying" if ("lying" in src or b.vision == "lying") else "upright"
        elif k == "IDENTITY":
            b.id_level = max(b.id_level, int(det.match_level or 0))
        elif k == "VITALS":
            if det.vitals:
                b.breathing = True
            elif det.vitals is False and not b.breathing:
                b.no_vitals = True
        elif k == "TAG" and det.tag_id:
            b.tag = True
        elif k == "SOUND":
            self._sound(b, det)

    def _sound(self, b: Belief, det: LifeDetection) -> None:
        cat, src = det.category, det.source or ""
        if cat == "SIGNAL" and src.startswith("code:"):
            self._code_event(b, det)
            return
        key = {"SIGNAL": "signal", "COUGH": "cough", "DISTRESS": "distress", "BREATH": "breath_sound",
               "VOICE": "voice"}.get(cat)
        if key is None:
            return
        cap = int(self.lr.get(f"{key}_max", 0))
        if b.events.get(key, 0) < cap:
            b.events[key] = b.events.get(key, 0) + 1

    def _code_event(self, b: Belief, det: LifeDetection) -> None:
        c = self.c["code"]
        name = (det.source or "")[5:]
        kind = self.code_kind.get(name) or ("tap" if "tap" in name or "knock" in name else "whistle")
        b.cls = P.CLS_TAP if kind == "tap" else P.CLS_WHISTLE
        b.code_events.append((det.t, det.az_deg, getattr(det, "conf", None)))
        while b.code_events and det.t - b.code_events[0][0] > float(c["window_s"]):
            b.code_events.popleft()
        ok, n = code_rule(b.code_events, int(c["min_events"]), float(c["bearing_tol_deg"]), float(c["min_conf"]))
        b.code_best_n = max(b.code_best_n, min(n, int(c["min_events"]) - 1))
        if ok and b.code_confirmed_t is None:
            b.code_confirmed_t = det.t
            self.log(f"DECIDE  {b.subject}: briefed code confirmed ({n} events in {c['window_s']:.0f} s "
                     f"within +-{c['bearing_tol_deg']:.0f} deg)")

    def observe_bearing(self, det: LifeDetection) -> None:
        """A briefed-code event on a bearing that matched no track."""
        bo = self.c["bearing_only"]
        if not bo.get("enabled", True) or det.az_deg is None or not (det.source or "").startswith("code:"):
            return
        tol = float(self.c["code"]["bearing_tol_deg"])
        best = None
        for b in self.beliefs.values():
            if b.az_deg is not None and abs(ang_diff_deg(det.az_deg, b.az_deg)) <= tol:
                best = b if best is None or abs(ang_diff_deg(det.az_deg, b.az_deg)) < abs(
                    ang_diff_deg(det.az_deg, best.az_deg)) else best
        if best is None:
            key = f"bearing:{self._next_bearing}"
            self._next_bearing += 1
            best = Belief(key, None, det.t, self.prior_lo, last_seen=det.t, first_t=det.t, az_deg=float(det.az_deg))
            self.beliefs[key] = best
        best.last_seen = max(best.last_seen, det.t)
        best.status_bits |= int(det.status_bits or 0)
        self._code_event(best, det)
        best.az_deg = circ_mean_deg([e[1] for e in best.code_events if e[1] is not None] or [det.az_deg])

    def ingest(self, life: LedgerLifeList) -> None:
        """Feed everything the life list attached since the last call."""
        attached, unlocated = life.take_ledger()
        for tid, det in attached:
            self.observe(tid, det)
        for det in unlocated:
            if det.kind == "SOUND" and det.category == "SIGNAL":
                self.observe_bearing(det)

    # ---- geometry clues -----------------------------------------------------------------------------
    def _geometry(self, now: float, tracks: list) -> None:
        g, lr = self.c["geometry"], self.lr
        pos = {t.id: np.asarray(t.x, float) for t in tracks}
        for dup, prim in list(self.alias.items()):             # duplicates that turned out to be two people
            if dup in pos and prim in pos and float(np.hypot(*(pos[dup] - pos[prim]))) > 3 * float(g["merge_radius_m"]):
                del self.alias[dup]
                self.log(f"DECIDE  track {dup} split from track {prim}: two people, separate evidence from now")
        for t in tracks:
            if t.id in self.alias:
                continue                                          # its evidence feeds the primary track
            b = self._belief(t.id, now, pos[t.id])
            b.xy, b.ce_m = pos[t.id], float(t.ce_m)
            b.status_bits |= int(t.status_bits or 0)
            if not b.hist or now - b.hist[-1][0] >= float(g["still_sample_s"]):
                b.hist.append((now, float(b.xy[0]), float(b.xy[1])))
                while b.hist and now - b.hist[0][0] > 7200.0 + 2 * float(g["still_sample_s"]):
                    b.hist.popleft()
            geo = {}
            d = [float(np.hypot(*(pos[o] - pos[t.id]))) for o in pos if o != t.id]
            d = [x for x in d if x > float(g["merge_radius_m"])]          # closer: the same person twice
            if any(x <= float(g["group_radius_m"]) for x in d):
                geo["group"] = Clue("group", lr["group"], "context", "another person within "
                                    f"{g['group_radius_m']:.0f} m")
            elif not any(x <= float(g["alone_radius_m"]) for x in d):
                geo["alone"] = Clue("alone", lr["alone"], "context", f"no other person within {g['alone_radius_m']:.0f} m")
            if self.area is not None and float(np.hypot(*(b.xy - self.area[0]))) <= self.area[1]:
                geo["location"] = Clue("location", lr["location"], "context", "inside the search area")
            for T, key in ((1800.0, "still30"), (7200.0, "still120")):
                if self._still(b, now, T):
                    geo[key] = Clue(key, lr[key], "context", f"still for {T / 60:.0f} min")
            if not self.c["mission"].get("target_mobile", False) and self._moved(b, now) > float(g["moving_m"]):
                geo["moving"] = Clue("moving", lr["moving"], "context",
                                     f"moved > {g['moving_m']:.0f} m in {g['moving_window_s']:.0f} s")
            b.geo = geo

    def _still(self, b: Belief, now: float, T: float) -> bool:
        g = self.c["geometry"]
        if now - b.born < T or not b.hist or b.hist[0][0] > now - T + 2 * float(g["still_sample_s"]):
            return False
        pts = np.array([(x, y) for t, x, y in b.hist if t >= now - T])
        if len(pts) < 3:
            return False
        return bool(np.max(np.hypot(*(pts - pts.mean(0)).T)) <= float(g["still_radius_m"]))

    def _moved(self, b: Belief, now: float) -> float:
        w = float(self.c["geometry"]["moving_window_s"])
        old = [(x, y) for t, x, y in b.hist if now - t >= w]
        if not old or b.xy is None:
            return 0.0
        return float(np.hypot(b.xy[0] - old[-1][0], b.xy[1] - old[-1][1]))

    # ---- bearing candidates ---------------------------------------------------------------------------
    def _bearing_geometry(self, b: Belief, now: float, tracks: list) -> None:
        bo, lr = self.c["bearing_only"], self.lr
        R = float(bo["range_m"])
        a = math.radians(b.az_deg)
        u = np.array([math.cos(a), math.sin(a)])
        b.xy = self.perch + 0.5 * R * u
        b.ce_m = 0.5 * R
        geo = {}
        if now - b.first_t >= float(bo["stable_span_s"]) and b.code_confirmed_t is not None:
            geo["stable"] = Clue("stable", float(bo["lr_stable"]), "context",
                                 f"same bearing for {(now - b.first_t) / 60:.0f} min")
        if self.area is not None:
            c, r = self.area
            s = np.clip(np.dot(c - self.perch, u), 0.0, R)          # closest point of the ray to the centre
            if float(np.hypot(*(self.perch + s * u - c))) <= r:
                geo["location"] = Clue("location", lr["location"], "context", "bearing crosses the search area")
        b.geo = geo

    # ---- decide -------------------------------------------------------------------------------------
    def tick(self, now: float, life: LifeList) -> list:
        """Re-evaluate every subject at ``now``; returns one :class:`Decision` per subject,
        most probable first. At most one subject per tick gets SEND."""
        tracks = [t for t in life.tracks if not t.rf_only]
        ids = {t.id for t in tracks}
        self.alias = {d: p for d, p in self.alias.items() if d in ids}
        for key in [k for k, b in self.beliefs.items() if b.track_id is not None and b.track_id not in ids]:
            b = self.beliefs.pop(key)
            heir = next((d for d, p in self.alias.items() if p == b.track_id), None)
            if heir is not None:                                  # the duplicate lives on: it carries the belief
                for d in [d for d, p in self.alias.items() if p == b.track_id]:
                    self.alias[d] = heir
                del self.alias[heir]
                b.track_id, b.subject = heir, f"track:{heir}"
                self.beliefs[b.subject] = b
            else:
                self.graveyard.append((now, b))
        while self.graveyard and now - self.graveyard[0][0] > float(self.c["geometry"]["inherit_s"]):
            self.graveyard.popleft()
        self._geometry(now, tracks)
        for b in self.beliefs.values():
            if b.track_id is None:
                self._bearing_geometry(b, now, tracks)
        while self.sent_t and now - self.sent_t[0] > 3600.0:
            self.sent_t.popleft()
        out = [self._decide(b, now) for b in self.beliefs.values()]
        out.sort(key=lambda d: -d.p)
        sent = False
        for d in out:                                 # one SEND per tick, highest P first
            if d.action == SEND:
                if sent:
                    d.action, d.reason = HOLD, "another report goes first this tick"
                sent = True
        for d in out:
            self.beliefs[d.subject].action = d.action
            self.beliefs[d.subject].reason = d.reason
        return out

    def _unknowns(self, b: Belief) -> list:
        """Other people in view who have not passed the gate themselves, as (subject, xy). Anyone
        we cannot call him is a possible listener, whatever their P."""
        merge = float(self.c["geometry"]["merge_radius_m"])
        out = []
        for o in self.beliefs.values():
            if o is b or o.track_id is None or o.xy is None:
                continue
            if b.xy is not None and b.track_id is not None and float(np.hypot(*(o.xy - b.xy))) <= merge:
                continue
            if o.p(self.lr) < float(self.m["p_send"]):
                out.append((o.subject, o.xy))
        return out

    def _false_alarm(self, b: Belief) -> str:
        why = [w for w, on in (("authenticated tag", b.tag), ("briefed code", b.code_confirmed_t is not None),
                               ("face verified", b.id_level >= P.ID_FACE)) if on]
        if why:
            return "LOW: " + " + ".join(why)
        if any(b.events.get(k) for k in ("cough", "distress", "breath_sound")):
            return "MEDIUM: stillness and sounds; a sick or wounded soldier could look the same"
        return "HIGH: warm, still and alone; a resting sentry or a sleeping farmer looks the same"

    def _decide(self, b: Belief, now: float) -> Decision:
        lr, gt, m, e = self.lr, self.c["gate"], self.m, self.c["emcon"]
        clues = sorted(b.clues(lr), key=lambda c: -abs(math.log(c.lr)))
        p = _odds_to_p(b.prior_lo + sum(math.log(c.lr) for c in clues))
        mods = sorted({c.modality for c in clues if c.lr > 1.0 and c.modality in MODALITIES})
        gates = {
            "p": p >= float(m["p_send"]),
            "corroborated": b.code_confirmed_t is not None or len(mods) >= int(m["min_modalities"]),
            "dwell": now - b.born >= float(gt["min_dwell_s"]) and now - b.last_seen <= float(gt["fresh_s"]),
            "fix": b.ce_m is not None and b.ce_m <= float(gt["max_ce_m"]),
        }
        d = Decision(now, b.subject, b.track_id, WATCH, p, "", gates, clues, mods, b.xy, b.ce_m,
                     b.status_bits, b.id_level, b.cls)
        if b.track_id is None and self._covered_by_track(b):
            d.reason = "a track on this bearing carries the evidence"
            return d
        if not all(gates.values()):
            d.reason = self._why_not(b, p, mods, gates, now)
            if b.reported:
                d.action = REPORTED
                d.reason = f"reported {b.n_reports}x; " + d.reason
            b.hold_t = None
            return d
        if b.decided_t is None:
            b.decided_t = now
            self.log(f"DECIDE  {b.subject}: P {p:.3f} >= {m['p_send']} on {', '.join(mods) or 'code'} "
                     f"({self._false_alarm(b)})")
        d.false_alarm = self._false_alarm(b)
        if b.reported:
            why = self._resend_reason(b, now)
            if not why:
                d.action, d.reason = REPORTED, f"reported {b.n_reports}x; nothing new to send"
                return d
            d.resend = True
            d.reason = why
        if len(self.sent_t) >= int(self.c["report"]["max_per_hour"]):
            d.action, d.reason = HOLD, f"report budget used ({self.c['report']['max_per_hour']} per hour)"
            return d
        # EMCON
        r = self.intercept_radius()
        blockers = [s for s, xy in self._unknowns(b) if float(np.hypot(*(xy - self.perch))) <= r]
        threat = self.c["mission"].get("threat_dist_m")
        threatened = threat is not None and float(threat) <= r
        if blockers or threatened:
            b.blocked_t = now
        if b.hold_t is not None and self._relocated_since(b.hold_t):
            d.action = SEND
            d.reason = (d.reason + "; " if d.reason else "") + "relocated away from him"
            return d
        recently_blocked = b.blocked_t is not None and now - b.blocked_t < float(e["clear_s"]) and b.hold_t is not None
        if blockers or threatened or recently_blocked:
            if b.hold_t is None:
                b.hold_t = now
            injured = (b.status_bits & P.ST_INJURED) or self.c["mission"].get("target_state") == "injured"
            max_hold = float(e["max_hold_injured_s"] if injured else e["max_hold_s"])
            what = (f"{len(blockers)} unknown person(s) within the {r:.0f} m intercept radius" if blockers else
                    f"briefed threat {float(threat):.0f} m away, inside the {r:.0f} m intercept radius" if threatened
                    else f"EMCON clear for {now - b.blocked_t:.0f} of {e['clear_s']:.0f} s")
            if not b.relocate_requested and (blockers or threatened):
                b.relocate_requested = True
                self.log(f"ACTION  REQUEST_RELOCATE for {b.subject}: {what}; move >= 2 km away, then signal "
                         f"(touch {e.get('relocated_flag')})")
            if now - b.hold_t >= max_hold and m["on_hold_timeout"] == "send":
                d.action = SEND
                d.reason = f"held {now - b.hold_t:.0f} s ({what}); urgency: sending anyway"
            else:
                d.action = HOLD
                d.reason = f"{what}; held {now - b.hold_t:.0f}/{max_hold:.0f} s" + (
                    "" if m["on_hold_timeout"] == "send" else " (strict: waits for relocation)")
            return d
        d.action = SEND
        d.reason = (d.reason + "; " if d.reason else "") + f"all gates hold, EMCON clear ({r:.0f} m)"
        return d

    def _covered_by_track(self, b: Belief) -> bool:
        tol = float(self.c["code"]["bearing_tol_deg"])
        for o in self.beliefs.values():
            if o.track_id is None or o.xy is None:
                continue
            az = math.degrees(math.atan2(*(o.xy - self.perch)[::-1]))
            if abs(ang_diff_deg(az, b.az_deg)) <= tol and o.p(self.lr) >= 0.5:
                return True
        return False

    def _why_not(self, b, p, mods, gates, now) -> str:
        m, gt = self.m, self.c["gate"]
        if not gates["p"]:
            return f"P {p:.3f} < {m['p_send']}"
        if not gates["corroborated"]:
            return (f"one modality ({', '.join(mods) or 'none'}); needs {m['min_modalities']} "
                    f"or the briefed-code rule")
        if not gates["dwell"]:
            if now - b.last_seen > float(gt["fresh_s"]):
                return f"not seen for {now - b.last_seen:.0f} s"
            return f"held {now - b.born:.0f} of {gt['min_dwell_s']:.0f} s"
        return f"fix ce {b.ce_m if b.ce_m is not None else float('nan'):.0f} m > {gt['max_ce_m']:.0f} m"

    def _resend_reason(self, b: Belief, now: float) -> str:
        r = self.c["report"]
        rep = b.reported
        if not self.m.get("resend", True) or now - rep["t"] < float(r["min_resend_gap_s"]):
            return ""
        if rep.get("xy") is not None and b.xy is not None:
            moved = float(np.hypot(*(b.xy - rep["xy"])))
            if moved > float(r["resend_move_m"]):
                return f"moved {moved:.0f} m since the last report"
        if b.id_level > rep.get("id_level", 0):
            return f"identity rose to {P.ID_LEVEL_NAMES.get(b.id_level, b.id_level)}"
        if (b.status_bits & ~rep.get("status_bits", 0)) & (P.ST_INJURED | P.ST_DURESS):
            return "new status flags"
        return ""

    # ---- after the runtime sent it ----------------------------------------------------------------
    def repeat_delay_s(self) -> float:
        lo, hi = (float(x) for x in self.c["report"]["repeat_after_s"])
        return self.rng.uniform(lo, hi)

    def mark_sent(self, subject: str, now: float) -> None:
        b = self.beliefs.get(subject)
        if b is None:
            return
        self.sent_t.append(now)
        b.n_reports += 1
        b.reported = {"t": now, "xy": None if b.xy is None else np.array(b.xy), "id_level": b.id_level,
                      "status_bits": b.status_bits}
        b.hold_t, b.blocked_t, b.relocate_requested = None, None, False

    def wipe(self, subject: str) -> None:
        """Strict mode, after the last burst: keep that he was reported, drop where."""
        b = self.beliefs.get(subject)
        if b is not None and b.reported is not None:
            b.reported["xy"] = None

    @property
    def transmit_allowed(self) -> bool:
        return bool(self.m.get("transmit", True))

    @property
    def log_positions(self) -> bool:
        return bool(self.m.get("log_positions", True))


# ---------------------------------------------------------------------------------- decision log
class DecisionLog:
    """JSON lines: every action change, plus the top subjects every ``every_s``. Positions only
    when the mode allows it (strict mode never writes a position to the Pi's disk)."""

    def __init__(self, path: str | None, every_s: float = 10.0, positions: bool = True, sink=None):
        self.path = None if not path else Path(os.path.expanduser(str(path)))
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.every_s, self.positions = float(every_s), positions
        self.sink = sink                         # callable(dict), e.g. the field recorder's events.jsonl
        self.last_action: dict = {}
        self.next_t = 0.0

    def write(self, decisions: list, now: float, extra: dict | None = None) -> list:
        lines = []
        periodic = now >= self.next_t
        if periodic:
            self.next_t = now + self.every_s
        for i, d in enumerate(decisions):
            changed = self.last_action.get(d.subject) != d.action
            self.last_action[d.subject] = d.action
            if changed or (periodic and i < 3):
                rec = d.as_log(self.positions)
                rec["event"] = "change" if changed else "tick"
                lines.append(rec)
        if extra:
            lines.append({"t": round(now, 2), **extra})
        if self.path is not None and lines:
            with self.path.open("a") as f:
                for rec in lines:
                    f.write(json.dumps(rec, default=float) + "\n")
        if self.sink is not None:
            for rec in lines:
                self.sink(rec)
        return lines
