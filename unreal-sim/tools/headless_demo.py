"""Fly the Eagle mission without Unreal and send its CoT to ATAK.

The backup for the live demo: if Unreal will not run, this still puts EAGLE-1, the cue and IP-1
on the ATAK map at the same place in Taiwan, on the same timeline, while printing the narration.

    python tools/headless_demo.py                          # settings from config.py (multicast)
    python tools/headless_demo.py --to 192.168.1.23:4242   # unicast to one phone
    python tools/headless_demo.py --speed 6 --beacon       # faster, with the SWIR beacon answer
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "EagleSim", "Content", "Python"))

from eagle_sim.core import cot, settings  # noqa: E402
from eagle_sim.core.headless import HeadlessEngine, run  # noqa: E402


def target(text):
    host, _, port = text.rpartition(":")
    if not host or not port.isdigit():
        raise argparse.ArgumentTypeError("use host:port, e.g. 192.168.1.23:4242")
    return host, int(port)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--to", type=target, action="append", help="CoT target host:port (repeatable)")
    ap.add_argument("--speed", type=float, help="simulated seconds per real second (default from config)")
    ap.add_argument("--beacon", action="store_true", help="the person answers with a SWIR beacon")
    ap.add_argument("--iface", help="local IP of the network adapter for multicast")
    ap.add_argument("--hold", type=float, default=60.0, help="simulated seconds of relay orbit at the end")
    ap.add_argument("--fast", action="store_true", help="run as fast as possible (for checking)")
    args = ap.parse_args(argv)

    overrides = {}
    if args.to:
        overrides["COT_TARGETS"] = args.to
    if args.speed:
        overrides["TIME_SCALE"] = args.speed
    if args.beacon:
        overrides["COOPERATIVE_SIGNAL"] = True
    if args.iface is not None:
        overrides["COT_MCAST_IFACE"] = args.iface
    cfg = settings.load(**overrides)

    sender = cot.CotSender(cfg.COT_TARGETS, iface=cfg.COT_MCAST_IFACE)
    print("Eagle headless mission: CoT to %s, x%g speed" % (
        ", ".join("%s:%s" % t for t in cfg.COT_TARGETS), cfg.TIME_SCALE))
    engine = HeadlessEngine(cfg, sender=sender, echo=True)
    mission = run(cfg, engine, realtime=not args.fast, hold_s=args.hold)
    print("Done: %d CoT events sent, phase %s at t+%.0f s simulated." % (
        len(engine.sent), mission.phase, mission.t))
    if sender.errors:
        print("Network errors (last %d):" % len(sender.errors))
        for e in sender.errors:
            print("  " + e)
    sender.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
