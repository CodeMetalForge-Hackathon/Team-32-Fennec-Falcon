#!/usr/bin/env python3
"""Nest base station: receive -> decrypt/verify -> print the fix -> CoT to ATAK.

    python apps/nest_station.py --config config/local.yaml                 # radio per config
    python apps/nest_station.py --transport udp                            # bench, with eagle_node --transport udp
    python apps/nest_station.py --atak 192.168.1.50                        # unicast to one phone (UDP 4242)
    python apps/nest_station.py --tcp 10.0.0.5:8087 --no-multicast         # TAK server plain-TCP input
    python apps/nest_station.py --transport udp --show-raw                 # also print raw frame bytes
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eagle.config import deep_merge, load_config   # noqa: E402
from eagle.link import make_transport              # noqa: E402
from eagle.nest import NestStation                 # noqa: E402


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--transport", choices=["serial", "udp", "meshtastic"])
    ap.add_argument("--port", help="serial port of the Nest radio")
    ap.add_argument("--atak", metavar="HOST", help="unicast CoT to HOST (UDP 4242)")
    ap.add_argument("--atak-port", type=int, default=4242)
    ap.add_argument("--tcp", metavar="HOST:PORT", help="TAK server plain-TCP input")
    ap.add_argument("--no-multicast", action="store_true", help="disable 239.2.3.1:6969")
    ap.add_argument("--print-cot", action="store_true", help="print the full CoT XML")
    ap.add_argument("--show-raw", action="store_true",
                    help="print every received frame as hex: what any receiver on this channel gets")
    ap.add_argument("--beacon-every", type=float, help="seconds between BEACONs (0 = never)")
    args = ap.parse_args(argv)

    over: dict = {"radio": {}, "cot": {}, "nest": {}}
    if args.transport:
        over["radio"]["transport"] = args.transport
    if args.port:
        over["radio"]["serial_port"] = args.port
    if args.atak:
        over["cot"]["unicast"] = {"enabled": True, "host": args.atak, "port": args.atak_port}
    if args.tcp:
        h, p = args.tcp.rsplit(":", 1)
        over["cot"]["tcp"] = {"enabled": True, "host": h, "port": int(p)}
    if args.no_multicast:
        over["cot"]["multicast"] = {"enabled": False}
    if args.beacon_every is not None:
        over["nest"]["beacon_every_s"] = args.beacon_every
    cfg = deep_merge(load_config(args.config), over)

    station = NestStation(cfg, log=log)
    transport = make_transport(cfg["radio"], log=log, role="nest")
    r, c = cfg["radio"], cfg["cot"]
    outs = [f"{k} {v.get('host', v.get('group'))}:{v.get('port')}" for k, v in c.items()
            if isinstance(v, dict) and v.get("enabled")]
    log(f"NEST    listening via {r['transport']} {r['freq_mhz']} MHz SF{r['sf']} BW{r['bw_khz']}; CoT -> "
        f"{', '.join(outs) or 'nowhere (print only)'}")
    every = float(cfg["nest"].get("beacon_every_s", 0) or 0)
    next_beacon = time.time() if every else float("inf")
    try:
        while True:
            if time.time() >= next_beacon:
                station.beacon(transport, int(cfg["nest"].get("beacon_dbm", 10)))
                next_beacon = time.time() + every
            rx = transport.recv(timeout=0.5)
            if rx is None:
                continue
            if args.show_raw:
                log(f"RAW     {len(rx.payload)} B, rssi {rx.rssi:.0f} dBm: {rx.payload.hex(' ')}")
            res = station.handle(rx)
            if res and args.print_cot:          # FIX -> one CoT, LIFE -> one CoT per person
                for xml in (res[1] if isinstance(res[1], list) else [res[1]]):
                    print(xml.decode(), flush=True)
    except KeyboardInterrupt:
        log("bye")
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
