"""Print every CoT event that reaches this computer: checks the network path to ATAK without a phone.

Run it on a second laptop on the same Wi-Fi as the Unreal laptop (or on the same laptop).

    python tools/cot_listen.py                      # ATAK SA multicast 239.2.3.1:6969
    python tools/cot_listen.py --unicast --port 4242
    python tools/cot_listen.py --iface 192.168.1.40 # join the group on a specific adapter
"""
import argparse
import datetime
import socket
import struct
import sys
import xml.etree.ElementTree as ET


def describe(data):
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return "not CoT (%d bytes)" % len(data)
    pt = root.find("point")
    contact = root.find("detail/contact")
    remarks = root.find("detail/remarks")
    return "%-12s %-14s %-18s %10s %11s %7s m  %s" % (
        root.get("type"), root.get("uid"),
        contact.get("callsign") if contact is not None else "",
        pt.get("lat") if pt is not None else "", pt.get("lon") if pt is not None else "",
        pt.get("hae") if pt is not None else "",
        (remarks.text or "")[:60] if remarks is not None else "")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--group", default="239.2.3.1")
    ap.add_argument("--port", type=int, default=6969)
    ap.add_argument("--unicast", action="store_true", help="listen on the port without joining a group")
    ap.add_argument("--iface", default="0.0.0.0", help="local adapter IP for the multicast join")
    args = ap.parse_args(argv)

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", args.port))
    if not args.unicast:
        mreq = struct.pack("4s4s", socket.inet_aton(args.group), socket.inet_aton(args.iface))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        print("Listening for CoT on %s:%d (Ctrl+C to stop)" % (args.group, args.port))
    else:
        print("Listening for CoT on UDP port %d (Ctrl+C to stop)" % args.port)
    try:
        while True:
            data, addr = s.recvfrom(65535)
            now = datetime.datetime.now().strftime("%H:%M:%S")
            print("%s %-15s %s" % (now, addr[0], describe(data)), flush=True)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
