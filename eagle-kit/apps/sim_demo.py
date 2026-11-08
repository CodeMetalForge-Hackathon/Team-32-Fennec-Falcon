#!/usr/bin/env python3
"""Eagle end-to-end simulation - no hardware needed.

A person under a perch at height h whistles a briefed code. We synthesise what the array
hears (noise + reflections), run the real detector / SRP-PHAT / locator, poll the person's
tag over the mock radio, send ONE encrypted FIX to the Nest at the lowest power that closes
the link, print the CoT the Nest would send to ATAK, and report what an enemy SDR saw.

    python apps/sim_demo.py                       # defaults: ReSpeaker USB, h = 8 m
    python apps/sim_demo.py --preset minidsp_uma16_v2 --code long_short_short --duress
    python apps/sim_demo.py --atak 192.168.1.50   # also push the marker to a real ATAK phone
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eagle import protocol as P                                    # noqa: E402
from eagle.config import array_geometry, deep_merge, load_config, load_keys  # noqa: E402
from eagle.cot import UdpUnicastSender, parse_cot                  # noqa: E402
from eagle.doa import vector_to_az_el, wrap180                     # noqa: E402
from eagle.link import EnemyScanner, MockChannel, PathLossModel    # noqa: E402
from eagle.locate import Bearing, compass_bearing, fuse_bearings, heading_to_yaw  # noqa: E402
from eagle.nest import NestStation                                 # noqa: E402
from eagle.node import EagleRadio                                  # noqa: E402
from eagle.pipeline import Locator, describe                       # noqa: E402
from eagle.sim import simulate_array_capture                       # noqa: E402


def run_perch(cfg, geom, fs, perch, heading, src, args, seed):
    loc = Locator(cfg, geometry=geom, fs=fs, perch_enu=perch, heading_deg=heading)
    refl = [] if args.no_reflections else [
        ((0, 0, 1), loc.floor_z, 0.5),                 # ground bounce
        ((1, 0, 0), src[0] + 7.0, 0.3),                # wall / rock face 7 m east of the person
        ((0, 1, 0), src[1] - 12.0, 0.25),              # tree line 12 m south
    ]
    audio, cs = simulate_array_capture(geom.positions, fs, perch, heading_to_yaw(heading), src, args.code,
                                       snr_db=args.snr, reflections=refl, c=loc.c, seed=seed)
    events, dets = loc.process(audio)
    return loc, audio, cs, events, dets, len(refl)


def plot_floor(path, perches, src, dets, fused, nest=None):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Ellipse
    except ImportError:
        print("[plot] matplotlib not installed - skipping PNG")
        return None
    fig, ax = plt.subplots(figsize=(7, 6))
    colors = ["#c0392b", "#7d3c98", "#2e86c1"]
    for k, ((name, p), det) in enumerate(zip(perches, dets)):
        col = colors[k % len(colors)]
        ax.plot(p[0], p[1], "^", ms=11, color=col, label=f"perch {name} (h={p[2]:.0f} m)")
        if det is not None:
            a = np.radians(det.az_world)
            L = 1.6 * np.hypot(*(np.asarray(src[:2]) - p[:2]))
            ax.plot([p[0], p[0] + L * np.cos(a)], [p[1], p[1] + L * np.sin(a)], "--", lw=0.8, color=col)
            if det.has_fix:
                ax.plot(*det.xyz[:2], "x", ms=10, mew=2, color=col, label=f"estimate {name} (single perch)")
                ea, eb, ang = det.ellipse95
                ax.add_patch(Ellipse(det.xyz[:2], 2 * ea, 2 * eb, angle=ang, fill=False, color=col,
                                     lw=1.2, label=f"95% ellipse {name}"))
    if fused is not None and np.all(np.isfinite(fused.xy)):
        ax.plot(*fused.xy, "P", ms=10, color="#117a65", label="fused (2 perches)")
        ea, eb, ang = fused.ellipse95
        ax.add_patch(Ellipse(fused.xy, 2 * ea, 2 * eb, angle=ang, fill=False, color="#117a65", lw=1.2,
                             label="95% ellipse fused"))
    ax.plot(src[0], src[1], "*", ms=16, color="#f39c12", mec="k", label="true position")
    ax.set_xlabel("east (m)")
    ax.set_ylabel("north (m)")
    ax.set_aspect("equal")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="best")
    ax.set_title("Eagle simulation - floor view")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="YAML overriding config/example.yaml")
    ap.add_argument("--preset", help="array preset (default: audio.preset)")
    ap.add_argument("--fs", type=int, help="sample rate (default: preset)")
    ap.add_argument("--height", type=float, default=8.0, help="perch height above the floor, m")
    ap.add_argument("--heading", type=float, default=30.0, help="array 0-deg mark, compass deg")
    ap.add_argument("--target", type=float, nargs=2, default=(14.0, 9.0), metavar=("E", "N"))
    ap.add_argument("--code", default="three_whistles",
                    choices=["three_whistles", "long_short_short", "taps3", "tap_2_3"])
    ap.add_argument("--snr", type=float, default=15.0, help="in-band SNR at the mics, dB")
    ap.add_argument("--no-reflections", action="store_true")
    ap.add_argument("--second-perch", type=float, nargs=4, default=(30.0, -6.0, 6.0, 300.0),
                    metavar=("E", "N", "H", "HEADING"))
    ap.add_argument("--single", action="store_true", help="skip the two-perch bearing fusion")
    ap.add_argument("--nest", type=float, nargs=2, default=(600.0, 500.0), metavar=("E", "N"))
    ap.add_argument("--scanner", type=float, nargs=2, default=(-1300.0, 900.0), metavar=("E", "N"))
    ap.add_argument("--exponent", type=float, default=3.0, help="path-loss exponent of the mock air")
    ap.add_argument("--no-beacon", action="store_true", help="use the configured link budget, not a Nest beacon")
    ap.add_argument("--no-tag", action="store_true", help="person has no tag (acoustic-only, unconfirmed)")
    ap.add_argument("--duress", action="store_true", help="person answers with the duress gesture")
    ap.add_argument("--atak", metavar="HOST", help="also send the CoT to HOST:4242/udp")
    ap.add_argument("--plot", default="sim_floor.png", help="PNG path ('' to skip)")
    ap.add_argument("--save-wav", metavar="PATH", help="also save perch A's synthetic capture (for eagle_node --wav)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    cfg = deep_merge(cfg, {"perch": {"az_offset_deg": 0.0, "pitch_deg": 0.0, "roll_deg": 0.0},
                           "audio": {"mirror_y": False}})
    geom = array_geometry(cfg, args.preset, args.fs)
    fs = geom.fs
    keys = load_keys(cfg)
    src_h = float(cfg["site"].get("source_height_m", 0.5))
    floor_z = float(cfg["site"].get("floor_z", 0.0))
    perch_a = np.array([0.0, 0.0, floor_z + args.height])
    src = np.array([args.target[0], args.target[1], floor_z + src_h])
    out: dict = {}

    print(f"=== Eagle simulation: {geom.name} ({geom.n_mics} mics @ {fs} Hz), perch h={args.height:.1f} m, "
          f"code {args.code}, SNR {args.snr:.0f} dB ===")
    t0 = time.time()
    loc, audio, cs, events, dets, nrefl = run_perch(cfg, geom, fs, perch_a, args.heading, src, args, args.seed)
    print(f"[audio]  {audio.shape[1] / fs:.1f} s x {audio.shape[0]} ch, {len(cs.events)} code elements, "
          f"{nrefl} reflections")
    if args.save_wav:
        from eagle.audio_io import write_wav
        write_wav(args.save_wav, fs, audio / max(1e-9, 1.1 * np.abs(audio).max()), float32=True)
        print(f"[audio]  saved {args.save_wav} (perch at ENU {perch_a.tolist()}, heading {args.heading} deg)")
    print(f"[detect] {len(events)} events: " + ", ".join(str(e) for e in events))
    good = [d for d in dets if d.has_fix]
    if not dets:
        print("[detect] no briefed code found - nothing to report")
        return {"ok": False}
    det = good[0] if good else dets[0]
    print(f"[detect] code matched: {det.match}")
    true_az, true_el = vector_to_az_el(src - perch_a)
    true_rng = float(np.hypot(*(src[:2] - perch_a[:2])))
    print(f"[doa]    {describe(det)}   ({time.time() - t0:.2f} s)")
    out["bearing_err_deg"] = float(wrap180(det.az_world - true_az))
    out["el_err_deg"] = float(det.el_world - true_el)
    print(f"[truth]  bearing {float(compass_bearing(true_az)):.1f} deg true, el {true_el:.1f} deg, range {true_rng:.1f} m")
    if det.has_fix:
        out["range_err_m"] = det.fix.ground_range - true_rng
        out["pos_err_m"] = float(np.linalg.norm(det.xyz[:2] - src[:2]))
        print(f"[error]  bearing {out['bearing_err_deg']:+.2f} deg | elevation {out['el_err_deg']:+.2f} deg | "
              f"range {out['range_err_m']:+.2f} m | position {out['pos_err_m']:.2f} m")
    else:
        print(f"[error]  bearing {out['bearing_err_deg']:+.2f} deg; no position: {det.note}")

    # ---- second perch: azimuth-only bearings fused --------------------------------------------
    fused, det_b, perch_b = None, None, None
    if not args.single:
        e, n, h, hd = args.second_perch
        perch_b = np.array([e, n, floor_z + h])
        _, _, _, _, dets_b, _ = run_perch(cfg, geom, fs, perch_b, hd, src, args, args.seed + 1)
        if dets_b:
            det_b = dets_b[0]
            sa, sb = loc.sigma_az, loc.sigma_az
            fused = fuse_bearings([Bearing(perch_a[0], perch_a[1], det.az_world, sa),
                                   Bearing(perch_b[0], perch_b[1], det_b.az_world, sb)])
            out["fused_err_m"] = float(np.linalg.norm(fused.xy - src[:2]))
            a95, b95, _ = fused.ellipse95
            print(f"[fuse]   perch B at E{e:.0f} N{n:.0f}: bearing {det_b.bearing_deg:.1f} deg -> crossing "
                  f"E {fused.xy[0]:.1f} N {fused.xy[1]:.1f}, error {out['fused_err_m']:.2f} m, "
                  f"95% ellipse {a95:.1f} x {b95:.1f} m{'' if fused.ok else ' (' + fused.message + ')'}")

    if not det.has_fix:
        return out

    # ---- radio: mock air with an enemy scanner ----------------------------------------------
    r = cfg["radio"]
    ch = MockChannel(PathLossModel(exponent=args.exponent), sf=int(r["sf"]), bw_khz=float(r["bw_khz"]),
                     cr=int(r["cr"]), noise_figure_db=float(r.get("noise_figure_db", 6.0)), seed=args.seed)
    nest_xyz = np.array([args.nest[0], args.nest[1], 2.0])
    eagle_t = ch.add_node("eagle", perch_a)
    nest_t = ch.add_node("nest", nest_xyz)
    scanner = ch.add_scanner(EnemyScanner("enemy-sdr", [args.scanner[0], args.scanner[1], 2.0]))
    sim_cfg = deep_merge(cfg, {"radio": {"path_loss": {"exponent": args.exponent},
                                         "nest_link": {"distance_m": float(np.linalg.norm(nest_xyz - perch_a))}}})
    session = P.EagleSession(cfg["eagle"]["id"], keys["tag_hmac"], keys["fix_aes"],
                             counter=P.CounterStore(None, start=1000))
    clock = [time.time()]
    eagle = EagleRadio(sim_cfg, eagle_t, session, log=lambda m: print(f"[eagle]  {m}"), clock=lambda: clock[0])
    nest = NestStation(cfg, senders=[], log=lambda m: print(f"[nest]   {m}"), sim=True)
    if not args.no_tag:
        tag_t = ch.add_node("tag", src)
        tag = P.TagLogic(7, keys["tag_hmac"], keys["covert"], sens_dbm=eagle.sensitivity,
                         margin_db=eagle.margin, status=P.ST_INJURED | P.ST_WATER, battery=87)
        tag.duress = args.duress

        def respond(rx):
            resp = tag.on_frame(rx.payload, rx.rssi)
            if resp is None:
                return None
            print(f"[tag]    heard CHALLENGE at {rx.rssi:.0f} dBm -> ONE reply after {resp.delay_ms:.0f} ms "
                  f"@ {resp.tx_dbm:+d} dBm{' (duress gesture active)' if tag.duress else ''}")
            return resp.frame, resp.tx_dbm, resp.delay_ms / 1e3
        tag_t.responder = respond

    if not args.no_beacon:
        nest.beacon(nest_t, int(cfg["nest"].get("beacon_dbm", 10)))
        ch.advance(5.0)
        eagle.drain()

    poll = None
    if not args.no_tag and cfg.get("poll", {}).get("enabled", True):
        poll = eagle.poll_tag(det.fix.slant_range)
        if poll.reply is not None:
            # an attacker 60 m away recorded the challenge and replays it at full power to
            # make the person's tag transmit again: the tag must stay silent
            attacker = ch.add_node("attacker", src + np.array([45.0, -40.0, 1.0]))
            before = tag.transmissions
            attacker.send(P.encode_challenge(keys["tag_hmac"], session.eagle_id, poll.nonce,
                                             500, poll.tx_dbm), 22)
            print(f"[replay] attacker replays the recorded CHALLENGE at +22 dBm -> tag replies: "
                  f"{'NO (ignored)' if tag.transmissions == before else 'YES (BUG)'}")
            out["replay_ignored"] = tag.transmissions == before

    t_unix = int(clock[0])
    body = loc.fix_body(det, t_unix, sim=True)
    sent = eagle.send_fix(body, poll)
    out["fix_bytes"] = len(sent.frame)
    out["fix_dbm"] = sent.tx_dbm
    out["fix_airtime_ms"] = sent.airtime_s * 1e3

    result = None
    while True:
        rx = nest_t.recv()
        if rx is None:
            break
        res = nest.handle(rx, now=t_unix)
        if res is not None:
            result = res
    if result is None:
        print("[nest]   nothing decoded - the burst did not reach the Nest (raise margin or power)")
        out["nest_ok"] = False
    else:
        rep, xml = result
        out["nest_ok"] = True
        out["duress_seen_by_nest"] = rep.duress
        c = parse_cot(xml)
        print(f"[cot]    type={c['type']} uid={c['uid']} lat={c['point_lat']} lon={c['point_lon']} "
              f"ce={c['point_ce']} m callsign='{c['callsign']}'")
        print("[cot]    " + xml.decode())
        if args.atak:
            UdpUnicastSender(args.atak).send(xml)
            print(f"[cot]    sent to {args.atak}:4242/udp")

    # ---- what did the enemy see? -------------------------------------------------------------
    d_sc = float(np.linalg.norm(scanner.xyz - perch_a))
    print(f"[scanner] enemy SDR {d_sc / 1e3:.2f} km from the perch, {np.linalg.norm(scanner.xyz - nest_xyz) / 1e3:.2f} km "
          f"from the Nest (noise floor {scanner.hits[0].rx_dbm - scanner.hits[0].snr_db:.0f} dBm in "
          f"{ch.bw_khz:g} kHz, detects at SNR >= {scanner.threshold_db:.0f} dB):")
    for h in scanner.hits:
        print(f"           {h.src:6s} {h.n_bytes:3d} B @ {h.tx_dbm:+3d} dBm -> {h.rx_dbm:7.1f} dBm, SNR {h.snr_db:+6.1f} dB, "
              f"{h.airtime_s * 1e3:6.1f} ms : {'SEEN' if h.detected else 'not seen'}")
    fix_hit = [h for h in scanner.hits if h.src == "eagle" and h.n_bytes == P.FIX_LEN][-1]
    snr_max = fix_hit.snr_db + (eagle.max_dbm - fix_hit.tx_dbm)
    print(f"           same FIX at max power ({eagle.max_dbm:+d} dBm) would arrive at SNR {snr_max:+.1f} dB: "
          f"{'SEEN' if snr_max >= scanner.threshold_db else 'not seen'}")
    out["scanner_saw_fix"] = fix_hit.detected
    out["scanner_saw_fix_at_max"] = snr_max >= scanner.threshold_db
    out["eagle_bursts"] = sum(1 for rec in ch.log if rec.src == "eagle")

    if args.plot:
        dets_plot = [det] + ([det_b] if det_b is not None else [])
        perches = [("A", perch_a)] + ([("B", perch_b)] if perch_b is not None else [])
        p = plot_floor(args.plot, perches, src, dets_plot, fused)
        if p:
            print(f"[plot]   saved {Path(p).resolve()}")
    summary = (f"RESULT bearing err {out['bearing_err_deg']:+.2f} deg | el err {out['el_err_deg']:+.2f} deg | "
               f"range err {out.get('range_err_m', float('nan')):+.2f} m | position err {out.get('pos_err_m', float('nan')):.2f} m")
    if "fused_err_m" in out:
        summary += f" | fused err {out['fused_err_m']:.2f} m"
    summary += f" | FIX {out['fix_bytes']} B @ {out['fix_dbm']:+d} dBm, airtime {out['fix_airtime_ms']:.1f} ms"
    summary += f" | scanner saw FIX: {'yes' if out['scanner_saw_fix'] else 'no'}"
    print(summary)
    out["ok"] = bool(out.get("nest_ok"))
    return out


if __name__ == "__main__":
    res = main()
    sys.exit(0 if res.get("ok") else 1)
