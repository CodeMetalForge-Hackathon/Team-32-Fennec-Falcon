#!/usr/bin/env python3
"""Signs-of-life simulation - no hardware needed.

A perch 3 m up watches a room with a thermal camera, a mic array, radar pellets and a BLE
scanner. Default story: P1 is conscious (whistles the briefed code, then talks; carries tag 7
set to INJURED), P2 is unconscious (silent, warm, breathing 10/min, a radar pellet 1.3 m
away), P3 is a warm decoy (jacket with hand warmers, a pellet nearby), P4 is an allowlisted
team radio with nobody there, and six bystanders' phones are only counted. Every detector runs
for real; fusion builds the life list; the list goes to the Nest as encrypted LIFE frames;
the Nest makes one ATAK marker per person.

    python apps/life_demo.py                       # the story above; writes life_floor.png
    python apps/life_demo.py --people 10 --seed 3  # random 3-12 m placements, accuracy stats
    python apps/life_demo.py --atak 192.168.1.50   # also push the markers to an ATAK phone
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eagle import protocol as P                                    # noqa: E402
from eagle.config import deep_merge, load_config, load_keys        # noqa: E402
from eagle.cot import UdpUnicastSender, parse_cot                  # noqa: E402
from eagle.geo import LocalFrame                                   # noqa: E402
from eagle.lifesim import EXPECTED, default_scenario, match_truth, random_scenario, run_scenario  # noqa: E402
from eagle.link import MockChannel, PathLossModel                  # noqa: E402
from eagle.nest import NestStation                                 # noqa: E402
from eagle.node import EagleRadio                                  # noqa: E402

STATUS_COLORS = {P.LS_RESPONSIVE: "#1e8449", P.LS_BREATHING: "#d35400", P.LS_MOVING: "#2e86c1",
                 P.LS_WARM_ONLY: "#7f8c8d", P.LS_RF_ONLY: "#8e44ad", P.LS_UNKNOWN: "#34495e"}


def plot_life(path, sc, res, now):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Circle, Ellipse
    except ImportError:
        print("[plot]    matplotlib not installed - skipping PNG")
        return None
    from eagle.locate import error_ellipse
    fig, ax = plt.subplots(figsize=(7.5, 7))
    cam = res.camera
    for u in (-0.5, cam.width - 0.5):                       # camera field of view on the floor
        g = cam.pixel_to_ground([u, u], [-0.5 + 1e-3, cam.height - 0.5])
        ax.plot([sc.perch[0], g[0, 0]], [sc.perch[1], g[0, 1]], ":", color="#aaaaaa", lw=0.8)
    ax.plot(*sc.perch[:2], "^", ms=12, color="k", label=f"perch (h = {sc.perch[2]:.0f} m)")
    for p in sc.people:
        mk = {"conscious": "*", "unconscious": "*", "decoy": "X", "device": "s"}[p.kind]
        ax.plot(*p.xy, mk, ms=13, mfc="none", mec="k", mew=1.2)
        ax.annotate(f"{p.name} ({p.kind})", p.xy, textcoords="offset points", xytext=(6, 6), fontsize=8)
        if p.pellet_xy is not None:
            ax.plot(*p.pellet_xy, "d", ms=6, color="#b9770e")
    seen = set()
    for tr in res.life.tracks:
        col = STATUS_COLORS[tr.status]
        lab = tr.status_name if tr.status not in seen else None
        seen.add(tr.status)
        if tr.rf_only:
            ax.add_patch(Circle(tuple(sc.perch[:2]), tr.rf_range, fill=False, ls="--", color=col, lw=1.2, label=lab))
            continue
        x = tr.x
        ax.plot(*x, "o", ms=8, color=col, label=lab)
        a, b, ang = error_ellipse(tr.P)
        ax.add_patch(Ellipse(tuple(x), 2 * a, 2 * b, angle=ang, fill=False, color=col, lw=1.0))
        ax.annotate(f"#{tr.id}", x, textcoords="offset points", xytext=(-14, -12), fontsize=8, color=col)
    ax.plot([], [], "d", color="#b9770e", label="radar pellet")
    ax.plot([], [], "*", mfc="none", mec="k", label="truth (X = decoy, square = radio)")
    ax.set_xlabel("east (m)")
    ax.set_ylabel("north (m)")
    ax.set_aspect("equal")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7, loc="lower right")
    ax.set_title("Eagle life list - floor view (colour = status, ellipse = 95 %)")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="YAML overriding config/example.yaml")
    ap.add_argument("--people", type=int, default=0, help="random scenario with N bodies (3-12 m)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--preset", help="mic array preset (default: audio.preset)")
    ap.add_argument("--snr", type=float, default=20.0, help="in-band audio SNR, dB")
    ap.add_argument("--nest", type=float, nargs=2, default=(600.0, 500.0), metavar=("E", "N"))
    ap.add_argument("--exponent", type=float, default=3.0, help="path-loss exponent of the mock air")
    ap.add_argument("--max-len", type=int, default=P.LIFE_MAX_LEN, help="LIFE frame size limit (bytes)")
    ap.add_argument("--atak", metavar="HOST", help="also send the CoT markers to HOST:4242/udp")
    ap.add_argument("--plot", default="life_floor.png", help="PNG path ('' to skip)")
    ap.add_argument("--quiet", action="store_true", help="less per-sensor output")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    keys = load_keys(cfg)
    sc = random_scenario(args.people, args.seed) if args.people else default_scenario(args.seed)
    out: dict = {"people": len(sc.people)}
    say = (lambda m: None) if args.quiet else print
    print(f"=== Eagle signs-of-life simulation: {len(sc.people)} object(s), perch h = {sc.perch[2]:.1f} m ===")
    session = P.EagleSession(cfg["eagle"]["id"], keys["tag_hmac"], keys["fix_aes"],
                             counter=P.CounterStore(None, start=1000))
    t0 = time.time()
    res = run_scenario(sc, cfg, keys=keys, session=session, log=say, preset=args.preset, snr_db=args.snr)
    now = res.t_end
    print(f"[fusion]  {len(res.detections)} detections -> {len(res.life.tracks)} track(s) "
          f"({time.time() - t0:.1f} s compute), life list at t = {now:.0f} s:")
    print("          " + res.life.summary(now).replace("\n", "\n          "))

    # ---- truth vs estimate ------------------------------------------------------------------
    rows = match_truth(sc, res.life)
    print("[truth]   object  kind         expected    got         track  pos err")
    errs, ok_status, persons_found = [], 0, 0
    out["status"], out["pos_err_m"] = {}, {}
    for p, tr, d in rows:
        exp = P.LIFE_STATUS_NAMES[EXPECTED[p.kind]]
        got = tr.status_name if tr is not None else "MISSED"
        out["status"][p.name] = got
        if tr is not None and np.isfinite(d):
            out["pos_err_m"][p.name] = d
        if p.is_person and tr is not None:
            persons_found += 1
            errs.append(d)
        ok_status += got == exp
        print(f"          {p.name:<7} {p.kind:<12} {exp:<11} {got:<11} {('#' + str(tr.id)) if tr else '-':>5}  "
              + (f"{d:5.2f} m" if np.isfinite(d) else "  -"))
    n_persons = sum(p.is_person for p in sc.people)
    matched_ids = {tr.id for _, tr, _ in rows if tr is not None}
    false_tracks = [t for t in res.life.tracks if t.id not in matched_ids]
    alive = [t for t in res.life.tracks if t.status in (P.LS_RESPONSIVE, P.LS_BREATHING, P.LS_MOVING)]
    out.update(status_correct=ok_status, objects=len(rows), persons=n_persons, persons_found=persons_found,
               alive_reported=len(alive), false_tracks=len(false_tracks))
    if errs:
        e = np.array(errs)
        out.update(err_mean_m=float(e.mean()), err_median_m=float(np.median(e)), err_p95_m=float(np.percentile(e, 95)),
                   err_max_m=float(e.max()))
    print(f"[score]   persons found {persons_found}/{n_persons}; alive (RESPONSIVE/BREATHING/MOVING) reported "
          f"{len(alive)} vs {n_persons} true; status correct {ok_status}/{len(rows)}; "
          f"false tracks {len(false_tracks)}")
    if errs:
        print(f"[score]   person position error: mean {out['err_mean_m']:.2f} m, median {out['err_median_m']:.2f} m, "
              f"95th pct {out['err_p95_m']:.2f} m, max {out['err_max_m']:.2f} m")

    # ---- LIFE burst(s) -> Nest -> CoT ---------------------------------------------------------
    o = cfg["site"]["origin"]
    frame0 = LocalFrame(o["lat"], o["lon"], o.get("hae_m", 0.0))
    plat, plon, _ = frame0.to_geodetic(*sc.perch[:2], 0.0)
    floor_hae = o.get("hae_m", 0.0) + float(cfg["site"].get("floor_z", 0.0))
    t_unix = int(time.time())
    persons = res.life.life_persons(now)
    frames = session.encrypt_life(P.LifeHead(t_unix, plat, plon, floor_hae), persons, args.max_len)
    r = cfg["radio"]
    ch = MockChannel(PathLossModel(exponent=args.exponent), sf=int(r["sf"]), bw_khz=float(r["bw_khz"]),
                     cr=int(r["cr"]), noise_figure_db=float(r.get("noise_figure_db", 6.0)), seed=args.seed)
    nest_xyz = np.array([args.nest[0], args.nest[1], 2.0])
    eagle_t, nest_t = ch.add_node("eagle", sc.perch), ch.add_node("nest", nest_xyz)
    sim_cfg = deep_merge(cfg, {"radio": {"path_loss": {"exponent": args.exponent},
                                         "nest_link": {"distance_m": float(np.linalg.norm(nest_xyz - sc.perch))}}})
    eagle = EagleRadio(sim_cfg, eagle_t, session, log=lambda m: print(f"[eagle]   {m}"), clock=lambda: float(t_unix))
    nest = NestStation(cfg, senders=[], log=lambda m: print(f"[nest]    {m}"), sim=True)
    nest.beacon(nest_t, int(cfg["nest"].get("beacon_dbm", 10)))
    eagle.drain()
    sent = eagle.send_life(frames)
    out["life_frames"] = len(frames)
    out["life_bytes"] = [len(f) for f in frames]
    out["life_airtime_ms"] = float(sum(s.airtime_s for s in sent) * 1e3)
    print(f"[life]    {len(persons)} record(s) in {len(frames)} LIFE frame(s) of {out['life_bytes']} B "
          f"(limit {args.max_len} B): total airtime {out['life_airtime_ms']:.1f} ms at "
          f"SF{r['sf']}/BW{r['bw_khz']} @ {sent[0].tx_dbm:+d} dBm")
    cots = []
    while True:
        rx = nest_t.recv()
        if rx is None:
            break
        res_n = nest.handle(rx, now=t_unix)
        if res_n is not None and isinstance(res_n[0], P.LifeReport):
            cots += res_n[1]
    out["nest_persons"] = len(cots)
    for xml in cots:
        c = parse_cot(xml)
        print(f"[cot]     {c['type']:<10} uid={c['uid']:<13} {c['point_lat']}, {c['point_lon']} ce={c['point_ce']} m "
              f"'{c['callsign']}'")
        if args.atak:
            UdpUnicastSender(args.atak).send(xml)
    if args.atak and cots:
        print(f"[cot]     sent {len(cots)} marker(s) to {args.atak}:4242/udp")
    out["cot"] = [parse_cot(x) for x in cots]
    if args.plot:
        pth = plot_life(args.plot, sc, res, now)
        if pth:
            print(f"[plot]    saved {Path(pth).resolve()}")
    out["ok"] = bool(cots) and len(cots) == len(persons)
    if not args.people:
        want = {"P1": "RESPONSIVE", "P2": "BREATHING", "P3": "WARM_ONLY", "P4": "RF_ONLY"}
        out["story_ok"] = all(out["status"].get(k) == v for k, v in want.items())
        out["ok"] = out["ok"] and out["story_ok"]
    print(f"RESULT {'OK' if out['ok'] else 'CHECK'}: " + ", ".join(f"{k} {v}" for k, v in out["status"].items())
          + f" | LIFE {out['life_frames']} frame(s), {out['life_airtime_ms']:.1f} ms")
    return out


if __name__ == "__main__":
    res = main()
    sys.exit(0 if res.get("ok") else 1)
