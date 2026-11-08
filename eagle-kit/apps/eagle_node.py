#!/usr/bin/env python3
"""Eagle node: listen -> detect briefed code -> DOA -> fix -> (poll tag) -> ONE encrypted burst.

    python apps/eagle_node.py --list-devices
    python apps/eagle_node.py --config config/local.yaml                   # live from the array
    python apps/eagle_node.py --config config/local.yaml --clap-test       # calibration, never transmits
    python apps/eagle_node.py --wav capture.wav --transport udp            # offline file -> Nest over UDP
    python apps/eagle_node.py --record 10 raw.wav                          # save raw multichannel audio
    python apps/eagle_node.py --no-radio                                   # compute + log only

Every step prints one log line: DETECT, DOA/FIX, POLL, TX.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eagle import protocol as P                                    # noqa: E402
from eagle.audio_io import LiveCapture, list_devices, read_wav, write_wav  # noqa: E402
from eagle.config import array_geometry, deep_merge, load_config, load_keys  # noqa: E402
from eagle.detect import detect_taps                               # noqa: E402
from eagle.link import make_transport                              # noqa: E402
from eagle.locate import body_to_world, compass_bearing            # noqa: E402
from eagle.node import EagleRadio                                  # noqa: E402
from eagle.pipeline import Locator, describe                       # noqa: E402


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def wav_to_mics(path, geom):
    fs, x = read_wav(path)
    if x.shape[0] == geom.n_mics:
        return fs, x
    if x.shape[0] >= geom.device_channels:
        return fs, x[geom.channels]
    raise SystemExit(f"{path}: {x.shape[0]} channels, preset {geom.name} needs {geom.n_mics} "
                     f"(mics) or {geom.device_channels} (raw device channels)")


class Node:
    def __init__(self, cfg, args):
        self.cfg, self.args = cfg, args
        self.geom = array_geometry(cfg)
        self.loc = Locator(cfg, geometry=self.geom)
        a = self.geom
        log(f"ARRAY   {a.name}: {a.n_mics} mics @ {a.fs} Hz, device channels {a.device_channels} -> "
            f"{a.channels} (verified: {a.verified})")
        p = self.loc
        log(f"PERCH   ENU {p.perch.tolist()} heading {p.heading:.1f} deg true, az offset {p.az_offset:.1f} deg, "
            f"source height {p.src_h} m, c = {p.c:.1f} m/s")
        self.radio = None
        if not args.no_radio and not args.clap_test:
            keys = load_keys(cfg)
            if keys["covert"] is not None:
                log("WARNING keys.covert is set on this Eagle: remove it so a captured Eagle cannot read duress")
            transport = make_transport(cfg["radio"], log=log)
            session = P.EagleSession(cfg["eagle"]["id"], keys["tag_hmac"], keys["fix_aes"],
                                     counter=P.CounterStore(cfg["eagle"].get("state_file")))
            self.radio = EagleRadio(cfg, transport, session, log=log)
            r = cfg["radio"]
            log(f"RADIO   {r['transport']} {r['freq_mhz']} MHz SF{r['sf']} BW{r['bw_khz']} CR4/{r['cr']}, "
                f"power {r['min_dbm']}..{r['max_dbm']} dBm, counter at {session.counter.value}")
        self.last_end_abs = -1

    # ---- one analysis window -------------------------------------------------------------
    def process(self, audio, abs_start: int = 0, settle_s: float = 0.0) -> int:
        events, dets = self.loc.process(audio)
        n_new = 0
        n = np.shape(audio)[1]
        for det in dets:
            end_abs = abs_start + det.match.end
            if end_abs <= self.last_end_abs:
                continue          # already reported in an earlier window
            if n - det.match.end < settle_s * self.loc.fs:
                continue          # let a longer code finish first; it is re-found next window
            self.last_end_abs = end_abs
            n_new += 1
            evs = ", ".join(str(e) for e in det.match.events)
            log(f"DETECT  {det.match}  [{evs}]")
            log(f"FIX     {describe(det)}")
            if not det.has_fix:
                continue
            self.report(det)
        return n_new

    def report(self, det) -> None:
        if self.radio is None:
            log("TX      (radio disabled)")
            return
        self.radio.drain()
        poll = None
        if (self.cfg.get("poll", {}).get("enabled", True) and not self.args.no_poll
                and self.radio.s.k_tag is not None):
            poll = self.radio.poll_tag(det.fix.slant_range)
        body = self.loc.fix_body(det, int(time.time()))
        self.radio.send_fix(body, poll)

    def blind_poll(self) -> None:
        """Tag-only presence check: position = below the perch, ce = RSSI range."""
        poll = self.radio.poll_tag(None)
        if poll.reply is None:
            return
        pl = P.path_loss_from(poll.tx_dbm, poll.reply.rssi_heard)
        m = self.cfg["radio"].get("path_loss", {})
        rng = P.distance_from_path_loss(pl, m.get("pl_d0_db", 31.7), m.get("exponent", 3.0), m.get("d0_m", 1.0))
        x, y = self.loc.perch[:2]
        lat, lon, hae = self.loc.frame.to_geodetic(x, y, self.loc.floor_z)
        log(f"FIX     tag-only: path loss {pl:.0f} dB -> within ~{rng:.0f} m of the perch")
        body = P.FixBody(lat, lon, hae, rng, P.CLS_TAG_RSSI, 0.0, int(time.time()))
        self.radio.send_fix(body, poll)

    # ---- modes ---------------------------------------------------------------------------
    def run_wav(self, path) -> None:
        fs, x = wav_to_mics(path, self.geom)
        if fs != self.loc.fs:
            raise SystemExit(f"WAV is {fs} Hz but the preset/config says {self.loc.fs} Hz (set audio.fs)")
        log(f"WAV     {path}: {x.shape[1] / fs:.1f} s, {x.shape[0]} mics")
        if self.process(x) == 0:
            log("DETECT  no briefed code found")

    def run_live(self) -> None:
        a = self.cfg["audio"]
        win, step = float(a.get("window_s", 8.0)), float(a.get("step_s", 1.0))
        cap = LiveCapture(a.get("device"), self.geom.fs, self.geom.device_channels, seconds=win + 2).start()
        log(f"LISTEN  window {win:.0f} s, step {step:.1f} s (Ctrl-C to stop)")
        poll_every = float(self.cfg.get("poll", {}).get("every_s", 0) or 0)
        if self.radio is None or self.radio.s.k_tag is None:
            poll_every = 0.0
        next_poll = time.time() + poll_every if poll_every else float("inf")
        try:
            while True:
                time.sleep(step)
                if cap.total < win * self.geom.fs * 0.5:
                    continue
                abs_start, blk = cap.window(win)
                self.process(self.geom.select(blk), abs_start, float(a.get("settle_s", 1.5)))
                if self.radio is not None:
                    self.radio.drain()
                    if time.time() >= next_poll:
                        self.blind_poll()
                        next_poll = time.time() + poll_every
                if cap.overflows:
                    log(f"WARNING audio overflows: {cap.overflows} (CPU too slow? raise step_s)")
                    cap.overflows = 0
        except KeyboardInterrupt:
            log("bye")
        finally:
            cap.stop()

    def clap_test(self, wav=None, seconds: float = 20.0) -> None:
        """Print body + world az/el for every clap; summarise mean/std. Never transmits."""
        if wav:
            fs, x = wav_to_mics(wav, self.geom)
        else:
            cap = LiveCapture(self.cfg["audio"].get("device"), self.geom.fs, self.geom.device_channels,
                              seconds=seconds + 2).start()
            log(f"CLAP    clap {seconds:.0f} s: first ON the 0 deg mark, ~5 m away, at the target's height")
            blk = cap.record(seconds)
            cap.stop()
            fs, x = self.geom.fs, self.geom.select(blk)
        taps = detect_taps(x, fs)
        az, el = [], []
        for t in taps:
            d = self.loc.srp.localize(x, [(t.start, t.end)])
            azw, elw = body_to_world(d.az, d.el, heading_deg=self.loc.heading, az_offset_deg=self.loc.az_offset)
            az.append(d.az)
            el.append(d.el)
            log(f"CLAP    t={t.t0:6.2f}s body az {d.az:6.1f} el {d.el:6.1f} conf {d.confidence:.2f} -> "
                f"bearing {float(compass_bearing(azw)):6.1f} deg true, el {elw:5.1f}")
        if az:
            a = np.unwrap(np.radians(az))
            log(f"CLAP    {len(az)} claps: body az mean {np.degrees(a.mean()) % 360:.1f} std {np.degrees(a.std()):.1f}, "
                f"el mean {np.mean(el):.1f} std {np.std(el):.1f}.  If all were ON the 0 deg mark set "
                f"perch.az_offset_deg = {np.degrees(a.mean()) % 360:.1f}; use the std values for doa.sigma_*")
        else:
            log("CLAP    no claps detected (too quiet? lower detect.tap.on_db)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="YAML overriding config/example.yaml (e.g. config/local.yaml)")
    ap.add_argument("--wav", help="process a multichannel WAV instead of the live device")
    ap.add_argument("--preset", help="override audio.preset")
    ap.add_argument("--device", help="override audio.device (index or name substring)")
    ap.add_argument("--transport", choices=["serial", "udp", "meshtastic"], help="override radio.transport")
    ap.add_argument("--port", help="override radio.serial_port")
    ap.add_argument("--no-radio", action="store_true", help="never transmit; log only")
    ap.add_argument("--no-poll", action="store_true", help="do not challenge the tag")
    ap.add_argument("--force-dbm", type=int, help="DEMO ONLY: fixed TX power instead of power control")
    ap.add_argument("--clap-test", action="store_true", help="calibration: DOA of each clap, no radio")
    ap.add_argument("--seconds", type=float, default=20.0, help="clap-test duration")
    ap.add_argument("--record", nargs=2, metavar=("SECONDS", "OUT_WAV"), help="record raw device channels")
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--set-counter", type=int, help="raise the persistent frame counter to N and exit")
    args = ap.parse_args(argv)

    if args.list_devices:
        print(list_devices())
        return 0
    over: dict = {"audio": {}, "radio": {}}
    if args.preset:
        over["audio"]["preset"] = args.preset
    if args.device:
        over["audio"]["device"] = args.device
    if args.transport:
        over["radio"]["transport"] = args.transport
    if args.port:
        over["radio"]["serial_port"] = args.port
    if args.force_dbm is not None:
        over["radio"]["force_dbm"] = args.force_dbm
    cfg = deep_merge(load_config(args.config), over)

    if args.set_counter is not None:
        c = P.CounterStore(cfg["eagle"].get("state_file"))
        if args.set_counter <= c.value:
            raise SystemExit(f"counter is already {c.value}; it can only go up")
        c.value = args.set_counter - 1
        c.next()
        print(f"counter now {c.value}")
        return 0
    if args.record:
        geom = array_geometry(cfg)
        secs, out = float(args.record[0]), args.record[1]
        cap = LiveCapture(cfg["audio"].get("device"), geom.fs, geom.device_channels, seconds=secs + 2).start()
        log(f"REC     {secs:.0f} s, {geom.device_channels} ch @ {geom.fs} Hz -> {out}")
        blk = cap.record(secs)
        cap.stop()
        write_wav(out, geom.fs, blk.T, float32=True)
        return 0

    node = Node(cfg, args)
    if args.clap_test:
        node.clap_test(args.wav, args.seconds)
    elif args.wav:
        node.run_wav(args.wav)
    else:
        node.run_live()
    return 0


if __name__ == "__main__":
    sys.exit(main())
