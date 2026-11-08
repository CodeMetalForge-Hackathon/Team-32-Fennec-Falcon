"""Cursor-on-Target (CoT) events for ATAK / WinTAK / iTAK, plus senders.

Event skeleton (CoT 2.0)::

    <event version="2.0" uid=".." type="a-u-G" how="m-f" time=".." start=".." stale="..">
      <point lat=".." lon=".." hae=".." ce=".." le=".."/>
      <detail><contact callsign=".."/><remarks>..</remarks></detail>
    </event>

* ``type``: ``a-u-G`` (atom, unknown affiliation, ground) for an acoustic detection that is
  not yet confirmed; ``a-f-G-U-C`` (friendly ground unit) once a tag REPLY authenticated the
  person. Change them in config ``cot.unconfirmed_type`` / ``cot.authenticated_type``.
* ``ce`` / ``le``: 1-sigma horizontal / vertical error in metres; 9999999 means unknown.
* ``how``: hint of how the point was made (``m-f`` machine/fused by default, ``m-s`` for
  simulated data); configurable.

Senders (defaults as used by ATAK/PyTAK/FreeTAKServer, see README Sources):
UDP unicast to a device's network input (port 4242), UDP multicast "Mesh SA"
239.2.3.1:6969, plain TCP to a TAK server streaming input (FreeTAKServer: 8087).
"""
from __future__ import annotations

import datetime as _dt
import socket
import struct
from xml.etree import ElementTree as ET

UNCONFIRMED_TYPE = "a-u-G"
AUTHENTICATED_TYPE = "a-f-G-U-C"
UNKNOWN = 9999999.0

DEFAULT_UNICAST_PORT = 4242
DEFAULT_MULTICAST = ("239.2.3.1", 6969)
DEFAULT_TCP_PORT = 8087


def cot_time(t: _dt.datetime | None = None) -> str:
    t = t or _dt.datetime.now(_dt.timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=_dt.timezone.utc)
    t = t.astimezone(_dt.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def build_cot(uid: str, lat: float, lon: float, *, hae: float = UNKNOWN, ce: float = UNKNOWN,
              le: float = UNKNOWN, cot_type: str = UNCONFIRMED_TYPE, how: str = "m-f",
              stale_s: float = 300.0, callsign: str | None = None, remarks: str | None = None,
              now: _dt.datetime | None = None, extra: dict | None = None) -> bytes:
    """Return a CoT event as UTF-8 XML bytes (with XML declaration)."""
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        raise ValueError("lat/lon out of range")
    now = now or _dt.datetime.now(_dt.timezone.utc)
    ev = ET.Element("event", {
        "version": "2.0", "uid": uid, "type": cot_type, "how": how,
        "time": cot_time(now), "start": cot_time(now),
        "stale": cot_time(now + _dt.timedelta(seconds=stale_s)),
    })
    ET.SubElement(ev, "point", {
        "lat": f"{lat:.7f}", "lon": f"{lon:.7f}", "hae": f"{hae:.1f}",
        "ce": f"{ce:.1f}", "le": f"{le:.1f}",
    })
    detail = ET.SubElement(ev, "detail")
    ET.SubElement(detail, "contact", {"callsign": callsign or uid})
    if remarks:
        ET.SubElement(detail, "remarks").text = remarks
    for tag, attrs in (extra or {}).items():
        ET.SubElement(detail, tag, {k: str(v) for k, v in attrs.items()})
    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + ET.tostring(ev, encoding="utf-8")


def parse_cot(xml: bytes) -> dict:
    """Minimal parser used by tests / the Nest log: returns the key attributes."""
    ev = ET.fromstring(xml)
    pt = ev.find("point")
    contact = ev.find("detail/contact")
    remarks = ev.find("detail/remarks")
    return {**ev.attrib, **{f"point_{k}": v for k, v in (pt.attrib if pt is not None else {}).items()},
            "callsign": contact.get("callsign") if contact is not None else None,
            "remarks": remarks.text if remarks is not None else None}


# ---- senders --------------------------------------------------------------------------------
class UdpUnicastSender:
    """Send CoT datagrams straight to one ATAK device (Settings > Network > inputs, UDP 4242)."""

    def __init__(self, host: str, port: int = DEFAULT_UNICAST_PORT):
        self.addr = (host, int(port))
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, xml: bytes) -> None:
        self.sock.sendto(xml, self.addr)

    def close(self) -> None:
        self.sock.close()


class UdpMulticastSender:
    """ATAK mesh SA multicast (default 239.2.3.1:6969). ``ttl`` 1 keeps it on the local LAN."""

    def __init__(self, group: str = DEFAULT_MULTICAST[0], port: int = DEFAULT_MULTICAST[1],
                 ttl: int = 1, interface: str | None = None):
        self.addr = (group, int(port))
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("b", int(ttl)))
        if interface:  # local IPv4 address of the NIC to send from
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface))

    def send(self, xml: bytes) -> None:
        self.sock.sendto(xml, self.addr)

    def close(self) -> None:
        self.sock.close()


class TcpSender:
    """Plain-TCP streaming to a TAK server input (no TLS / client certs). Reconnects on error."""

    def __init__(self, host: str, port: int = DEFAULT_TCP_PORT, timeout: float = 3.0):
        self.addr, self.timeout = (host, int(port)), timeout
        self.sock: socket.socket | None = None

    def _connect(self) -> None:
        self.sock = socket.create_connection(self.addr, timeout=self.timeout)

    def send(self, xml: bytes) -> None:
        for attempt in (1, 2):
            try:
                if self.sock is None:
                    self._connect()
                self.sock.sendall(xml)
                return
            except OSError:
                self.close()
                if attempt == 2:
                    raise

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None


def senders_from_config(cot_cfg: dict) -> list:
    out = []
    u = cot_cfg.get("unicast", {})
    if u.get("enabled"):
        out.append(UdpUnicastSender(u["host"], u.get("port", DEFAULT_UNICAST_PORT)))
    m = cot_cfg.get("multicast", {})
    if m.get("enabled"):
        out.append(UdpMulticastSender(m.get("group", DEFAULT_MULTICAST[0]), m.get("port", DEFAULT_MULTICAST[1]),
                                      m.get("ttl", 1), m.get("interface")))
    t = cot_cfg.get("tcp", {})
    if t.get("enabled"):
        out.append(TcpSender(t["host"], t.get("port", DEFAULT_TCP_PORT)))
    return out
