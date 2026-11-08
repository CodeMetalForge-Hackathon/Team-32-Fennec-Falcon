"""Cursor-on-Target (CoT) events and a UDP sender for ATAK. Pure Python standard library."""
import datetime as _dt
import socket
from xml.sax.saxutils import escape, quoteattr

# CoT types used by the demo
EAGLE = "a-f-A-M-F-Q"          # friendly air, military, fixed wing, UAV
UNKNOWN_GROUND = "a-u-G"       # unconfirmed warm target


def _ts(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (t.microsecond // 1000)


def event(uid, cot_type, lat, lon, hae, ce=10.0, le=10.0, callsign=None, remarks=None,
          stale_s=120, how="m-g", now=None):
    """Build one CoT event as UTF-8 bytes."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    stale = now + _dt.timedelta(seconds=stale_s)
    detail = []
    if callsign:
        detail.append("<contact callsign=%s/>" % quoteattr(callsign))
    if remarks:
        detail.append("<remarks>%s</remarks>" % escape(remarks))
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<event version="2.0" uid=%s type=%s how=%s time="%s" start="%s" stale="%s">'
        '<point lat="%.7f" lon="%.7f" hae="%.1f" ce="%.1f" le="%.1f"/>'
        '<detail>%s</detail></event>'
    ) % (quoteattr(uid), quoteattr(cot_type), quoteattr(how), _ts(now), _ts(now), _ts(stale),
         lat, lon, hae, ce, le, "".join(detail))
    return xml.encode("utf-8")


class CotSender:
    """Sends CoT over UDP to multicast and/or unicast targets. Never raises on network errors.

    iface: local IP of the network adapter to send multicast from (e.g. the Wi-Fi adapter's
    192.168.x.y). Leave empty to let the OS choose; laptops with VPN or virtual adapters
    often pick the wrong one, which is the usual reason ATAK sees nothing.
    """

    def __init__(self, targets, ttl=1, iface=""):
        self.targets = list(targets)
        self.sent = 0
        self.errors = []
        self._sock = None
        self._ttl = ttl
        self._iface = iface

    def _socket(self):
        if self._sock is None:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, self._ttl)
            try:
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            except OSError:
                pass
            if self._iface:
                try:
                    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                                 socket.inet_aton(self._iface))
                except OSError as exc:
                    self._error("multicast interface %s: %s" % (self._iface, exc))
            self._sock = s
        return self._sock

    def _error(self, text):
        self.errors = self.errors[-19:] + [text]

    def send(self, payload):
        ok = 0
        for host, port in self.targets:
            try:
                self._socket().sendto(payload, (host, int(port)))
                ok += 1
            except OSError as exc:          # unreachable LAN, firewall, no network
                self._error("%s:%s %s" % (host, port, exc))
        self.sent += ok
        return ok

    def close(self):
        if self._sock is not None:
            self._sock.close()
            self._sock = None
