"""Fennec Fix: the one report an Eagle sends home. 64 bits of meaning, sealed to 15 bytes on air.

The team's relay design packs what was found, where (to 2.5 m), how sure and when into 64 bits,
seals it with AES-128-CCM and a 4-byte tag, and sends it as one short burst: 46 ms at SF7 /
125 kHz (modelled), against 97.5 ms for the kit's 48-byte FIX. ``pack64`` / ``unpack64`` /
``seal`` / ``open_packet`` reproduce the design's reference code (fennec_fix.py) bit for bit;
``tests/test_fennec.py`` checks them against it.

Payload (big-endian 64-bit integer)::

    63-61 type    FIX 0, BEARING 1, MOVED 2, STATUS 3, COMPROMISED 4, TEST 5 (6, 7 spare)
    60-45 east    signed 2.5 m steps from the mission reference point (+-81.9 km)
    44-29 north   same
    28-25 err_k   95 % error radius = 5 m x 1.5^k (5 m to 2.2 km)
    24-22 cue     what found him: 0 briefed whistle/tap pattern, 1 other sound, 2 thermal,
                  3 radio bearing (tag / RF), 4 visual answer (camera), 5 several sensors
    21-20 conf    gate level: 0 below the gate, 1 P >= 0.95, 2 P >= 0.99, 3 P >= 0.999
    19-16 state   bit 0 moving, bit 1 pattern matched (design); bit 2 injured (briefed status),
                  bit 3 face verified (identity level 3) use the two spare bits
    15-0  t_min   minutes since mission start (45.5 days)

BEARING (no fix) keeps type, east and north in place (the EAGLE's own offset) and packs
azimuth (bits 28-20, 9 bits, 360/512 deg, compass degrees clockwise from true north),
elevation (19-13, 7 bits, -45..+45 deg in 90/128 deg steps) and t_min (12-0, 13 bits, 5.7 days).
The base crosses two of them from different Eagles.

On air: header [0b01 version | eagle id 0-63][16-bit counter] (3 B) + ciphertext (8 B) + tag (4 B).
Nonce (13 B) = mission id (4 B) + eagle id + counter + 6 zero bytes; the header is the associated
data, so it cannot be altered either. One AES-128 key per mission, loaded before launch, wiped on
capture (``wipe_key_file``). The counter is persisted per mission and Eagle and never wraps: at
65,535 reports the Eagle refuses to seal until the mission key is replaced.

Offsets are east/north in a local tangent plane around the mission reference point, computed
with eagle/geo.py at both ends. The design names UTM; what matters is that both ends convert
the same way, and the kit's frame does that without pyproj (over +-80 km the two differ, so
never mix them). The first byte (0x40-0x7F) never collides with the kit's frame types (0x01-0x05).
"""
from __future__ import annotations

import calendar
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path

TYPES = {"FIX": 0, "BEARING": 1, "MOVED": 2, "STATUS": 3, "COMPROMISED": 4, "TEST": 5}
TYPE_NAMES = {v: k for k, v in TYPES.items()}
CUES = {0: "briefed pattern", 1: "other sound", 2: "thermal", 3: "radio bearing", 4: "visual answer",
        5: "several sensors"}
CUE = {name: k for k, name in CUES.items()}
CONF_LEVELS = (0.95, 0.99, 0.999)             # conf = number of these P reaches
ST_MOVING, ST_PATTERN, ST_INJURED, ST_FACE = 0x1, 0x2, 0x4, 0x8
STATE_NAMES = {ST_MOVING: "moving", ST_PATTERN: "pattern matched", ST_INJURED: "injured", ST_FACE: "face verified"}
STEP_M = 2.5
MAX_OFFSET_M = 32767 * STEP_M                  # 81.9 km
LEN = 15
VERSION = 0x40                                 # top two bits 01


class FennecError(ValueError):
    pass


# ---------------------------------------------------------------------------------- bits
def err_k_for(err95_m: float) -> int:
    """Smallest k with 5 m x 1.5^k >= the 95 % radius (k = 15, 2.2 km, at most)."""
    r = max(float(err95_m), 0.0)
    for k in range(16):
        if 5.0 * 1.5 ** k >= r:
            return k
    return 15


def err_m_for(k: int) -> int:
    return round(5 * 1.5 ** (int(k) & 0xF))


def conf_level(p: float) -> int:
    return sum(1 for x in CONF_LEVELS if p >= x)


def _offset(v: float) -> int:
    if not -MAX_OFFSET_M <= v <= MAX_OFFSET_M:
        raise FennecError(f"offset {v:.0f} m is outside +-{MAX_OFFSET_M:.0f} m of the mission reference")
    return round(v / STEP_M) & 0xFFFF


def _s16(x: int) -> int:
    return x - 0x10000 if x & 0x8000 else x


def pack64(mtype: str, east_m: float, north_m: float, err_k: int, cue: int, conf: int, state: int,
           t_min: int) -> bytes:
    """The design's pack64 (same bits), with range checks instead of silent wrap-around."""
    if mtype == "BEARING":
        raise FennecError("use pack_bearing for BEARING")
    for name, v, top in (("err_k", err_k, 15), ("cue", cue, 7), ("conf", conf, 3), ("state", state, 15)):
        if not 0 <= int(v) <= top:
            raise FennecError(f"{name} {v} out of range 0..{top}")
    e, n = _offset(east_m), _offset(north_m)
    v = ((TYPES[mtype] << 61) | (e << 45) | (n << 29) | (int(err_k) << 25) | (int(cue) << 22) | (int(conf) << 20)
         | (int(state) << 16) | (int(t_min) & 0xFFFF))
    return v.to_bytes(8, "big")


def pack_bearing(own_east_m: float, own_north_m: float, az_deg: float, el_deg: float, t_min: int) -> bytes:
    """BEARING: the Eagle's own offset, compass azimuth (9 bits) and elevation (7 bits, +-45 deg)."""
    az = round((float(az_deg) % 360.0) / 360.0 * 512) % 512
    el = round((min(max(float(el_deg), -45.0), 45.0) + 45.0) / 90.0 * 127)
    v = ((TYPES["BEARING"] << 61) | (_offset(own_east_m) << 45) | (_offset(own_north_m) << 29) | (az << 20)
         | (el << 13) | (int(t_min) & 0x1FFF))
    return v.to_bytes(8, "big")


def unpack64(b: bytes) -> dict:
    """The design's unpack64 (same keys and values), plus the type name and the BEARING layout."""
    v = int.from_bytes(b, "big")
    t = v >> 61
    out = {"type": t, "type_name": TYPE_NAMES.get(t, f"spare{t}"), "east_m": _s16((v >> 45) & 0xFFFF) * STEP_M,
           "north_m": _s16((v >> 29) & 0xFFFF) * STEP_M}
    if t == TYPES["BEARING"]:
        out.update({"az_deg": ((v >> 20) & 0x1FF) * 360.0 / 512, "el_deg": ((v >> 13) & 0x7F) * 90.0 / 127 - 45.0,
                    "t_min": v & 0x1FFF})
    else:
        out.update({"err95_m": err_m_for((v >> 25) & 0xF), "cue": (v >> 22) & 7, "conf": (v >> 20) & 3,
                    "state": (v >> 16) & 0xF, "t_min": v & 0xFFFF})
    return out


# ---------------------------------------------------------------------------------- sealing
def _nonce(mission_id: bytes, node: int, ctr: int) -> bytes:          # 4 + 1 + 2 + 6 = 13 bytes
    return mission_id + bytes([node]) + ctr.to_bytes(2, "big") + bytes(6)


def seal(key: bytes, mission_id: bytes, node: int, ctr: int, payload8: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESCCM
    if not 0 <= node <= 63:
        raise FennecError(f"eagle id {node} must be 0..63 for the Fennec header")
    if not 0 <= ctr <= 0xFFFF:
        raise FennecError("16-bit counter exhausted: load a new mission key")
    hdr = bytes([VERSION | node]) + ctr.to_bytes(2, "big")
    return hdr + AESCCM(key, tag_length=4).encrypt(_nonce(mission_id, node, ctr), payload8, hdr)


def open_packet(key: bytes, mission_id: bytes, pkt: bytes) -> tuple:
    """-> (eagle id, counter, payload8); raises cryptography's InvalidTag if forged or corrupted."""
    from cryptography.hazmat.primitives.ciphers.aead import AESCCM
    node, ctr = pkt[0] & 0x3F, int.from_bytes(pkt[1:3], "big")
    return node, ctr, AESCCM(key, tag_length=4).decrypt(_nonce(mission_id, node, ctr), pkt[3:], pkt[:3])


def is_fennec(frame: bytes) -> bool:
    return len(frame) == LEN and (frame[0] & 0xC0) == VERSION


# ---------------------------------------------------------------------------------- mission
def _unix(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("Z", "")
    return float(calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")))


def wipe_key_file(path) -> bool:
    """Overwrite the mission key file with zeros, flush it to the card, delete it. True if it existed."""
    p = Path(os.path.expanduser(str(path)))
    if not p.exists():
        return False
    n = p.stat().st_size
    with open(p, "r+b") as f:
        f.write(b"\0" * max(n, 32))
        f.flush()
        os.fsync(f.fileno())
    p.unlink()
    return True


@dataclass
class Mission:
    """Everything both ends share: key, mission id, reference point, start time."""
    key: bytes
    mission_id: bytes
    ref_lat: float
    ref_lon: float
    start_unix: float
    key_file: str | None = None
    test_key: bool = False

    @classmethod
    def from_config(cls, cfg: dict) -> "Mission":
        f = cfg.get("fennec") or {}
        key_hex = None
        if f.get("key_file"):
            p = Path(os.path.expanduser(str(f["key_file"])))
            if p.exists():
                key_hex = p.read_text().strip()
        key_hex = key_hex or f.get("key")
        if not key_hex:
            raise FennecError("no Fennec mission key (fennec.key_file or fennec.key)")
        key = bytes.fromhex(str(key_hex))
        if len(key) != 16:
            raise FennecError("the Fennec mission key is AES-128: 32 hex digits")
        mid = bytes.fromhex(str(f.get("mission_id", "00000000")))
        if len(mid) != 4:
            raise FennecError("fennec.mission_id is 4 bytes: 8 hex digits")
        ref = f.get("mission_ref") or cfg["site"]["origin"]
        return cls(key, mid, float(ref["lat"]), float(ref["lon"]), _unix(f.get("mission_start", 0)),
                   f.get("key_file"), bool(f.get("test_key", False)))

    def frame(self):
        from .geo import LocalFrame
        return LocalFrame(self.ref_lat, self.ref_lon, 0.0)

    def t_min(self, t_unix: float, bits: int = 16) -> int:
        m = int(max(float(t_unix) - self.start_unix, 0.0) // 60)
        if m >= 1 << bits:
            raise FennecError(f"{m} min since mission start does not fit {bits} bits: set fennec.mission_start")
        return m

    def t_unix(self, t_min: int, bits: int = 16, near: float | None = None) -> float:
        """Minutes back to unix time; a 13-bit BEARING time is unwrapped to the period nearest ``near``."""
        t = self.start_unix + 60.0 * t_min
        if bits < 16 and near is not None:
            span = 60.0 * (1 << bits)
            t += span * round((near - t) / span)
        return t


class CounterFile:
    """16-bit counter per mission and Eagle, persisted with an atomic replace before each use."""

    def __init__(self, path: str | None):
        self.path = Path(os.path.expanduser(path)) if path else None
        self.value = -1
        if self.path and self.path.exists():
            self.value = int(json.loads(self.path.read_text())["counter"])

    def next(self) -> int:
        if self.value + 1 > 0xFFFF:
            raise FennecError("16-bit counter exhausted for this mission key: load a new key")
        self.value += 1
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"counter": self.value}))
            os.replace(tmp, self.path)
        return self.value


@dataclass
class Report:
    """What the decision layer chose to say, before packing."""
    mtype: str                                   # FIX | MOVED | TEST | STATUS | COMPROMISED | BEARING
    t_unix: float
    lat: float | None = None                     # FIX-like: where he is
    lon: float | None = None
    err95_m: float = 0.0
    cue: int = 0
    conf: int = 0
    state: int = 0
    own_lat: float | None = None                 # BEARING / STATUS / COMPROMISED: where the Eagle is
    own_lon: float | None = None
    az_deg: float | None = None                  # BEARING: compass azimuth, elevation
    el_deg: float = 0.0


class EagleCodec:
    """Eagle side: Report -> 15 sealed bytes, one fresh counter each."""

    def __init__(self, mission: Mission, eagle_id: int, counter_file: str | None = None):
        if not 0 <= int(eagle_id) <= 63:
            raise FennecError(f"eagle.id {eagle_id} must be 0..63 for Fennec reports")
        self.m, self.node = mission, int(eagle_id)
        self.frame = mission.frame()
        self.counter = CounterFile(counter_file)

    def payload(self, r: Report) -> bytes:
        if r.mtype == "BEARING":
            e, n, _ = self.frame.to_enu(r.own_lat, r.own_lon, None)
            return pack_bearing(e, n, r.az_deg, r.el_deg, self.m.t_min(r.t_unix, 13) & 0x1FFF)
        lat, lon = (r.lat, r.lon) if r.lat is not None else (r.own_lat, r.own_lon)
        e, n, _ = self.frame.to_enu(lat, lon, None)
        return pack64(r.mtype, e, n, err_k_for(r.err95_m), r.cue, r.conf, r.state, self.m.t_min(r.t_unix))

    def seal(self, r: Report) -> bytes:
        p = self.payload(r)                          # range errors before a counter is spent
        return seal(self.m.key, self.m.mission_id, self.node, self.counter.next(), p)


class NestCodec:
    """Base side: check the tag, drop replays (per Eagle), decode to positions."""

    def __init__(self, mission: Mission, window: int = 64):
        self.m, self.window = mission, int(window)
        self.frame = mission.frame()
        self.seen: dict = {}                         # eagle id -> set of counters within the window

    def open(self, pkt: bytes, now: float | None = None) -> dict:
        from cryptography.exceptions import InvalidTag
        if not is_fennec(pkt):
            raise FennecError("not a Fennec packet")
        try:
            node, ctr, p8 = open_packet(self.m.key, self.m.mission_id, pkt)
        except InvalidTag:
            raise FennecError("tag check failed: forged, corrupted, or another mission's key")
        seen = self.seen.setdefault(node, set())
        top = max(seen) if seen else -1
        if ctr in seen or ctr <= top - self.window:
            raise FennecError(f"replay or stale counter {ctr} from Eagle {node}")
        seen.add(ctr)
        if len(seen) > self.window:
            seen.difference_update({c for c in seen if c <= max(seen) - self.window})
        d = unpack64(p8)
        d.update({"eagle_id": node, "counter": ctr})
        now = time.time() if now is None else now
        if d["type"] == TYPES["BEARING"]:
            d["t_unix"] = self.m.t_unix(d["t_min"], 13, near=now)
            d["own_lat"], d["own_lon"], _ = self.frame.to_geodetic(d["east_m"], d["north_m"], 0.0)
        else:
            d["t_unix"] = self.m.t_unix(d["t_min"])
            d["lat"], d["lon"], _ = self.frame.to_geodetic(d["east_m"], d["north_m"], 0.0)
        return d


def describe(d: dict) -> str:
    """One line for the Nest log."""
    when = time.strftime("%H:%MZ", time.gmtime(d["t_unix"]))
    if d["type"] == TYPES["BEARING"]:
        return (f"BEARING eagle {d['eagle_id']} #{d['counter']}: from {d['own_lat']:.6f}, {d['own_lon']:.6f} "
                f"az {d['az_deg']:.1f} deg el {d['el_deg']:+.1f} deg at {when}")
    st = ", ".join(n for b, n in STATE_NAMES.items() if d["state"] & b) or "-"
    conf = ["below gate", "P>=0.95", "P>=0.99", "P>=0.999"][d["conf"]]
    return (f"{d['type_name']} eagle {d['eagle_id']} #{d['counter']}: {d['lat']:.6f}, {d['lon']:.6f} "
            f"err95 {d['err95_m']} m, cue {CUES.get(d['cue'], d['cue'])}, {conf}, state [{st}], at {when}")


def crossing(b1: dict, b2: dict, frame) -> tuple | None:
    """Where two BEARINGs from different Eagles cross: (lat, lon, angle between them deg) or None."""
    import numpy as np
    p1 = np.array([b1["east_m"], b1["north_m"]])
    p2 = np.array([b2["east_m"], b2["north_m"]])
    u1 = np.array([math.sin(math.radians(b1["az_deg"])), math.cos(math.radians(b1["az_deg"]))])
    u2 = np.array([math.sin(math.radians(b2["az_deg"])), math.cos(math.radians(b2["az_deg"]))])
    A = np.column_stack([u1, -u2])
    if abs(np.linalg.det(A)) < 1e-6:
        return None
    s, r = np.linalg.solve(A, p2 - p1)
    if s <= 0 or r <= 0:
        return None                                  # the rays point away from each other
    x = p1 + s * u1
    ang = math.degrees(math.acos(max(-1.0, min(1.0, float(u1 @ u2)))))
    lat, lon, _ = frame.to_geodetic(float(x[0]), float(x[1]), 0.0)
    return lat, lon, ang
