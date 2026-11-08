#!/usr/bin/env python3
"""Eagle life node: every available sensor -> life list -> encrypted LIFE bursts to the Nest.

    python apps/life_node.py --no-radio                       # bench: whatever sensors are present, log only
    python apps/life_node.py --config config/local.yaml       # live, radio per radio.transport
    python apps/life_node.py --transport udp --modules thermal --duration 30   # with nest_station --transport udp
    python apps/life_node.py --modules thermal vitals --thermal mlx90640 --vitals-port /dev/ttyACM0
    python apps/life_node.py --config ../pi/field.yaml --transport udp --record   # field: also save to ~/field_logs

Modules (life.modules in the config; each one is skipped with a log line if its hardware or
library is missing): audio (mic array: briefed codes + life sounds), thermal (synthetic,
mlx90640, uvc_y16, gray8), vitals (serial radar bridge / vendor module), rf (BLE allowlist
scan), vision (JSON lines of person boxes). One log line per step: THERMAL, SOUND, VITALS,
RF, VISION, LIFE (the list), TX.

``--record [DIR]`` (default ~/field_logs) saves thermal frames (2/s), every mic channel (WAV), and
every detection, life list and TX (events.jsonl) to DIR/<timestamp>/ (eagle/record.py).
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eagle import protocol as P                                    # noqa: E402
from eagle.camgeo import Camera                                    # noqa: E402
from eagle.config import deep_merge, load_config, load_keys        # noqa: E402
from eagle.fusion import LifeDetection, LifeList, iso_cov          # noqa: E402

ALL_MODULES = ("audio", "thermal", "vitals", "rf", "vision")
MAX_REQUIRED_FAILS = 10          # consecutive step errors of a --require'd module before life_node exits


class RequiredModuleError(RuntimeError):
    """A --require'd module is missing or keeps failing: exit non-zero so systemd restarts us."""


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _mount(c: dict, perch) -> dict:
    """Camera config with mount.enu_m defaulting to the perch position."""
    c = dict(c or {})
    m = dict(c.get("mount") or {})
    if m.get("enu_m") is None:
        m["enu_m"] = list(perch)
    c["mount"] = m
    return c


class LifeNode:
    def __init__(self, cfg: dict, args, clock=time.time):
        self.cfg, self.args, self.clock = cfg, args, clock
        L = cfg.get("life", {})
        self.L = L
        self.perch = np.asarray(cfg["perch"]["enu_m"], float)
        self.floor_z = float(cfg.get("site", {}).get("floor_z", 0.0))
        self.life = LifeList.from_config(L.get("fusion", {}), perch_xy=self.perch[:2])
        self.mods: dict = {}
        self.next_t: dict = {}
        self.unavailable: dict = {}
        self.required = set(getattr(args, "require", None) or [])
        self.fail_count: dict = {}
        self._th_cache = None
        self.rec = None
        self.radio, self.session = None, None
        if getattr(args, "record", None):
            from eagle.record import Recorder
            r = L.get("record", {})
            self.rec = Recorder(args.record, thermal_hz=float(r.get("thermal_hz", 2.0)),
                                chunk_s=float(r.get("chunk_s", 60.0)), min_free_gb=float(r.get("min_free_gb", 2.0)),
                                log=log)
        want = list(args.modules or L.get("modules", ALL_MODULES))
        for name in want:
            if name not in ALL_MODULES:
                log(f"MODULE  {name}: unknown (choose from {', '.join(ALL_MODULES)})")
                continue
            try:
                getattr(self, f"_init_{name}")()
            except Exception as e:                       # missing library / hardware: keep going
                log(f"MODULE  {name}: unavailable ({type(e).__name__}: {e})")
                self.unavailable[name] = f"{type(e).__name__}: {e}"
        log(f"MODULES running: {', '.join(self.mods) or 'none'} (perch ENU {self.perch.tolist()})")
        missing = sorted(m for m in self.required if m not in self.mods)
        if missing:
            if self.rec:
                self.rec.event("required_missing", modules=missing, unavailable=self.unavailable)
            self.close()
            raise RequiredModuleError(f"required module(s) unavailable: {', '.join(missing)}")
        if self.rec:
            self.rec.event("run_start", modules=list(self.mods), unavailable=self.unavailable, perch=self.perch,
                           config={k: cfg.get(k) for k in ("site", "perch", "audio", "life", "identity") if k in cfg})
        self.radio, self.session = None, None
        if not args.no_radio:
            try:
                self._init_radio()
            except Exception as e:
                log(f"RADIO   unavailable ({type(e).__name__}: {e}) - logging only")
        self.last_sig, self.last_report = None, -1e9
        self.target_fix_t = {}                  # track id -> last time a TARGET FIX was sent

    # ---- module setup ---------------------------------------------------------------------
    def _init_thermal(self):
        from eagle.thermal import ThermalDetector, make_thermal_source
        t = self.L.get("thermal", {})
        if t.get("source", "synthetic") in (None, "none"):
            raise RuntimeError("life.thermal.source is none")
        cam = Camera.from_config(_mount(t.get("camera", {}), self.perch))
        src = make_thermal_source(t, cam)
        det = ThermalDetector.from_config(cam, t.get("detector", {}), floor_z=self.floor_z)
        self.mods["thermal"] = (src, det)
        log(f"MODULE  thermal: {t.get('source', 'synthetic')} {cam.width}x{cam.height}, hfov {cam.hfov_deg:.0f} deg, "
            f"heading {cam.heading_deg:.0f} deg, tilt {cam.tilt_down_deg:.0f} deg down")

    def _init_audio(self):
        from eagle.audio_io import LiveCapture
        from eagle.config import array_geometry
        from eagle.pipeline import Locator
        from eagle.sounds import make_classifier
        geom = array_geometry(self.cfg)
        loc = Locator(self.cfg, geometry=geom)
        a = self.cfg.get("audio", {})
        cap = LiveCapture(a.get("device"), geom.fs, geom.device_channels, seconds=float(a.get("window_s", 8.0)) + 2)
        cap.start()
        clf = make_classifier(self.L.get("sounds", {}), log=log)
        self.mods["audio"] = {"cap": cap, "geom": geom, "loc": loc, "clf": clf, "last_end": -1}
        log(f"MODULE  audio: {geom.name}, {type(clf).__name__}")

    def _init_vitals(self):
        from eagle.vitals import VitalsSerialReader
        v = self.L.get("vitals", {})
        port = getattr(self.args, "vitals_port", None) or v.get("port")
        if not port:
            raise RuntimeError("no life.vitals.port configured")
        rd = VitalsSerialReader(port, int(v.get("baud", 115200)), fs=float(v.get("fs_hz", 50)),
                                window_s=float(v.get("window_s", 30)), log=log)
        xy = np.asarray(v["enu_m"], float)[:2] if v.get("enu_m") is not None else self.perch[:2]
        self.mods["vitals"] = (rd, xy, float(v.get("pellet_sigma_m", 1.0)))
        log(f"MODULE  vitals: {port} (radar at E {xy[0]:.1f} N {xy[1]:.1f})")

    def _init_rf(self):
        from eagle.rfscan import make_rf_scanner
        r = self.L.get("rf", {})
        self.mods["rf"] = make_rf_scanner(r, perch_xyz=self.perch, log=log)
        log(f"MODULE  rf: {r.get('source', 'bleak')} scan, allowlist {self.mods['rf'].allow}")

    def _init_vision(self):
        from eagle.vision import VisionJsonReader
        v = self.L.get("vision", {})
        if not v.get("jsonl"):
            raise RuntimeError("no life.vision.jsonl configured")
        cam = Camera.from_config(_mount(v.get("camera", {}), self.perch))
        self.mods["vision"] = (VisionJsonReader(v["jsonl"]), cam)
        log(f"MODULE  vision: reading {v['jsonl']}")

    def _init_radio(self):
        from eagle.link import make_transport
        from eagle.node import EagleRadio
        keys = load_keys(self.cfg)
        transport = make_transport(self.cfg["radio"], log=log)
        self.session = P.EagleSession(self.cfg["eagle"]["id"], keys["tag_hmac"], keys["fix_aes"],
                                      counter=P.CounterStore(self.cfg["eagle"].get("state_file")))
        self.radio = EagleRadio(self.cfg, transport, self.session, log=log)
        log(f"RADIO   {self.cfg['radio']['transport']}, counter at {self.session.counter.value}")

    # ---- one pass over the modules --------------------------------------------------------------
    def _due(self, name: str, now: float, every: float) -> bool:
        if now >= self.next_t.get(name, 0.0):
            self.next_t[name] = now + every
            return True
        return False

    def step_thermal(self, now):
        src, det = self.mods["thermal"]
        if self._th_cache is not None and self._th_cache[0] == now:
            _, frame, rad = self._th_cache
        else:
            _, frame, rad = src.read()
            if self.rec:
                self.rec.thermal(now, frame)
        blobs = det.detect(frame, rad, now)
        log(f"THERMAL {len(blobs)} warm bod{'y' if len(blobs) == 1 else 'ies'}"
            + "".join(f" | {b}" for b in blobs) + (f" ({len(det.rejected)} rejected)" if det.rejected else ""))
        return [LifeDetection("HEAT", now, xy=b.xy, cov=b.cov, temp_c=b.peak, source="thermal") for b in blobs]

    def step_audio(self, now):
        from eagle.sounds import detect_life_sounds, locate_sounds
        m = self.mods["audio"]
        a = self.cfg.get("audio", {})
        abs0, blk = m["cap"].window(float(a.get("window_s", 8.0)))
        audio = m["geom"].select(blk)
        loc, fs, out = m["loc"], m["geom"].fs, []
        _, codes = loc.process(audio)
        sounds = locate_sounds(audio, detect_life_sounds(audio, fs, m["clf"]), loc)
        speech = [(s.start, s.end) for s in sounds if s.category in ("VOICE", "DISTRESS")]
        for d in codes:
            if abs0 + d.match.end <= m["last_end"] or any(e.start < b and e.end > a_ for e in d.match.events
                                                          for a_, b in speech):
                continue
            log(f"SOUND   code {d.match.code}: bearing {d.bearing_deg:.1f} deg"
                + (f" -> E {d.xyz[0]:.1f} N {d.xyz[1]:.1f}" if d.has_fix else " (bearing only)"))
            out.append(LifeDetection("SOUND", now, xy=d.xyz[:2] if d.has_fix else None, cov=d.cov,
                                     origin=self.perch[:2], az_deg=d.az_world, sigma_az_deg=loc.sigma_az,
                                     category="SIGNAL", status_bits=d.status, source=f"code:{d.match.code}"))
        for s in sounds:
            if s.category is None or abs0 + s.end <= m["last_end"] or s.category == "SIGNAL" and codes:
                continue
            log(f"SOUND   {s}")
            out.append(LifeDetection("SOUND", now, xy=s.xy, cov=s.cov, origin=self.perch[:2], az_deg=s.az_world,
                                     sigma_az_deg=loc.sigma_az, category=s.category, source="sounds"))
        ends = [abs0 + s.end for s in sounds] + [abs0 + d.match.end for d in codes]
        if ends:
            m["last_end"] = max([m["last_end"]] + ends)
        return out

    def step_vitals(self, now):
        rd, xy, sig = self.mods["vitals"]
        out = []
        if rd.last is not None and now - rd.last.t < 30:
            r = rd.last
            alive = bool(r.presence and (r.breath_bpm > 0 or r.heart_bpm > 0))
            log(f"VITALS  module: breath {r.breath_bpm:.0f}/min heart {r.heart_bpm:.0f} bpm range {r.range_m:.1f} m "
                f"presence {int(r.presence)}")
            if r.presence:
                out.append(LifeDetection("VITALS", now, xy=xy, cov=iso_cov(sig), breath_bpm=r.breath_bpm or None,
                                         heart_bpm=r.heart_bpm or None, vitals=alive, update_position=False,
                                         source="vitals-module"))
        if rd.last_motion_t is not None and now - rd.last_motion_t < 15:
            log("VITALS  PIR: motion near the pellet")
            out.append(LifeDetection("MOTION", now, xy=xy, cov=iso_cov(2.0 * sig), update_position=False, source="pir"))
        est = rd.estimate()
        if est is not None:
            log(f"VITALS  radar IF: {est}")
            if est.status in ("ok", "no_vitals"):
                out.append(LifeDetection("VITALS", now, xy=xy, cov=iso_cov(sig), breath_bpm=est.breath_bpm,
                                         vitals=est.has_vitals, update_position=False, source="radar-if"))
        return out

    def step_rf(self, now):
        sc = self.mods["rf"]
        hits, anon = sc.scan(now)
        self.life.rf_anonymous = anon
        return sc.detections(hits, self.perch[:2], now)

    def step_vision(self, now):
        from eagle.vision import boxes_to_detections
        rd, cam = self.mods["vision"]
        v = self.L.get("vision", {})
        out = []
        for t, boxes, size in rd.read():
            dets = boxes_to_detections(boxes, cam, now, size=size, min_score=float(v.get("min_score", 0.4)),
                                       posture=v.get("posture", "auto"), floor_z=self.floor_z)
            out += dets
        people = [d for d in out if d.kind == "PERSON"]
        if people:
            log(f"VISION  {len(people)} person box(es) -> " + ", ".join(f"E {d.xy[0]:.1f} N {d.xy[1]:.1f}" for d in people))
        for d in out:
            if d.kind == "IDENTITY":
                face = f", face {d.match_face:.2f}" if d.match_face is not None else ""
                log(f"IDENT   match level {d.match_level} ({P.ID_LEVEL_NAMES[d.match_level]}) at "
                    f"E {d.xy[0]:.1f} N {d.xy[1]:.1f}: appearance {d.match_appearance:.2f}{face}")
        return out

    def step(self, now: float | None = None) -> list:
        now = self.clock() if now is None else now
        every = {"thermal": self.L.get("thermal", {}).get("every_s", 2.0),
                 "audio": self.L.get("audio", {}).get("every_s", 4.0),
                 "vitals": self.L.get("vitals", {}).get("every_s", 10.0),
                 "rf": self.L.get("rf", {}).get("every_s", 20.0),
                 "vision": self.L.get("vision", {}).get("every_s", 1.0)}
        dets = []
        if self.rec:
            self._record_step(now)
        for name in list(self.mods):
            if not self._due(name, now, float(every[name])):
                continue
            try:
                dets += getattr(self, f"step_{name}")(now)
                self.fail_count[name] = 0
            except Exception as e:
                log(f"{name.upper():<7} error: {type(e).__name__}: {e}")
                self.fail_count[name] = self.fail_count.get(name, 0) + 1
                if name in self.required and self.fail_count[name] >= MAX_REQUIRED_FAILS:
                    if self.rec:
                        self.rec.event("required_failed", module=name, error=f"{type(e).__name__}: {e}")
                    raise RequiredModuleError(f"required module {name} failed {MAX_REQUIRED_FAILS} times in a row")
        if dets:
            if self.rec:
                self.rec.detections(dets)
            try:
                self.life.update(dets, now=now)
            except Exception as e:                       # one bad detection must not stop the node
                log(f"LIFE    update error: {type(e).__name__}: {e}")
                for d in dets:
                    try:
                        self.life.update([d], now=now)
                    except Exception as e1:
                        log(f"LIFE    dropped {d.kind} from {d.source or '?'}: {type(e1).__name__}: {e1}")
        self.life.evaluate(now)
        self.maybe_report(now)
        self.maybe_send_target_fix(now)
        return dets

    def _record_step(self, now: float) -> None:
        """Thermal frames at the recording rate (between detection passes) and new audio samples."""
        self._th_cache = None
        try:
            self.rec.tick()
            if "thermal" in self.mods and self.rec.thermal_due(now):
                _, frame, rad = self.mods["thermal"][0].read()
                self._th_cache = (now, frame, rad)
                self.rec.thermal(now, frame)
            if "audio" in self.mods:
                self.rec.audio(self.mods["audio"]["cap"])
        except Exception as e:
            log(f"RECORD  error: {type(e).__name__}: {e}")

    # ---- reporting -----------------------------------------------------------------------------
    def maybe_report(self, now: float, force: bool = False):
        rep = self.L.get("report", {})
        sig = tuple((t.id, t.status, t.flags) for t in self.life.ranked(now))
        changed = bool(rep.get("on_change", True)) and sig != self.last_sig
        if not force and now - self.last_report < float(rep.get("min_gap_s", 5.0)):
            return None
        if not force and not changed and now - self.last_report < float(rep.get("every_s", 30.0)):
            return None
        if not self.life.tracks and self.last_sig is None:
            return None
        self.last_sig, self.last_report = sig, now
        log("LIFE    list:\n" + "          " + self.life.summary(now).replace("\n", "\n          "))
        if self.rec:
            self.rec.event("life_list", summary=self.life.summary(now), persons=self.life.life_persons(now))
        if self.radio is None:
            log("TX      (radio disabled)")
            return None
        o = self.cfg["site"]["origin"]
        from eagle.geo import LocalFrame
        lat, lon, _ = LocalFrame(o["lat"], o["lon"], o.get("hae_m", 0.0)).to_geodetic(*self.perch[:2], 0.0)
        head = P.LifeHead(int(time.time()), lat, lon, o.get("hae_m", 0.0) + self.floor_z)
        frames = self.session.encrypt_life(head, self.life.life_persons(now), int(rep.get("max_frame_bytes", 128)))
        self.radio.drain()
        out = self.radio.send_life(frames)
        if self.rec:
            self.rec.event("tx_life", frames=len(frames), bytes=[len(f) for f in frames])
        return out

    def maybe_send_target_fix(self, now: float):
        """One FIX for a track that the identity matcher verified as the missing person.

        LIFE lists carry status and position but no identity, so a face-verified track
        (``id_level`` >= report.target_fix.min_level, default 3) is reported once as a FIX with the
        match level in its class byte, then at most every ``every_s`` while it stays verified. The
        Nest logs it as ``RX FIX ... class ...ID-FACE``; the demo view shows SIGNAL SENT from that
        line, so the screen reflects a message that was actually received and decrypted."""
        tf = self.L.get("report", {}).get("target_fix", {})
        if not tf.get("enabled", True):
            return None
        min_level, every_s = int(tf.get("min_level", 3)), float(tf.get("every_s", 60.0))
        best = None
        for tr in self.life.ranked(now):
            if tr.id_level >= min_level and now - self.target_fix_t.get(tr.id, -1e9) >= every_s:
                if best is None or (tr.id_level, tr.score) > (best.id_level, best.score):
                    best = tr
        if best is None:
            return None
        self.target_fix_t[best.id] = now
        x = best.x
        o = self.cfg["site"]["origin"]
        from eagle.geo import LocalFrame
        lat, lon, _ = LocalFrame(o["lat"], o["lon"], o.get("hae_m", 0.0)).to_geodetic(float(x[0]), float(x[1]), 0.0)
        body = P.FixBody(lat, lon, o.get("hae_m", 0.0) + self.floor_z, best.ce_m,
                         P.with_identity(P.CLS_UNKNOWN, min(3, best.id_level)), min(1.0, best.score),
                         int(time.time()))
        log(f"TARGET  track #{best.id} verified as the missing person (ID-{P.ID_LEVEL_NAMES[min(3, best.id_level)]}) "
            f"at E {x[0]:.1f} N {x[1]:.1f}, ce {best.ce_m:.1f} m -> FIX")
        if self.rec:
            self.rec.event("tx_fix", track=best.id, match_level=int(best.id_level), east_m=float(x[0]),
                           north_m=float(x[1]), ce_m=float(best.ce_m))
        if self.radio is None:
            log("TX      (radio disabled)")
            return None
        self.radio.drain()
        return self.radio.send_fix(body)

    def run(self, duration: float | None = None, step_s: float = 0.5) -> None:
        t_end = None if duration is None else time.time() + duration
        log("LISTEN  (Ctrl-C to stop)")
        try:
            while t_end is None or time.time() < t_end:
                self.step()
                time.sleep(step_s)
        except KeyboardInterrupt:
            log("bye")
        finally:
            self.close()

    def close(self) -> None:
        for name, m in self.mods.items():
            try:
                if name == "audio":
                    m["cap"].stop()
                elif name == "thermal":
                    m[0].close()
                elif name == "vitals":
                    m[0].close()
            except Exception:
                pass
        if self.radio is not None:
            self.radio.t.close()
        if self.rec is not None:
            self.rec.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="YAML overriding config/example.yaml")
    ap.add_argument("--modules", nargs="*", help=f"subset of {' '.join(ALL_MODULES)} (default: life.modules)")
    ap.add_argument("--thermal", help="override life.thermal.source (synthetic | mlx90640 | uvc_y16 | gray8 | hti | none)")
    ap.add_argument("--vitals-port", help="override life.vitals.port")
    ap.add_argument("--transport", choices=["serial", "udp", "meshtastic"], help="override radio.transport")
    ap.add_argument("--port", help="override radio.serial_port")
    ap.add_argument("--no-radio", action="store_true", help="never transmit; log only")
    ap.add_argument("--duration", type=float, help="stop after N seconds (default: run until Ctrl-C)")
    ap.add_argument("--step", type=float, default=0.5, help="loop period, s")
    ap.add_argument("--require", nargs="+", choices=ALL_MODULES, metavar="MODULE",
                    help="exit with status 3 if any of these modules is unavailable at start or fails "
                         f"{MAX_REQUIRED_FAILS} steps in a row (systemd then restarts life_node)")
    ap.add_argument("--record", nargs="?", const="~/field_logs", default=None, metavar="DIR",
                    help="save thermal frames, raw mic channels and every detection to DIR/<timestamp>/ "
                         "(default DIR ~/field_logs)")
    args = ap.parse_args(argv)
    over: dict = {"radio": {}, "life": {"thermal": {}}}
    if args.transport:
        over["radio"]["transport"] = args.transport
    if args.port:
        over["radio"]["serial_port"] = args.port
    if args.thermal:
        over["life"]["thermal"]["source"] = args.thermal
    cfg = deep_merge(load_config(args.config), over)

    def _term(signum, frame):              # systemd stop: unwind through run()'s finally (closes the recording)
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _term)
    try:
        node = LifeNode(cfg, args)
        node.run(args.duration, args.step)
    except RequiredModuleError as e:
        log(f"EXIT    {e}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
