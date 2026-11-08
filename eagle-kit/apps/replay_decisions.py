#!/usr/bin/env python3
"""Replay a recording through the decision engine, with any settings, and see what it would send.

    python apps/replay_decisions.py ~/field_logs/latest
    python apps/replay_decisions.py DIR --set decide.lr.code=30 --set decide.modes.default.p_send=0.99
    python apps/replay_decisions.py DIR --config ../pi/field/field.yaml --mode strict --json replay.json

DIR is a recording from ``--record`` (eagle/record.py): ``events.jsonl`` with one ``detection``
event per detection. Recordings made by ``apps/life_node.py`` replay too, so the runs already on
the Pi can be scored by the gate. The life list (one update per sensor) and the gate run exactly as
in ``apps/eagle_runtime.py``, ticking every --tick seconds; a SEND is taken as sent (one report per
person). Use it to calibrate the likelihood ratios: record a volunteer and some strangers, then
change ``decide.lr.*`` until the gate opens for the volunteer and stays shut for the rest.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from eagle.config import deep_merge, load_config                    # noqa: E402
from eagle.decide import SEND, DecisionEngine, LedgerLifeList, planned_tx_from_config   # noqa: E402
from eagle.geo import LocalFrame                                    # noqa: E402


def dotted(pairs) -> dict:
    """['decide.lr.code=30', ...] -> nested dict with YAML-parsed values."""
    out: dict = {}
    for p in pairs or []:
        key, _, val = p.partition("=")
        node = out
        parts = key.strip().split(".")
        for k in parts[:-1]:
            node = node.setdefault(k, {})
        node[parts[-1]] = yaml.safe_load(val)
    return out


def load_batches(path: Path) -> list:
    """-> [(t, sensor, [LifeDetection])] in time order, one batch per (time, sensor)."""
    from eagle.decide import det_from_json, sensor_of
    f = path / "events.jsonl" if path.is_dir() else path
    rows, index = [], {}
    for line in f.read_text().splitlines():
        try:
            j = json.loads(line)
        except ValueError:
            continue                                   # a line cut by a power loss
        if j.get("event") != "detection":
            continue
        d = det_from_json(j)
        key = (round(float(d.t), 3), j.get("sensor") or sensor_of(d))
        if key not in index:
            index[key] = len(rows)
            rows.append((key[0], key[1], []))
        rows[index[key]][2].append(d)
    rows.sort(key=lambda r: r[0])
    return rows


def replay(batches: list, cfg: dict, *, mode: str | None = None, tick_s: float = 1.0, log=print) -> dict:
    if mode:
        cfg = deep_merge(cfg, {"decide": {"mode": mode}})
    # a replay never reads (or consumes) the live relocation flag of an Eagle running on this Pi
    cfg = deep_merge(cfg, {"decide": {"emcon": {"relocated_flag": None}}})
    perch = cfg["perch"]["enu_m"][:2]
    o = cfg["site"]["origin"]
    life = LedgerLifeList.from_config(cfg.get("life", {}).get("fusion", {}), perch_xy=perch)
    eng = DecisionEngine(cfg.get("decide"), perch_xy=perch, frame=LocalFrame(o["lat"], o["lon"], o.get("hae_m", 0.0)),
                         codes=cfg.get("codes"), path_loss=cfg["radio"].get("path_loss"),
                         log=lambda *_: None, rng=random.Random(0))
    eng.set_link(planned_tx_from_config(cfg["radio"]))
    if not batches:
        return {"subjects": {}, "sends": [], "changes": []}
    t0, t_end = batches[0][0], batches[-1][0]
    last_action, sends, changes, best = {}, [], [], {}
    i, t = 0, t0
    while t <= t_end + 1e-9:
        while i < len(batches) and batches[i][0] <= t + 1e-9:
            life.update(batches[i][2], now=batches[i][0])
            i += 1
        life.evaluate(t)
        eng.ingest(life)
        for d in eng.tick(t, life):
            best[d.subject] = max(best.get(d.subject, 0.0), d.p)
            if last_action.get(d.subject) != d.action:
                last_action[d.subject] = d.action
                changes.append({"t_s": round(t - t0, 1), "subject": d.subject, "action": d.action,
                                "p": round(d.p, 4), "reason": d.reason})
                log(f"{t - t0:8.1f} s  {d.subject:<10} {d.action:<8} P {d.p:.3f}  {d.reason}")
            if d.action == SEND:
                eng.mark_sent(d.subject, t)
                sends.append({"t_s": round(t - t0, 1), "subject": d.subject, "p": round(d.p, 4),
                              "false_alarm": d.false_alarm, "clues": [[c.key, round(c.lr, 3)] for c in d.clues]})
        t += tick_s
    return {"duration_s": round(t_end - t0, 1), "subjects": {k: round(v, 4) for k, v in best.items()},
            "sends": sends, "changes": changes}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recording", help="a --record run directory (or its events.jsonl), e.g. ~/field_logs/latest")
    ap.add_argument("--config", help="YAML overriding config/example.yaml")
    ap.add_argument("--mode", choices=["training", "default", "strict"])
    ap.add_argument("--set", action="append", metavar="KEY=VALUE", help="override a setting, e.g. decide.lr.code=30")
    ap.add_argument("--tick", type=float, default=1.0, help="decision tick between recorded batches, s")
    ap.add_argument("--json", help="also write the result here")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    cfg = deep_merge(load_config(args.config), dotted(args.set))
    res = replay(load_batches(Path(args.recording).expanduser()), cfg, mode=args.mode, tick_s=args.tick,
                 log=(lambda *_: None) if args.quiet else print)
    print(f"\n{len(res['sends'])} report(s) in {res.get('duration_s', 0)} s")
    for s in res["sends"]:
        print(f"  SEND {s['subject']} at {s['t_s']} s, P {s['p']}, false-alarm risk {s['false_alarm']}")
    for k, v in sorted(res["subjects"].items(), key=lambda kv: -kv[1]):
        print(f"  max P {v:.3f}  {k}")
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
