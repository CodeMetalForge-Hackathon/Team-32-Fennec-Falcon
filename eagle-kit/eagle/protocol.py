"""Over-the-air messages: byte layouts, HMAC, AES-GCM, replay windows and power control.

This docstring is the ONE canonical description of the byte layouts. The firmware headers
(firmware/eagle_radio/eagle_proto.h, firmware/tag/eagle_proto.h) mirror the constants and
tests/test_firmware_layout.py fails if they drift. All integers are big-endian.

CHALLENGE  Eagle -> tag, 20 bytes, authenticated with K_tag (shared by Eagles and tags)
  off len field
  0   1   type = 0x01
  1   1   eagle_id
  2   8   nonce = counter (uint32, monotonic per Eagle) || 4 random bytes
  10  1   reply_window in units of 10 ms (tag answers 50 ms .. min(500 ms, window))
  11  1   eagle_tx_dbm (int8) - power this challenge was sent with
  12  8   mac = HMAC-SHA256(K_tag, bytes[0:12])[:8]

REPLY  tag -> Eagle, 21 bytes, authenticated with K_tag
  0   1   type = 0x02
  1   1   tag_id
  2   8   nonce (echoed)
  10  1   status_wire = status XOR covert_mask   (random-looking to everyone but the Nest)
  11  1   battery percent (0..100, 255 = unknown)
  12  1   rssi_heard (int8, dBm of the challenge as received by the tag)
  13  8   mac = HMAC-SHA256(K_tag, bytes[0:13])[:8]
  covert_mask = HMAC-SHA256(K_covert, b"EGCV" || tag_id || eagle_id || counter_be32)[0]
  K_covert lives only on tags and the Nest, so a duress answer verifies exactly like a
  normal one at the Eagle and on the air; only the Nest can see the DURESS bit.

FIX  Eagle -> Nest, 48 bytes, AES-256-GCM with K_fix (shared by Eagles and the Nest)
  0   1   type = 0x03            \
  1   1   eagle_id                |  header = GCM associated data (authenticated, clear)
  2   4   salt (random per boot)  |
  6   4   counter (uint32)       /   same monotonic counter as the challenge nonces
  10  22  ciphertext of: lat_e7 int32 | lon_e7 int32 | alt_m int16 | ce_m uint16 |
          class uint8 | confidence uint8 (0-100) | unix_time uint32 | status uint8 |
          tag_id uint8 (0 = none) | chal_ctr16 uint16 (low 16 bits of the answered challenge)
  32  16  GCM tag
  GCM nonce (12 bytes) = salt || counter || eagle_id || 0x03 || 0x00 0x00
  class byte: bits 0-3 how it was found (CLS_UNKNOWN, CLS_WHISTLE, CLS_TAP, CLS_TAG_RSSI),
  bit 4 CLS_AUTH (authenticated tag REPLY), bits 5-6 missing-person identity match level
  (ID_NONE 0, ID_APPEARANCE 1 = appearance candidate, ID_STRONG 2 = strong appearance,
  ID_FACE 3 = face verified; see eagle/identity.py), bit 7 CLS_SIM (simulated traffic).
  The identity level only adds information: a FIX is sent the same way at every level.

BEACON  Nest -> Eagles, 15 bytes (optional; lets Eagles measure path loss to the Nest)
  0   1   type = 0x04
  1   1   nest_id
  2   4   counter (uint32)
  6   1   nest_tx_dbm (int8)
  7   8   mac = HMAC-SHA256(K_beacon, bytes[0:7])[:8], K_beacon = HMAC-SHA256(K_fix, b"EAGLE-BEACON-v1")

LIFE  Eagle -> Nest, 44 + 10 per person (+4 per tagged person) bytes, at most 128 per frame,
      AES-256-GCM with K_fix exactly like FIX (host-only message: the radios just carry it)
  0   1   type = 0x05            \
  1   1   eagle_id                |  header = GCM associated data (authenticated, clear)
  2   4   salt (random per boot)  |
  6   4   counter (uint32)       /   same monotonic counter as CHALLENGE / FIX
  10  n   ciphertext of the list header (18 B):
          unix_time uint32 | perch lat_e7 int32 | perch lon_e7 int32 | floor_hae_m int16 |
          list_seq uint16 (low 16 bits of the counter of the burst's first frame) |
          chunk uint8 (index << 4 | count, a list larger than one frame is split) |
          n_persons uint8 (in this frame)
        followed by n_persons records of 10 B:
          track_id uint8 | east_dm int16 | north_dm int16 (offsets from the perch, decimetres) |
          ce_dm uint8 (1-sigma, saturating at 25.5 m) | status uint8 | breath_per_min uint8 (0 =
          unknown) | heart_bpm uint8 (0 = unknown) | evidence uint8
        and, only when evidence has EV_TAG, 4 more bytes:
          tag_id uint8 | tag status_wire uint8 (still covert-masked) | chal_ctr16 uint16
  -16 16  GCM tag
  GCM nonce (12 bytes) = salt || counter || eagle_id || 0x05 || 0x00 0x00
  status byte: bits 0-2 life status (LS_*), bit 3 INJURED, bit 4 DURESS (set only by the Nest
  after unmasking a tag), bits 5-7 life score in eighths (round(score * 7)).
  evidence byte: 0x01 heat, 0x02 sound, 0x04 voice, 0x08 breath, 0x10 motion, 0x20 rf,
  0x40 tag (authenticated REPLY), 0x80 radar checked: no vital signs.

Power control (reciprocity): estimated path loss = TX power announced in the frame - RSSI
measured on it; reply power = clamp(ceil(sensitivity + margin + path_loss), min, max).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import struct
import time
from dataclasses import dataclass
from pathlib import Path

from .airtime import sensitivity_dbm

# ---- constants (mirrored in firmware/*/eagle_proto.h) --------------------------------
MSG_CHALLENGE = 0x01
MSG_REPLY = 0x02
MSG_FIX = 0x03
MSG_BEACON = 0x04
MSG_LIFE = 0x05          # host-only (Eagle computer -> Nest computer); not in the firmware headers

MAC_LEN = 8
NONCE_LEN = 8
CHAL_FMT = ">BB8sBb"           # type, eagle_id, nonce, window_10ms, tx_dbm
CHAL_BODY_LEN = struct.calcsize(CHAL_FMT)            # 12
CHAL_LEN = CHAL_BODY_LEN + MAC_LEN                   # 20
REPLY_FMT = ">BB8sBBb"         # type, tag_id, nonce, status_wire, battery, rssi_heard
REPLY_BODY_LEN = struct.calcsize(REPLY_FMT)          # 13
REPLY_LEN = REPLY_BODY_LEN + MAC_LEN                 # 21
FIX_HDR_FMT = ">BBII"          # type, eagle_id, salt, counter
FIX_HDR_LEN = struct.calcsize(FIX_HDR_FMT)           # 10
FIX_BODY_FMT = ">iihHBBIBBH"   # lat, lon, alt, ce, class, conf, time, status, tag_id, chal16
FIX_BODY_LEN = struct.calcsize(FIX_BODY_FMT)         # 22
GCM_TAG_LEN = 16
FIX_LEN = FIX_HDR_LEN + FIX_BODY_LEN + GCM_TAG_LEN   # 48
BEACON_FMT = ">BBIb"           # type, nest_id, counter, tx_dbm
BEACON_BODY_LEN = struct.calcsize(BEACON_FMT)        # 7
BEACON_LEN = BEACON_BODY_LEN + MAC_LEN               # 15

LIFE_HEAD_FMT = ">IiihHBB"     # time, perch lat, perch lon, floor hae, list_seq, chunk, n_persons
LIFE_HEAD_LEN = struct.calcsize(LIFE_HEAD_FMT)       # 18
LIFE_PERSON_FMT = ">BhhBBBBB"  # track, east_dm, north_dm, ce_dm, status, breath, heart, evidence
LIFE_PERSON_LEN = struct.calcsize(LIFE_PERSON_FMT)   # 10
LIFE_TAG_FMT = ">BBH"          # tag_id, status_wire, chal_ctr16
LIFE_TAG_LEN = struct.calcsize(LIFE_TAG_FMT)         # 4
LIFE_MIN_LEN = FIX_HDR_LEN + LIFE_HEAD_LEN + GCM_TAG_LEN   # 44 (empty list)
LIFE_MAX_LEN = 128             # ~108 ms at SF7/BW250

# LIFE status codes (low 3 bits of the person status byte)
LS_UNKNOWN, LS_RESPONSIVE, LS_BREATHING, LS_MOVING, LS_WARM_ONLY, LS_RF_ONLY = range(6)
LIFE_STATUS_NAMES = {LS_UNKNOWN: "UNKNOWN", LS_RESPONSIVE: "RESPONSIVE", LS_BREATHING: "BREATHING",
                     LS_MOVING: "MOVING", LS_WARM_ONLY: "WARM_ONLY", LS_RF_ONLY: "RF_ONLY"}
LF_INJURED = 0x08
LF_DURESS = 0x10
# LIFE evidence bits
EV_HEAT, EV_SOUND, EV_VOICE, EV_BREATH = 0x01, 0x02, 0x04, 0x08
EV_MOTION, EV_RF, EV_TAG, EV_NOVITALS = 0x10, 0x20, 0x40, 0x80
EVIDENCE_NAMES = {EV_HEAT: "heat", EV_SOUND: "sound", EV_VOICE: "voice", EV_BREATH: "breath",
                  EV_MOTION: "motion", EV_RF: "rf", EV_TAG: "tag", EV_NOVITALS: "no-vitals"}

COVERT_LABEL = b"EGCV"
BEACON_KEY_LABEL = b"EAGLE-BEACON-v1"

# status bits (REPLY / FIX)
ST_INJURED = 0x01
ST_MOBILE = 0x02
ST_WATER = 0x04
ST_FOOD = 0x08
ST_THREAT = 0x10
ST_DURESS = 0x20
ST_BUTTON = 0x40      # tag button was used
ST_LOWBATT = 0x80
STATUS_NAMES = {ST_INJURED: "INJURED", ST_MOBILE: "MOBILE", ST_WATER: "WATER", ST_FOOD: "FOOD",
                ST_THREAT: "THREAT", ST_DURESS: "DURESS", ST_BUTTON: "BUTTON", ST_LOWBATT: "LOWBATT"}

# FIX class byte: low nibble = how it was found, high bits = flags
CLS_UNKNOWN = 0x00
CLS_WHISTLE = 0x01
CLS_TAP = 0x02
CLS_TAG_RSSI = 0x03   # tag answered a blind poll; position = perch, ce = RSSI range
CLS_AUTH = 0x10       # an authenticated tag REPLY confirmed the person
CLS_ID_SHIFT = 5      # bits 5-6: identity match level vs the missing person's profile
CLS_ID_MASK = 0x60
CLS_SIM = 0x80        # simulated / test traffic
ID_NONE, ID_APPEARANCE, ID_STRONG, ID_FACE = range(4)
ID_LEVEL_NAMES = {ID_NONE: "NONE", ID_APPEARANCE: "APPEARANCE", ID_STRONG: "STRONG", ID_FACE: "FACE"}


class ProtocolError(Exception):
    pass


class AuthError(ProtocolError):
    pass


class ReplayError(ProtocolError):
    pass


def status_names(status: int) -> list:
    return [name for bit, name in STATUS_NAMES.items() if status & bit]


def status_from_names(names) -> int:
    rev = {v.lower(): k for k, v in STATUS_NAMES.items()}
    out = 0
    for n in names or []:
        out |= rev[str(n).lower()]
    return out


def class_name(cls: int) -> str:
    base = {CLS_UNKNOWN: "UNKNOWN", CLS_WHISTLE: "WHISTLE", CLS_TAP: "TAP", CLS_TAG_RSSI: "TAG-RSSI"}.get(
        cls & 0x0F, f"0x{cls & 0x0F:x}")
    flags = [n for b, n in ((CLS_AUTH, "AUTH"), (CLS_SIM, "SIM")) if cls & b]
    lvl = identity_level(cls)
    if lvl:
        flags.append(f"ID-{ID_LEVEL_NAMES[lvl]}")
    return "+".join([base] + flags)


def identity_level(cls: int) -> int:
    """Identity match level (ID_*) carried in bits 5-6 of a FIX class byte."""
    return (int(cls) & CLS_ID_MASK) >> CLS_ID_SHIFT


def with_identity(cls: int, level: int) -> int:
    """``cls`` with its identity bits set to ``level`` (0..3); every other bit is kept."""
    if not 0 <= int(level) <= 3:
        raise ValueError(f"identity level must be 0..3, got {level}")
    return (int(cls) & ~CLS_ID_MASK & 0xFF) | (int(level) << CLS_ID_SHIFT)


# ---- crypto helpers ---------------------------------------------------------------------
def hmac8(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()[:MAC_LEN]


def covert_mask(k_covert: bytes, tag_id: int, eagle_id: int, counter: int) -> int:
    msg = COVERT_LABEL + bytes([tag_id & 0xFF, eagle_id & 0xFF]) + struct.pack(">I", counter & 0xFFFFFFFF)
    return hmac.new(k_covert, msg, hashlib.sha256).digest()[0]


def beacon_key(k_fix: bytes) -> bytes:
    return hmac.new(k_fix, BEACON_KEY_LABEL, hashlib.sha256).digest()


def make_nonce(counter: int, rand4: bytes) -> bytes:
    if len(rand4) != 4:
        raise ValueError("rand4 must be 4 bytes")
    return struct.pack(">I", counter & 0xFFFFFFFF) + rand4


def nonce_counter(nonce: bytes) -> int:
    return struct.unpack(">I", nonce[:4])[0]


def _key(k, name: str) -> bytes:
    if isinstance(k, str):
        k = bytes.fromhex(k)
    if not isinstance(k, (bytes, bytearray)) or len(k) != 32:
        raise ValueError(f"{name} must be 32 bytes (64 hex chars)")
    return bytes(k)


# ---- replay window (RFC 4303 style) -----------------------------------------------------------
class ReplayWindow:
    """Accepts each counter once: anything newer than the highest seen, or within the last
    ``size`` values and not seen yet. Older or repeated counters are rejected."""

    def __init__(self, size: int = 64):
        self.size = int(size)
        self.top: int | None = None
        self.bits = 0

    def check(self, ctr: int) -> bool:
        if self.top is None or ctr > self.top:
            return True
        off = self.top - ctr
        return off < self.size and not (self.bits >> off) & 1

    def update(self, ctr: int) -> None:
        if self.top is None:
            self.top, self.bits = ctr, 1
        elif ctr > self.top:
            shift = ctr - self.top
            self.bits = (((self.bits << shift) | 1) & ((1 << self.size) - 1)) if shift < self.size else 1
            self.top = ctr
        else:
            self.bits |= 1 << (self.top - ctr)

    def accept(self, ctr: int) -> bool:
        if not self.check(ctr):
            return False
        self.update(ctr)
        return True


# ---- power control ----------------------------------------------------------------------------
def required_tx_dbm(sens_dbm: float, path_loss_db: float, margin_db: float = 10.0,
                    min_dbm: int = -9, max_dbm: int = 22) -> int:
    """Lowest whole-dBm power that closes the link with ``margin_db``, clamped to the radio."""
    p = math.ceil(sens_dbm + margin_db + path_loss_db - 1e-9)
    return int(min(max(p, min_dbm), max_dbm))


def path_loss_from(tx_dbm: float, rssi_dbm: float) -> float:
    """Reciprocity: loss measured on the frame we heard = its announced TX power - RSSI."""
    return float(tx_dbm) - float(rssi_dbm)


def log_distance_path_loss(d_m: float, pl_d0_db: float = 31.7, exponent: float = 3.0, d0_m: float = 1.0) -> float:
    """PL(d) = PL(d0) + 10 n log10(d/d0). PL(1 m) at 915 MHz free space ~= 31.7 dB."""
    return pl_d0_db + 10.0 * exponent * math.log10(max(d_m, d0_m) / d0_m)


def distance_from_path_loss(pl_db: float, pl_d0_db: float = 31.7, exponent: float = 3.0, d0_m: float = 1.0) -> float:
    return d0_m * 10.0 ** ((pl_db - pl_d0_db) / (10.0 * exponent))


# ---- CHALLENGE / REPLY -------------------------------------------------------------------------
@dataclass
class Challenge:
    eagle_id: int
    nonce: bytes
    window_10ms: int
    tx_dbm: int

    @property
    def counter(self) -> int:
        return nonce_counter(self.nonce)


def encode_challenge(k_tag, eagle_id: int, nonce: bytes, window_ms: int, tx_dbm: int) -> bytes:
    k = _key(k_tag, "K_tag")
    body = struct.pack(CHAL_FMT, MSG_CHALLENGE, eagle_id & 0xFF, nonce,
                       max(0, min(255, int(round(window_ms / 10)))), int(max(-128, min(127, tx_dbm))))
    return body + hmac8(k, body)


def decode_challenge(k_tag, frame: bytes) -> Challenge:
    k = _key(k_tag, "K_tag")
    if len(frame) != CHAL_LEN or frame[0] != MSG_CHALLENGE:
        raise ProtocolError("not a CHALLENGE")
    body, mac = frame[:CHAL_BODY_LEN], frame[CHAL_BODY_LEN:]
    if not hmac.compare_digest(mac, hmac8(k, body)):
        raise AuthError("CHALLENGE MAC mismatch")
    _, eid, nonce, win, tx = struct.unpack(CHAL_FMT, body)
    return Challenge(eid, nonce, win, tx)


@dataclass
class Reply:
    tag_id: int
    nonce: bytes
    status_wire: int
    battery: int
    rssi_heard: int


def round_half_away(x: float) -> int:
    """C lroundf(): halves round away from zero (SX126x RSSI comes in 0.5 dB steps)."""
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


def encode_reply(k_tag, tag_id: int, nonce: bytes, status_wire: int, battery: int, rssi_heard: float) -> bytes:
    k = _key(k_tag, "K_tag")
    body = struct.pack(REPLY_FMT, MSG_REPLY, tag_id & 0xFF, nonce, status_wire & 0xFF,
                       max(0, min(255, int(battery))), max(-128, min(127, round_half_away(rssi_heard))))
    return body + hmac8(k, body)


def decode_reply(k_tag, frame: bytes, expected_nonce: bytes | None = None) -> Reply:
    k = _key(k_tag, "K_tag")
    if len(frame) != REPLY_LEN or frame[0] != MSG_REPLY:
        raise ProtocolError("not a REPLY")
    body, mac = frame[:REPLY_BODY_LEN], frame[REPLY_BODY_LEN:]
    if not hmac.compare_digest(mac, hmac8(k, body)):
        raise AuthError("REPLY MAC mismatch")
    _, tid, nonce, st, batt, rssi = struct.unpack(REPLY_FMT, body)
    if expected_nonce is not None and not hmac.compare_digest(nonce, expected_nonce):
        raise ReplayError("REPLY answers a different (old?) challenge")
    return Reply(tid, nonce, st, batt, rssi)


# ---- the tag (reference implementation of firmware/tag/tag.ino) ---------------------------------
@dataclass
class TagResponse:
    frame: bytes
    delay_ms: float
    tx_dbm: int


class TagLogic:
    """What the pellet tag does with a received frame. Mirrors firmware/tag/tag.ino."""

    def __init__(self, tag_id: int, k_tag, k_covert, *, sens_dbm: float = -124.0,
                 margin_db: float = 10.0, min_dbm: int = -9, max_dbm: int = 22,
                 status: int = 0, battery: int = 255, window: int = 32, rng=None):
        import random
        self.tag_id = tag_id
        self.k_tag = _key(k_tag, "K_tag")
        self.k_covert = _key(k_covert, "K_covert")
        self.sens, self.margin, self.min_dbm, self.max_dbm = sens_dbm, margin_db, min_dbm, max_dbm
        self.status, self.battery, self.duress = status, battery, False
        self.window = window
        self.windows: dict = {}
        self.rng = rng or random.SystemRandom()
        self.transmissions = 0

    def on_frame(self, frame: bytes, rssi_dbm: float) -> TagResponse | None:
        """Return the single REPLY to send (after ``delay_ms``), or None to stay silent."""
        try:
            ch = decode_challenge(self.k_tag, frame)
        except ProtocolError:
            return None                                   # not for us / forged: silent
        win = self.windows.setdefault(ch.eagle_id, ReplayWindow(self.window))
        if not win.accept(ch.counter):
            return None                                   # replayed challenge: silent
        tx = required_tx_dbm(self.sens, path_loss_from(ch.tx_dbm, rssi_dbm), self.margin,
                             self.min_dbm, self.max_dbm)
        hi = max(50.0, min(500.0, ch.window_10ms * 10.0))
        delay = self.rng.uniform(50.0, hi)
        status = (self.status | (ST_DURESS if self.duress else 0)) & 0xFF
        mask = covert_mask(self.k_covert, self.tag_id, ch.eagle_id, ch.counter)
        self.transmissions += 1
        return TagResponse(encode_reply(self.k_tag, self.tag_id, ch.nonce, status ^ mask,
                                        self.battery, rssi_dbm), delay, tx)


# ---- FIX ------------------------------------------------------------------------------------------
@dataclass
class FixBody:
    lat: float
    lon: float
    alt_m: float = 0.0
    ce_m: float = 0.0
    cls: int = CLS_UNKNOWN
    confidence: float = 0.0     # 0..1
    t_unix: int = 0
    status: int = 0
    tag_id: int = 0
    chal_ctr16: int = 0

    def pack(self) -> bytes:
        return struct.pack(
            FIX_BODY_FMT,
            int(round(self.lat * 1e7)), int(round(self.lon * 1e7)),
            int(max(-32768, min(32767, round(self.alt_m)))),
            int(max(0, min(65535, math.ceil(self.ce_m)))),
            self.cls & 0xFF, int(max(0, min(100, round(self.confidence * 100)))),
            int(self.t_unix) & 0xFFFFFFFF, self.status & 0xFF, self.tag_id & 0xFF, self.chal_ctr16 & 0xFFFF)

    @classmethod
    def unpack(cls, b: bytes) -> "FixBody":
        la, lo, alt, ce, c, conf, t, st, tid, ch = struct.unpack(FIX_BODY_FMT, b)
        return cls(la / 1e7, lo / 1e7, float(alt), float(ce), c, conf / 100.0, t, st, tid, ch)


@dataclass
class FixReport:
    """What the Nest learns from one FIX frame."""
    eagle_id: int
    counter: int
    body: FixBody
    status: int                 # plaintext status (unmasked when a tag answered)
    duress: bool
    authenticated: bool         # class has CLS_AUTH and a tag id
    status_known: bool          # False if a tag answered but the Nest has no covert key

    @property
    def lat(self):
        return self.body.lat

    @property
    def lon(self):
        return self.body.lon


def fix_nonce(salt: int, counter: int, eagle_id: int) -> bytes:
    return struct.pack(">II", salt & 0xFFFFFFFF, counter & 0xFFFFFFFF) + bytes([eagle_id & 0xFF, MSG_FIX, 0, 0])


def encrypt_fix(k_fix, eagle_id: int, salt: int, counter: int, body: FixBody) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    hdr = struct.pack(FIX_HDR_FMT, MSG_FIX, eagle_id & 0xFF, salt & 0xFFFFFFFF, counter & 0xFFFFFFFF)
    ct = AESGCM(_key(k_fix, "K_fix")).encrypt(fix_nonce(salt, counter, eagle_id), body.pack(), hdr)
    frame = hdr + ct
    assert len(frame) == FIX_LEN
    return frame


def decrypt_fix_frame(k_fix, frame: bytes) -> tuple:
    """-> (eagle_id, salt, counter, FixBody); raises AuthError on any tampering."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(frame) != FIX_LEN or frame[0] != MSG_FIX:
        raise ProtocolError("not a FIX")
    _, eid, salt, ctr = struct.unpack(FIX_HDR_FMT, frame[:FIX_HDR_LEN])
    try:
        pt = AESGCM(_key(k_fix, "K_fix")).decrypt(fix_nonce(salt, ctr, eid), frame[FIX_HDR_LEN:],
                                                   frame[:FIX_HDR_LEN])
    except InvalidTag as e:
        raise AuthError("FIX failed AES-GCM authentication") from e
    return eid, salt, ctr, FixBody.unpack(pt)


def full_challenge_counter(fix_counter: int, chal_ctr16: int) -> int:
    """Rebuild the 32-bit challenge counter (<= the FIX counter) from its low 16 bits."""
    c = (fix_counter & 0xFFFF0000) | (chal_ctr16 & 0xFFFF)
    if c > fix_counter:
        c -= 0x10000
    return c


# ---- BEACON ---------------------------------------------------------------------------------------
def encode_beacon(k_fix, nest_id: int, counter: int, tx_dbm: int) -> bytes:
    body = struct.pack(BEACON_FMT, MSG_BEACON, nest_id & 0xFF, counter & 0xFFFFFFFF, int(tx_dbm))
    return body + hmac8(beacon_key(_key(k_fix, "K_fix")), body)


def decode_beacon(k_fix, frame: bytes) -> tuple:
    if len(frame) != BEACON_LEN or frame[0] != MSG_BEACON:
        raise ProtocolError("not a BEACON")
    body, mac = frame[:BEACON_BODY_LEN], frame[BEACON_BODY_LEN:]
    if not hmac.compare_digest(mac, hmac8(beacon_key(_key(k_fix, "K_fix")), body)):
        raise AuthError("BEACON MAC mismatch")
    _, nid, ctr, tx = struct.unpack(BEACON_FMT, body)
    return nid, ctr, tx


# ---- LIFE (multi-person life list) -------------------------------------------------------------
def evidence_names(ev: int) -> list:
    return [name for bit, name in EVIDENCE_NAMES.items() if ev & bit]


def _sat(x: float, lo: int, hi: int) -> int:
    return int(max(lo, min(hi, round(x))))


@dataclass
class LifePerson:
    """One person record of a LIFE frame (offsets in metres from the perch)."""
    track_id: int
    east_m: float
    north_m: float
    ce_m: float
    status: int = LS_UNKNOWN          # LS_* code
    flags: int = 0                    # LF_INJURED | LF_DURESS
    score: float = 0.0                # life score 0..1 (sent in eighths)
    breath_bpm: float = 0.0           # 0 = unknown
    heart_bpm: float = 0.0
    evidence: int = 0                 # EV_* bits
    tag_id: int = 0                   # with EV_TAG: the tag that answered
    tag_status_wire: int = 0          # covert-masked status byte from the REPLY
    tag_chal16: int = 0               # low 16 bits of the answered challenge counter
    tag_status: int | None = None     # plaintext tag status (Nest, after unmasking)

    @property
    def status_name(self) -> str:
        return LIFE_STATUS_NAMES.get(self.status, f"0x{self.status:x}")

    @property
    def size(self) -> int:
        return LIFE_PERSON_LEN + (LIFE_TAG_LEN if self.evidence & EV_TAG else 0)

    def pack(self) -> bytes:
        st = (self.status & 0x07) | (self.flags & (LF_INJURED | LF_DURESS)) | (_sat(self.score * 7, 0, 7) << 5)
        b = struct.pack(LIFE_PERSON_FMT, self.track_id & 0xFF, _sat(self.east_m * 10, -32768, 32767),
                        _sat(self.north_m * 10, -32768, 32767), _sat(math.ceil(self.ce_m * 10 - 1e-9), 0, 255),
                        st, _sat(self.breath_bpm or 0, 0, 255), _sat(self.heart_bpm or 0, 0, 255),
                        self.evidence & 0xFF)
        if self.evidence & EV_TAG:
            b += struct.pack(LIFE_TAG_FMT, self.tag_id & 0xFF, self.tag_status_wire & 0xFF, self.tag_chal16 & 0xFFFF)
        return b

    @classmethod
    def unpack(cls, buf: bytes, off: int = 0) -> tuple:
        tid, e, n, ce, st, br, hr, ev = struct.unpack_from(LIFE_PERSON_FMT, buf, off)
        p = cls(tid, e / 10.0, n / 10.0, ce / 10.0, st & 0x07, st & (LF_INJURED | LF_DURESS), (st >> 5) / 7.0,
                float(br), float(hr), ev)
        off += LIFE_PERSON_LEN
        if ev & EV_TAG:
            p.tag_id, p.tag_status_wire, p.tag_chal16 = struct.unpack_from(LIFE_TAG_FMT, buf, off)
            off += LIFE_TAG_LEN
        return p, off


@dataclass
class LifeHead:
    t_unix: int
    lat: float                         # perch latitude / longitude (deg)
    lon: float
    floor_hae_m: float = 0.0           # height (HAE) of the floor the people are on
    list_seq: int = 0
    chunk_idx: int = 0
    chunk_count: int = 1
    n: int = 0

    def pack(self) -> bytes:
        return struct.pack(LIFE_HEAD_FMT, int(self.t_unix) & 0xFFFFFFFF, int(round(self.lat * 1e7)),
                           int(round(self.lon * 1e7)), _sat(self.floor_hae_m, -32768, 32767), self.list_seq & 0xFFFF,
                           ((self.chunk_idx & 0x0F) << 4) | (self.chunk_count & 0x0F), self.n & 0xFF)

    @classmethod
    def unpack(cls, b: bytes) -> "LifeHead":
        t, la, lo, hae, seq, ch, n = struct.unpack(LIFE_HEAD_FMT, b[:LIFE_HEAD_LEN])
        return cls(t, la / 1e7, lo / 1e7, float(hae), seq, ch >> 4, ch & 0x0F, n)


def life_nonce(salt: int, counter: int, eagle_id: int) -> bytes:
    return struct.pack(">II", salt & 0xFFFFFFFF, counter & 0xFFFFFFFF) + bytes([eagle_id & 0xFF, MSG_LIFE, 0, 0])


def chunk_persons(persons: list, max_len: int = LIFE_MAX_LEN) -> list:
    """Split person records into lists that each fit one LIFE frame of <= ``max_len`` bytes."""
    room = max_len - LIFE_MIN_LEN
    chunks, cur, used = [], [], 0
    for p in persons:
        if p.size > room:
            raise ProtocolError("max_len too small for one person record")
        if used + p.size > room:
            chunks.append(cur)
            cur, used = [], 0
        cur.append(p)
        used += p.size
    if cur or not chunks:
        chunks.append(cur)
    if len(chunks) > 15:
        raise ProtocolError("life list too long: more than 15 frames")
    return chunks


def encrypt_life(k_fix, eagle_id: int, salt: int, counter: int, head: LifeHead, persons: list) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    head.n = len(persons)
    hdr = struct.pack(FIX_HDR_FMT, MSG_LIFE, eagle_id & 0xFF, salt & 0xFFFFFFFF, counter & 0xFFFFFFFF)
    pt = head.pack() + b"".join(p.pack() for p in persons)
    frame = hdr + AESGCM(_key(k_fix, "K_fix")).encrypt(life_nonce(salt, counter, eagle_id), pt, hdr)
    if len(frame) > 255:
        raise ProtocolError("LIFE frame longer than a LoRa packet")
    return frame


def decrypt_life_frame(k_fix, frame: bytes) -> tuple:
    """-> (eagle_id, salt, counter, LifeHead, [LifePerson]); AuthError on any tampering."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(frame) < LIFE_MIN_LEN or frame[0] != MSG_LIFE:
        raise ProtocolError("not a LIFE frame")
    _, eid, salt, ctr = struct.unpack(FIX_HDR_FMT, frame[:FIX_HDR_LEN])
    try:
        pt = AESGCM(_key(k_fix, "K_fix")).decrypt(life_nonce(salt, ctr, eid), frame[FIX_HDR_LEN:],
                                                   frame[:FIX_HDR_LEN])
    except InvalidTag as e:
        raise AuthError("LIFE failed AES-GCM authentication") from e
    head = LifeHead.unpack(pt)
    persons, off = [], LIFE_HEAD_LEN
    for _ in range(head.n):
        p, off = LifePerson.unpack(pt, off)
        persons.append(p)
    if off != len(pt):
        raise ProtocolError("LIFE length mismatch")
    return eid, salt, ctr, head, persons


@dataclass
class LifeReport:
    """What the Nest learns from one LIFE frame (one chunk of an Eagle's life list)."""
    eagle_id: int
    counter: int
    head: LifeHead
    persons: list


# ---- persistent counter -------------------------------------------------------------------------
class CounterStore:
    """Monotonic uint32 counter persisted to a small JSON file (atomic replace).

    Never reuse a counter with the same key: GCM nonces and the tags' replay windows rely
    on it. If the file is lost, start from a value higher than anything used before
    (``python apps/eagle_node.py --set-counter N``).
    """

    def __init__(self, path: str | None = None, start: int = 0):
        self.path = Path(os.path.expanduser(path)) if path else None
        self.value = int(start)
        if self.path and self.path.exists():
            try:
                self.value = max(self.value, int(json.loads(self.path.read_text())["counter"]))
            except Exception as e:  # pragma: no cover
                raise ProtocolError(f"cannot read counter file {self.path}: {e}")

    def next(self) -> int:
        self.value += 1
        if self.value > 0xFFFFFFFF:
            raise ProtocolError("counter exhausted: rotate keys")
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"counter": self.value}))
            os.replace(tmp, self.path)
        return self.value


# ---- sessions -----------------------------------------------------------------------------------
class EagleSession:
    """Eagle-side crypto state: one counter for challenges and FIX frames, boot salt,
    and the latest path-loss estimate to the Nest (from BEACONs)."""

    def __init__(self, eagle_id: int, k_tag, k_fix, *, counter: CounterStore | None = None,
                 salt: int | None = None):
        self.eagle_id = int(eagle_id)
        self.k_tag = _key(k_tag, "K_tag") if k_tag else None
        self.k_fix = _key(k_fix, "K_fix")
        self.counter = counter or CounterStore()
        self.salt = int.from_bytes(os.urandom(4), "big") if salt is None else int(salt)
        self.beacon_windows: dict = {}
        self.nest_path_loss: float | None = None
        self.nest_path_loss_t: float = 0.0

    def make_challenge(self, window_ms: int, tx_dbm: int) -> tuple:
        if self.k_tag is None:
            raise ProtocolError("no K_tag configured")
        nonce = make_nonce(self.counter.next(), os.urandom(4))
        return encode_challenge(self.k_tag, self.eagle_id, nonce, window_ms, tx_dbm), nonce

    def verify_reply(self, frame: bytes, nonce: bytes) -> Reply:
        return decode_reply(self.k_tag, frame, expected_nonce=nonce)

    def encrypt_fix(self, body: FixBody) -> bytes:
        return encrypt_fix(self.k_fix, self.eagle_id, self.salt, self.counter.next(), body)

    def encrypt_life(self, head: LifeHead, persons: list, max_len: int = LIFE_MAX_LEN) -> list:
        """Encrypt a life list as one or more LIFE frames (each <= ``max_len`` bytes, own counter)."""
        chunks = chunk_persons(list(persons), max_len)
        frames = []
        for i, ch in enumerate(chunks):
            ctr = self.counter.next()
            if i == 0:
                head.list_seq = ctr & 0xFFFF
            h = LifeHead(head.t_unix, head.lat, head.lon, head.floor_hae_m, head.list_seq, i, len(chunks))
            frames.append(encrypt_life(self.k_fix, self.eagle_id, self.salt, ctr, h, ch))
        return frames

    def on_beacon(self, frame: bytes, rssi_dbm: float, now: float | None = None) -> float:
        nid, ctr, tx = decode_beacon(self.k_fix, frame)
        if not self.beacon_windows.setdefault(nid, ReplayWindow(64)).accept(ctr):
            raise ReplayError("replayed BEACON")
        self.nest_path_loss = path_loss_from(tx, rssi_dbm)
        self.nest_path_loss_t = time.time() if now is None else now
        return self.nest_path_loss


class NestSession:
    """Nest-side: decrypt + authenticate FIX frames, replay window per Eagle, unmask status."""

    def __init__(self, k_fix, k_covert=None, *, window: int = 64, max_age_s: float = 600.0,
                 nest_id: int = 1, beacon_counter: CounterStore | None = None):
        self.k_fix = _key(k_fix, "K_fix")
        self.k_covert = _key(k_covert, "K_covert") if k_covert else None
        self.window, self.max_age_s, self.nest_id = window, max_age_s, nest_id
        self.windows: dict = {}
        self.beacon_counter = beacon_counter or CounterStore()

    def decrypt_fix(self, frame: bytes, now: float | None = None) -> FixReport:
        eid, salt, ctr, body = decrypt_fix_frame(self.k_fix, frame)
        win = self.windows.setdefault(eid, ReplayWindow(self.window))
        if not win.check(ctr):
            raise ReplayError(f"replayed FIX from eagle {eid} (counter {ctr})")
        now = time.time() if now is None else now
        if self.max_age_s and abs(now - body.t_unix) > self.max_age_s:
            raise ReplayError(f"FIX timestamp {body.t_unix} is {now - body.t_unix:.0f} s off")
        win.update(ctr)
        status, known = body.status, True
        if body.tag_id:
            if self.k_covert is None:
                known = False
            else:
                chal = full_challenge_counter(ctr, body.chal_ctr16)
                status = body.status ^ covert_mask(self.k_covert, body.tag_id, eid, chal)
        auth = bool(body.cls & CLS_AUTH) and body.tag_id != 0
        return FixReport(eid, ctr, body, status, bool(known and status & ST_DURESS), auth, known)

    def decrypt_life(self, frame: bytes, now: float | None = None) -> LifeReport:
        """Decrypt + authenticate a LIFE frame (shares the per-Eagle replay window with FIX,
        since both use the Eagle's one counter) and unmask tag status for tagged persons."""
        eid, salt, ctr, head, persons = decrypt_life_frame(self.k_fix, frame)
        win = self.windows.setdefault(eid, ReplayWindow(self.window))
        if not win.check(ctr):
            raise ReplayError(f"replayed LIFE from eagle {eid} (counter {ctr})")
        now = time.time() if now is None else now
        if self.max_age_s and abs(now - head.t_unix) > self.max_age_s:
            raise ReplayError(f"LIFE timestamp {head.t_unix} is {now - head.t_unix:.0f} s off")
        win.update(ctr)
        for p in persons:
            if p.evidence & EV_TAG and p.tag_id and self.k_covert is not None:
                chal = full_challenge_counter(ctr, p.tag_chal16)
                p.tag_status = p.tag_status_wire ^ covert_mask(self.k_covert, p.tag_id, eid, chal)
                if p.tag_status & ST_INJURED:
                    p.flags |= LF_INJURED
                if p.tag_status & ST_DURESS:
                    p.flags |= LF_DURESS
                if p.tag_status & ST_BUTTON and p.status != LS_RESPONSIVE:
                    p.status = LS_RESPONSIVE           # the person pressed the tag button
        return LifeReport(eid, ctr, head, persons)

    def make_beacon(self, tx_dbm: int) -> bytes:
        return encode_beacon(self.k_fix, self.nest_id, self.beacon_counter.next(), tx_dbm)


def lora_sensitivity(sf: int, bw_khz: float, nf_db: float = 6.0) -> float:
    return sensitivity_dbm(sf, bw_khz * 1e3, nf_db)
