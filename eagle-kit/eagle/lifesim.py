"""Simulated rescue scenes for the life list: ground truth -> every sensor -> fusion.

Used by ``apps/life_demo.py`` and the tests, so the demo runs the same detectors that run at
the venue: thermal renderer -> :class:`eagle.thermal.ThermalDetector`; multichannel audio ->
briefed codes (:class:`eagle.pipeline.Locator`) and life sounds (:mod:`eagle.sounds`); radar
pellets -> :func:`eagle.vitals.estimate_vitals`; BLE world -> :class:`eagle.rfscan.RfScanner`
(allowlist rule); tag poll -> :class:`eagle.protocol.TagLogic`; all into
:class:`eagle.fusion.LifeList`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import protocol as P
from .camgeo import Camera
from .config import array_geometry, deep_merge
from .fusion import LifeDetection, LifeList, iso_cov
from .locate import heading_to_yaw
from .pipeline import Locator
from .rfscan import Allowlist, RfScanner, SimRfSource
from .sim import add_noise, code_signal, mic_positions_world, render
from .sounds import detect_life_sounds, locate_sounds, synth_speech
from .thermal import SyntheticThermal, ThermalDetector, person, warm_decoy
from .vitals import chest_displacement, estimate_vitals, radar_signal

EXPECTED = {"conscious": P.LS_RESPONSIVE, "unconscious": P.LS_BREATHING, "decoy": P.LS_WARM_ONLY,
            "device": P.LS_RF_ONLY}


@dataclass
class SimPerson:
    name: str
    xy: tuple
    kind: str                         # conscious | unconscious | decoy | device
    posture: str = "lying"
    yaw_deg: float = 0.0
    temp_c: float = 31.0
    breath_bpm: float | None = None
    heart_bpm: float | None = None
    sounds: tuple = ()                # "whistles", "speech"
    pellet_xy: tuple | None = None    # radar pellet dropped near this body
    tag_id: int | None = None
    tag_status: int = 0
    ble: tuple | None = None          # (address, name) of a BLE device here

    @property
    def is_person(self) -> bool:
        return self.kind in ("conscious", "unconscious")

    @property
    def warm(self) -> bool:
        return self.kind in ("conscious", "unconscious", "decoy")


@dataclass
class Scenario:
    people: list
    perch: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0, 3.0]))
    heading_deg: float = 0.0          # camera axis and mic array 0-deg mark (compass)
    tilt_down_deg: float = 35.0
    bystanders: list = field(default_factory=list)       # non-allowlisted BLE devices
    allowlist: list = field(default_factory=lambda: [{"name": "TEAM-RADIO", "label": "TEAM-RADIO"}])
    duration_s: float = 60.0
    seed: int = 0


def default_scenario(seed: int = 0) -> Scenario:
    """Perch 3 m up. P1 conscious (whistles + speech, tag 7 INJURED), P2 unconscious (silent,
    warm, breathing 10/min, radar pellet 1.3 m away), P3 warm decoy (jacket + hand warmers,
    pellet nearby), P4 an allowlisted radio with nobody there; 6 bystander phones."""
    rng = np.random.default_rng(seed + 99)
    people = [
        SimPerson("P1", (2.0, 6.0), "conscious", "curled", 60.0, 31.5, 16.0, 84.0, ("whistles", "speech"),
                  tag_id=7, tag_status=P.ST_INJURED),
        SimPerson("P2", (-2.5, 4.5), "unconscious", "lying", 20.0, 31.0, 10.0, 62.0, (), pellet_xy=(-1.6, 3.6)),
        SimPerson("P3", (1.5, 9.0), "decoy", "lying", 100.0, 29.5, pellet_xy=(0.6, 8.4)),
        SimPerson("P4", (-4.0, 9.5), "device", ble=("C0:FF:EE:00:00:42", "TEAM-RADIO-2")),
    ]
    by = [(f"{rng.integers(0, 256):02X}:{rng.integers(0, 256):02X}:5E:{k:02X}:9A:{rng.integers(0, 256):02X}",
           n, (rng.uniform(-25, 25), rng.uniform(-25, 25), 1.2))
          for k, n in enumerate(["Galaxy S24", "iPhone", "JBL Flip 6", None, "Pixel 9", "Fitbit"])]
    return Scenario(people, bystanders=by, seed=seed)


def _capsule(p: SimPerson):
    axes = {"lying": 0.85, "curled": 0.45, "sitting": 0.28}.get(p.posture, 0.6)
    rad = {"lying": 0.23, "curled": 0.3, "sitting": 0.25}.get(p.posture, 0.3)
    a = np.radians(p.yaw_deg)
    u = np.array([np.cos(a), np.sin(a)]) * max(axes - rad, 0.0)
    c = np.asarray(p.xy, float)
    return c - u, c + u, rad


def _capsule_gap(p: SimPerson, q: SimPerson) -> float:
    a0, a1, ra = _capsule(p)
    b0, b1, rb = _capsule(q)
    s = np.linspace(0, 1, 11)
    A = a0[None, :] + s[:, None] * (a1 - a0)[None, :]
    B = b0[None, :] + s[:, None] * (b1 - b0)[None, :]
    return float(np.min(np.linalg.norm(A[:, None, :] - B[None, :, :], axis=2)) - ra - rb)


def random_scenario(n: int = 10, seed: int = 0, *, r_range=(3.0, 12.0), min_gap_m: float = 1.2,
                    camera: Camera | None = None) -> Scenario:
    """``n`` bodies at random 3-12 m from the perch inside the camera view, at least
    ``min_gap_m`` apart (touching bodies merge into one thermal blob), with mixed kinds:
    conscious (whistle or speech), unconscious breathers with a radar pellet, warm decoys."""
    rng = np.random.default_rng(seed)
    sc = Scenario([], seed=seed)
    cam = camera or make_camera(sc)
    kinds = ["conscious", "unconscious", "decoy"]
    for attempt in range(200):
        people = []
        tries = 0
        while len(people) < n and tries < 4000:
            tries += 1
            r = rng.uniform(*r_range)
            az = np.radians(90.0 - sc.heading_deg + rng.uniform(-30, 30))
            xy = (float(r * np.cos(az)), float(r * np.sin(az)))
            kind = kinds[len(people) % 3] if len(people) < 3 else kinds[int(rng.integers(0, 3))]
            posture = "curled" if kind == "conscious" else "lying"
            p = SimPerson(f"P{len(people) + 1}", xy, kind, posture, float(rng.uniform(0, 180)),
                          float(rng.uniform(29.5, 32.5)) if kind != "decoy" else float(rng.uniform(28.5, 30.5)))
            a0, a1, rad = _capsule(p)
            ends = np.array([[*a0, 0.15], [*a1, 0.15]])
            u, v, front = cam.project(ends)
            if not np.all(front) or not np.all(cam.in_image(u, v, margin=4.0)):
                continue
            if any(_capsule_gap(p, q) < min_gap_m for q in people):
                continue
            if kind == "conscious":
                p.sounds = ("whistles",) if rng.uniform() < 0.5 else ("speech",)
                p.breath_bpm, p.heart_bpm = float(rng.uniform(12, 22)), float(rng.uniform(70, 110))
            elif kind == "unconscious":
                p.breath_bpm, p.heart_bpm = float(rng.uniform(8, 20)), float(rng.uniform(55, 100))
            if kind in ("unconscious", "decoy"):
                b = rng.uniform(0, 2 * np.pi)
                p.pellet_xy = (xy[0] + 1.0 * np.cos(b), xy[1] + 1.0 * np.sin(b))
            people.append(p)
        if len(people) == n:
            break
    else:
        raise RuntimeError("could not place everyone; lower n or min_gap_m")
    sc.people = people
    return sc


def make_camera(sc: Scenario, hfov: float = 57.0, w: int = 160, h: int = 120) -> Camera:
    return Camera.from_fov(hfov, w, h, position=sc.perch, heading_deg=sc.heading_deg, tilt_down_deg=sc.tilt_down_deg)


@dataclass
class SimResult:
    life: LifeList
    detections: list
    camera: Camera
    t_end: float
    sounds: list
    vitals: dict
    rf_anonymous: int
    thermal_blobs: list
    log: list


def run_scenario(sc: Scenario, cfg: dict, *, keys: dict | None = None, session: P.EagleSession | None = None,
                 log=print, thermal_frames: int = 6, snr_db: float = 20.0, preset: str | None = None) -> SimResult:
    """Simulate every sensor for ``sc`` and fuse. ``session`` (an EagleSession) polls tags."""
    rng = np.random.default_rng(sc.seed)
    lines: list = []

    def say(m):
        lines.append(m)
        log(m)
    dets: list = []
    perch = np.asarray(sc.perch, float)
    floor_z = float(cfg.get("site", {}).get("floor_z", 0.0))
    life_cfg = cfg.get("life", {})
    # ---- thermal ---------------------------------------------------------------------------
    cam = make_camera(sc)
    bodies = [person(p.xy, p.posture, p.yaw_deg, p.temp_c, p.name) if p.is_person
              else warm_decoy(p.xy, p.yaw_deg, p.temp_c, p.name) for p in sc.people if p.warm]
    therm = SyntheticThermal(cam, bodies, noise_c=0.2, seed=sc.seed, floor_z=floor_z)
    tdet = ThermalDetector.from_config(cam, life_cfg.get("thermal", {}).get("detector", {}), floor_z=floor_z)
    blobs_all = []
    for k in range(thermal_frames):
        t = 10.0 * k
        _, frame, rad = therm.read()
        blobs = tdet.detect(frame, rad, t)
        blobs_all.append(blobs)
        dets += [LifeDetection("HEAT", t, xy=b.xy, cov=b.cov, temp_c=b.peak, source="thermal") for b in blobs]
    say(f"[thermal] {cam.width}x{cam.height} {cam.hfov_deg:.0f} deg camera {perch[2]:.1f} m up, tilt "
        f"{sc.tilt_down_deg:.0f} deg: {len(blobs_all[-1])} warm bodies per frame "
        f"({len(tdet.rejected)} blob(s) rejected by the size gate in the last frame)")
    # ---- audio: briefed codes + life sounds -------------------------------------------------
    c = deep_merge(cfg, {"perch": {"enu_m": perch.tolist(), "heading_deg": sc.heading_deg, "az_offset_deg": 0.0,
                                   "pitch_deg": 0.0, "roll_deg": 0.0}, "audio": {"mirror_y": False}})
    geom = array_geometry(c, preset)
    loc = Locator(c, geometry=geom)
    fs = geom.fs
    talkers = [p for p in sc.people if p.sounds]
    sounds = []
    t_audio = 2.0                                     # capture starts 2 s into the timeline
    if talkers:
        mics = mic_positions_world(geom.positions, perch, heading_to_yaw(sc.heading_deg))
        seg_s = 9.0
        total = seg_s * len(talkers) + 1.0
        audio = np.zeros((geom.n_mics, int(total * fs)))
        active = []
        for k, p in enumerate(talkers):
            src = np.array([p.xy[0], p.xy[1], floor_z + 0.25])
            mono = np.zeros(int(seg_s * fs))
            if "whistles" in p.sounds:
                cs = code_signal(fs, "three_whistles", t_start=0.5, total_s=4.5, rng=rng)
                mono[:cs.signal.size] += cs.signal
                active += [(k * seg_s + a, k * seg_s + b) for a, b in cs.events]
            if "speech" in p.sounds:
                sp = 0.6 * synth_speech(fs, 2.5, float(rng.uniform(110, 200)), rng)
                i0 = int(5.5 * fs)
                mono[i0:i0 + sp.size] += sp[:mono.size - i0]
                active.append((k * seg_s + 5.5, k * seg_s + 8.0))
            paths = [(src, 1.0), (src * np.array([1, 1, -1]) + np.array([0, 0, 2 * floor_z]), 0.3)]
            y = render(mono, fs, mics, paths, c=loc.c)
            i0 = int(k * seg_s * fs)
            audio[:, i0:i0 + y.shape[1]] += y[:, :audio.shape[1] - i0]
        audio = add_noise(audio, fs, snr_db, active, rng=rng)
        events, codes = loc.process(audio)
        sounds = locate_sounds(audio, detect_life_sounds(audio, fs), loc)
        # the whistle/tap detectors also fire on speech (voiced syllables look tonal, their
        # onsets look like taps): drop briefed-code matches that sit inside VOICE/DISTRESS
        speech = [(s.start, s.end) for s in sounds if s.category in ("VOICE", "DISTRESS")]
        kept = [d for d in codes if not any(e.start < b and e.end > a for e in d.match.events for a, b in speech)]
        if len(kept) < len(codes):
            say(f"[sound]   {len(codes) - len(kept)} briefed-code match(es) inside speech ignored")
        codes = kept
        code_spans = []
        polled = set()
        for d in codes:
            t = t_audio + d.match.end / fs
            code_spans.append((d.match.start, d.match.end))
            st = d.status
            if d.has_fix:
                dets.append(LifeDetection("SOUND", t, xy=d.xyz[:2], cov=d.cov, origin=perch[:2], az_deg=d.az_world,
                                          sigma_az_deg=loc.sigma_az, category="SIGNAL", status_bits=st,
                                          source=f"code:{d.match.code}"))
            else:
                dets.append(LifeDetection("SOUND", t, origin=perch[:2], az_deg=d.az_world, sigma_az_deg=loc.sigma_az,
                                          category="SIGNAL", status_bits=st, source=f"code:{d.match.code}"))
            say(f"[sound]   briefed code {d.match.code}: bearing {d.bearing_deg:.1f} deg"
                + (f" -> E {d.xyz[0]:.1f} N {d.xyz[1]:.1f}" if d.has_fix else " (bearing only)"))
            # the Eagle polls the tag once after an acoustic fix (one CHALLENGE, one REPLY)
            if d.has_fix and session is not None and keys is not None:
                near = min(sc.people, key=lambda p: np.hypot(p.xy[0] - d.xyz[0], p.xy[1] - d.xyz[1]))
                if (near.tag_id and near.tag_id not in polled
                        and np.hypot(near.xy[0] - d.xyz[0], near.xy[1] - d.xyz[1]) < 5.0):
                    polled.add(near.tag_id)                   # poll each tag once
                    tag = P.TagLogic(near.tag_id, keys["tag_hmac"], keys["covert"], status=near.tag_status)
                    ch, nonce = session.make_challenge(500, -9)
                    resp = tag.on_frame(ch, -70.0)
                    rep = session.verify_reply(resp.frame, nonce)
                    dets.append(LifeDetection("TAG", t + 0.6, xy=d.xyz[:2], cov=d.cov, tag_id=rep.tag_id,
                                              tag_status_wire=rep.status_wire,
                                              tag_chal16=P.nonce_counter(nonce) & 0xFFFF,
                                              update_position=False, source="tag-poll"))
                    say(f"[tag]     CHALLENGE -> REPLY from tag {rep.tag_id} verified (status byte masked "
                        f"0x{rep.status_wire:02x}: only the Nest can read it)")
        for s in sounds:
            overl = any(s.start < e and s.end > b for b, e in code_spans)
            if s.category is None or (s.category == "SIGNAL" and overl):
                continue
            t = t_audio + s.end / fs
            if s.xy is not None:
                dets.append(LifeDetection("SOUND", t, xy=s.xy, cov=s.cov, origin=perch[:2], az_deg=s.az_world,
                                          sigma_az_deg=loc.sigma_az, category=s.category, source="sounds"))
            elif s.az_world is not None:
                dets.append(LifeDetection("SOUND", t, origin=perch[:2], az_deg=s.az_world, sigma_az_deg=loc.sigma_az,
                                          category=s.category, source="sounds"))
            say(f"[sound]   {s}")
    # ---- radar pellets ----------------------------------------------------------------------------
    vit = {}
    vcfg = life_cfg.get("vitals", {})
    for p in sc.people:
        if p.pellet_xy is None:
            continue
        cand = [q for q in sc.people if q.warm and np.hypot(q.xy[0] - p.pellet_xy[0], q.xy[1] - p.pellet_xy[1])
                <= float(vcfg.get("pellet_range_m", 1.5))]
        if not cand:
            continue
        q = min(cand, key=lambda q: np.hypot(q.xy[0] - p.pellet_xy[0], q.xy[1] - p.pellet_xy[1]))
        fsr, dur = 20.0, 40.0
        d = chest_displacement(fsr, dur, q.breath_bpm if q.is_person else None,
                               q.heart_bpm if q.is_person else None, seed=int(rng.integers(1 << 30)))
        x = radar_signal(d, "phase", freq_ghz=60.0, seed=int(rng.integers(1 << 30)))
        r = estimate_vitals(x, fsr, "phase", freq_ghz=60.0)
        vit[p.name] = r
        sig = float(vcfg.get("pellet_sigma_m", 1.0))
        dets.append(LifeDetection("VITALS", 45.0, xy=np.asarray(p.pellet_xy, float), cov=iso_cov(sig),
                                  breath_bpm=r.breath_bpm, heart_bpm=r.heart_bpm, vitals=r.has_vitals,
                                  update_position=False, source=f"pellet-near-{p.name}"))
        say(f"[radar]   pellet at E {p.pellet_xy[0]:.1f} N {p.pellet_xy[1]:.1f} (60 GHz, 40 s): {r}")
    # ---- BLE ----------------------------------------------------------------------------------------
    devs = [(p.ble[0], p.ble[1], (p.xy[0], p.xy[1], 0.3)) for p in sc.people if p.ble] + list(sc.bystanders)
    anon = 0
    if devs:
        rf = RfScanner(Allowlist(sc.allowlist), SimRfSource(devs, perch, seed=sc.seed), log=say)
        for t in (20.0, 50.0):
            hits, anon = rf.scan(t)
            dets += rf.detections(hits, perch[:2], t)
    # ---- fusion -------------------------------------------------------------------------------------
    life = LifeList.from_config(life_cfg.get("fusion", {}), perch_xy=perch[:2])
    for t in sorted({d.t for d in dets}):
        life.update([d for d in dets if d.t == t], now=t)
    life.rf_anonymous = anon
    t_end = max(sc.duration_s, max((d.t for d in dets), default=0.0) + 5.0)
    life.evaluate(t_end)
    return SimResult(life, dets, cam, t_end, sounds, vit, anon, blobs_all, lines)


def match_truth(sc: Scenario, life: LifeList, max_dist_m: float = 1.5) -> list:
    """Greedy nearest matching of truth objects to tracks -> [(SimPerson, track or None, dist)].
    Devices match RF_ONLY entries by allowlist label presence."""
    pairs = []
    tracks = [t for t in life.tracks if not t.rf_only]
    for i, p in enumerate(sc.people):
        if not p.warm:
            continue
        for tr in tracks:
            d = float(np.hypot(*(tr.x - np.asarray(p.xy))))
            if d <= max_dist_m:
                pairs.append((d, i, tr.id))
    pairs.sort()
    used_p, used_t, out = set(), set(), {}
    by = {t.id: t for t in tracks}
    for d, i, tid in pairs:
        if i in used_p or tid in used_t:
            continue
        used_p.add(i)
        used_t.add(tid)
        out[i] = (sc.people[i], by[tid], d)
    rf_tracks = [t for t in life.tracks if t.rf_only]
    res = []
    for i, p in enumerate(sc.people):
        if p.warm:
            res.append(out.get(i, (p, None, float("nan"))))
        else:
            res.append((p, rf_tracks[0] if rf_tracks else None, float("nan")))
    return res
