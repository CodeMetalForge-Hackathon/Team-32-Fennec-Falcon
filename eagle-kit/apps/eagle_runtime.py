#!/usr/bin/env python3
"""Eagle runtime: the field process on the Pi. Sensors -> models -> life list -> decision -> one sealed burst.

    python apps/eagle_runtime.py --sim night --transport udp          # no hardware: gate -> FIX -> nest_station
    python apps/nest_station.py --transport udp --print-cot           # (second terminal)
    python apps/eagle_runtime.py --config ../pi/field/field.yaml --transport udp --require audio thermal --record
    bash ../pi/field/use_runtime.sh                                   # field: run it as eagle-runtime.service

What it adds to apps/life_node.py, whose sensor code, recorder (--record) and --require it reuses:
* the decision engine (eagle/decide.py): per track P(him), gates, EMCON and the send policy.
  Nothing is transmitted until a track passes every gate; then ONE FIX goes out as two bursts of
  the same sealed frame 30-120 s apart. No periodic LIFE bursts, no heartbeat.
* the thermal person model (eagle/infer.py) on the Hailo NPU or the CPU, scored against each warm
  blob; the camera's person boxes and identity levels still come from apps/hailo_vision.py.
* one life-list update per sensor, so one person seen by two sensors stays one track.
* decisions in the recording's events.jsonl (``decision`` events; the field dashboard shows them),
  or in ~/eagle/decisions.jsonl when not recording. apps/replay_decisions.py replays a recording
  (this runtime's or life_node's) with other settings.

Modes (--mode or decide.mode): training = decide and log, radio muted unless --transport udp;
default = P >= 0.95, an injured person's report goes after a 15 min EMCON hold; strict = P >= 0.99,
never from the perch while EMCON is blocked, no re-sends, no positions on the Pi's disk.
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from life_node import MAX_REQUIRED_FAILS, LifeNode, RequiredModuleError, log   # noqa: E402

from eagle import protocol as P                                      # noqa: E402
from eagle.config import deep_merge, load_config, load_keys          # noqa: E402
from eagle.decide import (SEND, DecisionEngine, DecisionLog, LedgerLifeList, det_to_json,   # noqa: E402
                          planned_tx_from_config)
from eagle.fusion import LifeDetection                               # noqa: E402
from eagle.geo import LocalFrame                                     # noqa: E402

EVERY = {"thermal": 2.0, "audio": 4.0, "vitals": 10.0, "rf": 20.0, "vision": 1.0}
RF_TRANSPORTS = ("serial", "meshtastic")


def uses_test_keys(cfg: dict) -> bool:
    try:
        return load_keys(cfg)["fix_aes"] == load_keys(load_config(None))["fix_aes"]
    except Exception:
        return False


class EagleRuntime(LifeNode):
    def __init__(self, cfg: dict, args, clock=time.time, sim=None):
        mode = (getattr(args, "mode", None) or cfg.get("decide", {}).get("mode", "default")).lower()
        cfg = deep_merge(cfg, {"decide": {"mode": mode}})
        transport = cfg["radio"]["transport"]
        if mode == "training" and transport != "udp" and not args.no_radio:
            log("RADIO   training mode: RF muted (decisions are logged, nothing is transmitted)")
            args.no_radio = True
        if (not args.no_radio and transport in RF_TRANSPORTS and mode != "training" and uses_test_keys(cfg)
                and not getattr(args, "allow_test_keys", False)):
            raise SystemExit("REFUSED: config uses the public TEST keys from config/example.yaml on a real radio. "
                             "Put new keys in your config (keys.fix_aes, keys.tag_hmac: "
                             "python -c 'import secrets; print(secrets.token_hex(32))', same values on the Nest), "
                             "or pass --allow-test-keys for a bench test.")
        self.sim = sim
        if sim is not None:
            cfg = deep_merge(cfg, {"life": {"modules": []}})
            args.modules = None
        super().__init__(cfg, args, clock)
        self.life = LedgerLifeList.from_config(self.L.get("fusion", {}), perch_xy=self.perch[:2])
        o = cfg["site"]["origin"]
        self.frame = LocalFrame(o["lat"], o["lon"], o.get("hae_m", 0.0))
        self.engine = DecisionEngine(cfg.get("decide"), perch_xy=self.perch[:2], frame=self.frame,
                                     codes=cfg.get("codes"), path_loss=cfg["radio"].get("path_loss"), log=log)
        D = self.engine.c
        if self.rec is not None:                     # decisions go into the recording beside the detections
            log_path, sink = None, self._record_decision
            where = f"{self.rec.dir}/events.jsonl"
        else:
            log_path, sink = D["log"].get("path"), None
            where = log_path or "off"
        self.dlog = DecisionLog(log_path, float(D["log"].get("every_s", 10.0)), positions=self.engine.log_positions,
                                sink=sink)
        self.tmodel = None
        if "thermal" in self.mods:
            from eagle.infer import make_thermal_model
            self.tmodel = make_thermal_model(self.L.get("thermal", {}).get("model"), log=log)
        self.pending: list = []
        self.list_sig, self.list_t = None, -1e9
        e = self.engine
        log(f"DECIDE  mode {e.mode}: P >= {e.m['p_send']}, {e.m['min_modalities']} modalities or the code rule; "
            f"prior 1 in {1 + float(D['mission']['others_expected']):.0f}; transmit "
            f"{'on' if e.transmit_allowed and self.radio is not None else 'off'}; decisions -> {where}")

    def _record_decision(self, rec: dict) -> None:
        r = dict(rec)
        r["kind"] = r.pop("event", "tick")                # the recorder's own key is "event"
        self.rec.event("decision", **r)

    # ---- radio helpers ---------------------------------------------------------------------------
    def planned_tx_dbm(self) -> float:
        """Power the next FIX would use from the perch (the radio's own power control)."""
        if self.radio is not None:
            pl, _ = self.radio.nest_path_loss()
            return float(self.radio._power(P.required_tx_dbm(self.radio.sensitivity, pl, self.radio.margin,
                                                             self.radio.min_dbm, self.radio.max_dbm))[0])
        return planned_tx_from_config(self.cfg["radio"])

    def fix_body(self, d) -> P.FixBody:
        lat, lon, _ = self.frame.to_geodetic(float(d.xy[0]), float(d.xy[1]), 0.0)
        o = self.cfg["site"]["origin"]
        sim = self.sim is not None or self.engine.mode == "training"
        cls = P.with_identity((d.cls & 0x0F) | (P.CLS_SIM if sim else 0), d.id_level)
        own = float(self.engine.c["report"].get("own_position_ce_m", 0.0))
        ce = float(np.hypot(float(d.ce_m or 0.0), own))          # where he is relative to us + where we are
        return P.FixBody(lat=lat, lon=lon, alt_m=float(o.get("hae_m", 0.0)) + self.floor_z, ce_m=ce,
                         cls=cls, confidence=float(d.p), t_unix=int(time.time()), status=int(d.status_bits) & 0xFF)

    # ---- sensors ----------------------------------------------------------------------------------
    def step_thermal(self, now):
        """Warm blobs (the kit's detector) scored by the thermal person model where they overlap; model
        boxes with no blob (low contrast in the afternoon) become PERSON detections."""
        from eagle.vision import box_to_ground
        src, det = self.mods["thermal"]
        if self._th_cache is not None and self._th_cache[0] == now:   # already read for the recording
            _, frame, rad = self._th_cache
        else:
            _, frame, rad = src.read()
            if self.rec:
                self.rec.thermal(now, frame)
        blobs = det.detect(frame, rad, now)
        boxes = []
        if self.tmodel is not None and rad and self.tmodel.due():
            try:
                boxes = self.tmodel(frame)
            except Exception as e:
                log(f"MODEL   thermal person error: {type(e).__name__}: {e}")
        out, used = [], set()
        for b in blobs:
            d = LifeDetection("HEAT", now, xy=b.xy, cov=b.cov, temp_c=b.peak, source="thermal")
            hits = [(i, bx) for i, bx in enumerate(boxes) if bx.contains(b.u, b.v, pad=2.0)]
            if hits:
                i, bx = max(hits, key=lambda h: h[1].score)
                used.add(i)
                d.shape = bx.score
            out.append(d)
        for i, bx in enumerate(boxes):
            if i in used:
                continue
            r = box_to_ground(bx.xyxy, det.cam, posture="auto", floor_z=self.floor_z)
            if r is not None:
                pd = LifeDetection("PERSON", now, xy=r[0], cov=r[1], source=f"thermal-model:{r[2]}")
                pd.shape = bx.score
                out.append(pd)
        log(f"THERMAL {len(blobs)} warm bod{'y' if len(blobs) == 1 else 'ies'}, {len(boxes)} person box(es)"
            + "".join(f" | {d.source} E {d.xy[0]:.1f} N {d.xy[1]:.1f}"
                      + (f" shape {d.shape:.2f}" if getattr(d, "shape", None) is not None else "") for d in out))
        return out

    # ---- one tick ----------------------------------------------------------------------------------
    def step(self, now: float | None = None) -> list:
        now = self.clock() if now is None else now
        if self.rec:
            self._record_step(now)
        batches = []
        for name in list(self.mods):
            if not self._due(name, now, float(self.L.get(name, {}).get("every_s", EVERY[name]))):
                continue
            try:
                dets = getattr(self, f"step_{name}")(now)
                self.fail_count[name] = 0
            except Exception as e:
                log(f"{name.upper():<7} error: {type(e).__name__}: {e}")
                self.fail_count[name] = self.fail_count.get(name, 0) + 1
                if name in self.required and self.fail_count[name] >= MAX_REQUIRED_FAILS:
                    if self.rec:
                        self.rec.event("required_failed", module=name, error=f"{type(e).__name__}: {e}")
                    raise RequiredModuleError(f"required module {name} failed {MAX_REQUIRED_FAILS} times in a row")
                continue
            if dets:
                batches.append((name, dets))
        if self.sim is not None:
            batches += self.sim(now)
        for name, dets in batches:                 # one batch per sensor: one person seen twice = one track
            if self.rec:
                for d in dets:
                    self.rec.event("detection", sensor=name, **det_to_json(d))
            self._update(dets, now)
        self.life.evaluate(now)
        self.engine.ingest(self.life)
        self.engine.set_link(self.planned_tx_dbm())
        decisions = self.engine.tick(now, self.life)
        self.dlog.write(decisions, now)
        for d in decisions:
            if d.action == SEND:
                self.report(d, now)
                break
        self.flush(now)
        self.list_report(now)
        return decisions

    def _update(self, dets: list, now: float) -> None:
        try:
            self.life.update(dets, now=now)
        except Exception as e:                       # one bad detection must not stop the Eagle
            log(f"LIFE    update error: {type(e).__name__}: {e}")
            for d in dets:
                try:
                    self.life.update([d], now=now)
                except Exception as e1:
                    log(f"LIFE    dropped {d.kind} from {d.source or '?'}: {type(e1).__name__}: {e1}")

    def list_report(self, now: float) -> None:
        """The life list on the console and in the recording (the dashboard shows it); never on the radio."""
        rep = self.L.get("report", {})
        sig = tuple((t.id, t.status, t.flags) for t in self.life.ranked(now))
        if not self.life.tracks and self.list_sig is None:
            return
        if sig == self.list_sig and now - self.list_t < float(rep.get("every_s", 30.0)):
            return
        if now - self.list_t < float(rep.get("min_gap_s", 5.0)):
            return
        self.list_sig, self.list_t = sig, now
        log("LIFE    list (not transmitted):\n" + "          " + self.life.summary(now).replace("\n", "\n          "))
        if self.rec:
            self.rec.event("life_list", summary=self.life.summary(now), persons=self.life.life_persons(now))

    def maybe_report(self, now: float, force: bool = False):
        return None                                  # life_node's LIFE bursts are off: the gate decides

    # ---- sending ----------------------------------------------------------------------------------
    def report(self, d, now: float) -> None:
        body = self.fix_body(d)
        pos = (f" at E {d.xy[0]:.1f} N {d.xy[1]:.1f} ce {body.ce_m:.0f} m" if self.engine.log_positions else "")
        what = (f"FIX for {d.subject}: P {d.p:.3f}{pos}, class {P.class_name(body.cls)} "
                f"({d.reason}; false-alarm risk {d.false_alarm.split(':')[0]})")
        rec = {"event": "SEND", "subject": d.subject, "p": round(d.p, 4), "reason": d.reason, "resend": d.resend}
        bench = self.engine.mode == "training" and self.cfg["radio"]["transport"] == "udp"
        if self.radio is None or not (self.engine.transmit_allowed or bench):
            log(f"TX      (muted, {self.engine.mode} mode{', no radio' if self.radio is None else ''}) {what}")
            rec["muted"] = True
        else:
            self.radio.drain()
            res = self.radio.send_fix(body)
            delay = self.engine.repeat_delay_s()
            self.pending.append({"due": now + delay, "frame": res.frame, "tx": res.tx_dbm, "subject": d.subject})
            log(f"TX      burst 1/2 {what}; burst 2 in {delay:.0f} s")
            rec.update({"bytes": len(res.frame), "tx_dbm": res.tx_dbm, "airtime_ms": round(res.airtime_s * 1e3, 1),
                        "burst": 1})
        self.engine.mark_sent(d.subject, now)
        self.dlog.write([], now, rec)
        if self.engine.m.get("wipe_after_send") and rec.get("muted"):
            self.engine.wipe(d.subject)

    def flush(self, now: float) -> None:
        for item in [p for p in self.pending if now >= p["due"]]:
            self.pending.remove(item)
            toa = self.radio.t.send(item["frame"], item["tx"])
            log(f"TX      burst 2/2 for {item['subject']}: the same sealed frame ({len(item['frame'])} B @ "
                f"{item['tx']:+d} dBm, {toa * 1e3:.1f} ms); the Nest keeps one copy")
            self.dlog.write([], now, {"event": "SEND", "subject": item["subject"], "burst": 2})
            if self.engine.m.get("wipe_after_send"):
                self.engine.wipe(item["subject"])
                item["frame"] = None

    # ---- loops ------------------------------------------------------------------------------------
    def run(self, duration: float | None = None, step_s: float = 0.5) -> None:
        if self.sim is not None:
            return self.run_sim(duration, step_s)
        super().run(duration, step_s)

    def run_sim(self, duration: float, step_s: float, speed: float | None = None) -> None:
        speed = getattr(self.args, "speed", 20.0) if speed is None else speed
        log(f"SIM     {getattr(self.args, 'sim', '')}: {getattr(self, 'sim_story', '')}")
        log(f"SIM     expected: {getattr(self, 'sim_expected', '')}")
        t0, t = time.time(), 0.0
        try:
            while t <= duration + 1e-9:
                self.step(t0 + t)
                t += step_s
                if speed:
                    time.sleep(step_s / speed)
            while self.pending:                                   # let the second burst go out
                t += step_s
                self.step(t0 + t)
                if speed:
                    time.sleep(step_s / speed)
        except KeyboardInterrupt:
            log("bye")
        finally:
            self.close()


def make_sim(name: str, perch_xy):
    """(sim(now) -> [(sensor, dets)], duration_s, story, expected) for a scene in eagle/scenes.py."""
    from eagle.scenes import make_scene
    scene, dur, story, expected = make_scene(name, perch_xy)
    start: dict = {}

    def sim(now):
        start.setdefault("t0", now)
        batches = scene(round(now - start["t0"], 6))
        for _, dets in batches:                          # scene times are relative; the runtime clock is not
            for d in dets:
                d.t = now
        return batches
    return sim, dur, story, expected


def main(argv=None) -> int:
    from life_node import ALL_MODULES
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="YAML overriding config/example.yaml (field: ../pi/field/field.yaml)")
    ap.add_argument("--mode", choices=["training", "default", "strict"], help="override decide.mode")
    ap.add_argument("--modules", nargs="*", help=f"subset of {' '.join(ALL_MODULES)} (default: life.modules)")
    ap.add_argument("--thermal", help="override life.thermal.source (synthetic | hti | mlx90640 | uvc_y16 | gray8 | none)")
    ap.add_argument("--vitals-port", help="override life.vitals.port")
    ap.add_argument("--transport", choices=["serial", "udp", "meshtastic"], help="override radio.transport")
    ap.add_argument("--port", help="override radio.serial_port")
    ap.add_argument("--no-radio", action="store_true", help="never transmit; decide and log only")
    ap.add_argument("--allow-test-keys", action="store_true", help="bench only: allow the example keys on a real radio")
    ap.add_argument("--require", nargs="+", choices=ALL_MODULES, metavar="MODULE",
                    help=f"exit with status 3 if any of these is unavailable at start or fails {MAX_REQUIRED_FAILS} "
                         "steps in a row (systemd then restarts the runtime)")
    ap.add_argument("--record", nargs="?", const="~/field_logs", default=None, metavar="DIR",
                    help="save thermal frames, raw mic channels, detections and decisions to DIR/<timestamp>/")
    ap.add_argument("--sim", help="no hardware: night | cough | patrol | unconscious | bearing | crowded")
    ap.add_argument("--speed", type=float, default=20.0, help="--sim time compression (0 = as fast as possible)")
    ap.add_argument("--duration", type=float, help="stop after N seconds (default: until Ctrl-C; --sim: the scene)")
    ap.add_argument("--step", type=float, default=0.5, help="loop period, s (--sim: 1 s unless set)")
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
    sim, story, expected, dur = None, "", "", None
    if args.sim:
        sim, dur, story, expected = make_sim(args.sim, cfg["perch"]["enu_m"][:2])
    try:
        rt = EagleRuntime(cfg, args, sim=sim)
        rt.sim_story, rt.sim_expected = story, expected
        if args.sim:
            rt.run(args.duration or dur, args.step if args.step != 0.5 else 1.0)
        else:
            rt.run(args.duration, args.step)
    except RequiredModuleError as e:
        log(f"EXIT    {e}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
