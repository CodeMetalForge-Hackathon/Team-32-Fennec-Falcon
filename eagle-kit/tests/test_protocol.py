import random

import pytest

from eagle import protocol as P

KT, KF, KC = bytes(range(32)), bytes(range(32, 64)), bytes(range(64, 96))


def tag(**kw):
    return P.TagLogic(7, KT, KC, sens_dbm=-124.5, rng=random.Random(1), **kw)


def challenge(ctr=1, eagle=1, tx=-9, key=KT, window_ms=500):
    nonce = P.make_nonce(ctr, b"\xa1\xa2\xa3\xa4")
    return P.encode_challenge(key, eagle, nonce, window_ms, tx), nonce


# ---- layouts --------------------------------------------------------------------------------
def test_frame_sizes():
    assert P.CHAL_LEN == 20 and P.REPLY_LEN == 21 and P.BEACON_LEN == 15
    assert P.FIX_LEN == 48
    body = P.FixBody(42.3550767, -71.0653398, 11, 3.2, P.CLS_WHISTLE | P.CLS_AUTH, 0.83, 1_800_000_000,
                     P.ST_INJURED, 7, 0x1234)
    frame = P.encrypt_fix(KF, 1, 0xDEADBEEF, 5, body)
    assert len(frame) <= 48


# ---- HMAC accept / reject / replay ------------------------------------------------------------
def test_tag_answers_valid_challenge_once():
    t = tag()
    ch, nonce = challenge(ctr=10)
    r = t.on_frame(ch, rssi_dbm=-80)
    assert r is not None and 50 <= r.delay_ms <= 500
    rep = P.decode_reply(KT, r.frame, expected_nonce=nonce)
    assert rep.tag_id == 7 and rep.rssi_heard == -80
    assert t.on_frame(ch, rssi_dbm=-80) is None            # exact replay: silent
    assert t.transmissions == 1


def test_tag_rejects_forgeries():
    t = tag()
    bad_key, _ = challenge(key=bytes(32))
    assert t.on_frame(bad_key, -80) is None
    ch, _ = challenge()
    tampered = bytearray(ch)
    tampered[11] ^= 0x01                                     # change the announced TX power
    assert t.on_frame(bytes(tampered), -80) is None
    assert t.on_frame(ch[:-1], -80) is None                  # wrong length
    assert t.on_frame(b"\x02" + ch[1:], -80) is None         # wrong type
    assert t.transmissions == 0
    with pytest.raises(P.AuthError):
        P.decode_challenge(KT, bytes(tampered))


def test_replay_window_out_of_order_and_too_old():
    t = tag(window=32)
    assert t.on_frame(challenge(ctr=100)[0], -80) is not None
    assert t.on_frame(challenge(ctr=90)[0], -80) is not None     # late but inside the window, once
    assert t.on_frame(challenge(ctr=90)[0], -80) is None
    assert t.on_frame(challenge(ctr=60)[0], -80) is None         # older than the window
    assert t.on_frame(challenge(ctr=101)[0], -80) is not None
    assert t.on_frame(challenge(ctr=100, eagle=2)[0], -80) is not None   # per-Eagle windows


def test_replay_window_unit():
    w = P.ReplayWindow(8)
    assert w.accept(5) and not w.accept(5) and w.accept(3) and w.accept(20)
    assert not w.accept(12)        # 20 - 12 = 8: outside an 8-wide window
    assert w.accept(13) and not w.accept(13)


def test_reply_for_other_challenge_rejected():
    t = tag()
    ch1, n1 = challenge(ctr=1)
    ch2, n2 = challenge(ctr=2)
    r1 = t.on_frame(ch1, -70)
    with pytest.raises(P.ReplayError):
        P.decode_reply(KT, r1.frame, expected_nonce=n2)
    bad = bytearray(r1.frame)
    bad[10] ^= 0x20
    with pytest.raises(P.AuthError):
        P.decode_reply(KT, bytes(bad), expected_nonce=n1)


# ---- duress ----------------------------------------------------------------------------------
def _round_trip(duress: bool, status=P.ST_INJURED | P.ST_WATER):
    t = tag(status=status)
    t.duress = duress
    sess = P.EagleSession(3, KT, KF, counter=P.CounterStore(None, start=500), salt=0x01020304)
    ch, nonce = sess.make_challenge(500, -9)
    r = t.on_frame(ch, -75)
    rep = sess.verify_reply(r.frame, nonce)                  # Eagle-side check: same either way
    body = P.FixBody(42.0, -71.0, 10, 4, P.CLS_WHISTLE | P.CLS_AUTH, 0.9, 1_800_000_000, rep.status_wire,
                     rep.tag_id, P.nonce_counter(nonce) & 0xFFFF)
    frame = sess.encrypt_fix(body)
    nest = P.NestSession(KF, KC, max_age_s=0)
    return rep, nest.decrypt_fix(frame)


def test_duress_verifies_like_normal_but_only_nest_sees_it():
    rep_n, fix_n = _round_trip(False)
    rep_d, fix_d = _round_trip(True)
    assert rep_n.tag_id == rep_d.tag_id == 7                 # both verified at the Eagle
    assert not fix_n.duress and fix_d.duress
    assert fix_n.status == P.ST_INJURED | P.ST_WATER
    assert fix_d.status == P.ST_INJURED | P.ST_WATER | P.ST_DURESS
    assert fix_n.authenticated and fix_d.authenticated
    # on the air the status byte is masked: it is not the plaintext
    assert rep_n.status_wire != fix_n.status or rep_d.status_wire != fix_d.status


def test_nest_without_covert_key_cannot_read_status():
    t = tag(status=P.ST_INJURED)
    t.duress = True
    sess = P.EagleSession(1, KT, KF, counter=P.CounterStore(None, start=7))
    ch, nonce = sess.make_challenge(500, -9)
    rep = sess.verify_reply(t.on_frame(ch, -70).frame, nonce)
    body = P.FixBody(1, 2, cls=P.CLS_AUTH | P.CLS_WHISTLE, t_unix=0, status=rep.status_wire, tag_id=7,
                     chal_ctr16=P.nonce_counter(nonce))
    out = P.NestSession(KF, None, max_age_s=0).decrypt_fix(sess.encrypt_fix(body))
    assert not out.status_known and not out.duress


def test_full_challenge_counter_wraps_low_bits():
    assert P.full_challenge_counter(0x00020005, 0x0004) == 0x00020004
    assert P.full_challenge_counter(0x00020001, 0xFFFF) == 0x0001FFFF


# ---- AES-GCM ----------------------------------------------------------------------------------
def test_fix_round_trip_and_tamper():
    body = P.FixBody(-33.8688197, 151.2092955, -5, 12.2, P.CLS_TAP, 0.5, 1_800_000_123, 0, 0, 0)
    frame = P.encrypt_fix(KF, 9, 0xCAFEBABE, 77, body)
    eid, salt, ctr, out = P.decrypt_fix_frame(KF, frame)
    assert (eid, salt, ctr) == (9, 0xCAFEBABE, 77)
    assert abs(out.lat - body.lat) < 1e-7 and abs(out.lon - body.lon) < 1e-7
    assert out.alt_m == -5 and out.ce_m == 13 and out.cls == P.CLS_TAP and out.t_unix == 1_800_000_123
    for i in (1, 5, 12, 40, 47):                             # header (AAD), ciphertext, tag
        bad = bytearray(frame)
        bad[i] ^= 0x01
        with pytest.raises(P.AuthError):
            P.decrypt_fix_frame(KF, bytes(bad))
    with pytest.raises(P.AuthError):
        P.decrypt_fix_frame(bytes(32), frame)


def test_nest_replay_and_age():
    sess = P.EagleSession(1, KT, KF, counter=P.CounterStore(None, start=10))
    nest = P.NestSession(KF, KC, max_age_s=600)
    f1 = sess.encrypt_fix(P.FixBody(1, 2, t_unix=1_000_000))
    f2 = sess.encrypt_fix(P.FixBody(1, 2, t_unix=1_000_000))
    assert nest.decrypt_fix(f2, now=1_000_010).counter == 12
    assert nest.decrypt_fix(f1, now=1_000_010).counter == 11      # late but inside the window
    with pytest.raises(P.ReplayError):
        nest.decrypt_fix(f2, now=1_000_010)                       # replay
    f3 = sess.encrypt_fix(P.FixBody(1, 2, t_unix=1_000_000))
    with pytest.raises(P.ReplayError):
        nest.decrypt_fix(f3, now=1_000_000 + 3600)                # stale timestamp


def test_counter_store_persists(tmp_path):
    p = tmp_path / "state.json"
    c = P.CounterStore(str(p))
    assert [c.next(), c.next()] == [1, 2]
    assert P.CounterStore(str(p)).next() == 3


def test_beacon_round_trip_and_path_loss():
    nest = P.NestSession(KF, KC)
    sess = P.EagleSession(1, KT, KF)
    b = nest.make_beacon(10)
    assert len(b) == P.BEACON_LEN
    assert sess.on_beacon(b, -105.0, now=0) == pytest.approx(115.0)
    with pytest.raises(P.ReplayError):
        sess.on_beacon(b, -105.0)
    with pytest.raises(P.AuthError):
        P.EagleSession(1, KT, bytes(32)).on_beacon(nest.make_beacon(10), -100)


# ---- power control ----------------------------------------------------------------------------
@pytest.mark.parametrize("tx,rssi,expected", [
    (10, -80, -9),      # PL 90 dB: -124.5 + 10 + 90 = -24.5 -> clamp to -9
    (10, -120, 16),     # PL 130 dB: 15.5 -> 16
    (22, -140, 22),     # PL 162 dB: needs 47.5 -> clamp to +22
    (0, -105, -9),      # PL 105 dB: -9.5 -> -9
    (0, -110, -4),      # PL 110 dB: -4.5 -> -4
])
def test_tag_reply_power(tx, rssi, expected):
    t = tag()
    ch, _ = challenge(ctr=1, tx=tx)
    assert t.on_frame(ch, rssi).tx_dbm == expected
    assert P.required_tx_dbm(-124.5, P.path_loss_from(tx, rssi), 10.0) == expected


def test_sensitivity_and_path_loss_model():
    assert P.lora_sensitivity(7, 125) == pytest.approx(-124.53, abs=0.01)
    assert P.log_distance_path_loss(1.0) == pytest.approx(31.7)
    assert P.log_distance_path_loss(100.0, exponent=2.0) == pytest.approx(71.7)
    assert P.distance_from_path_loss(P.log_distance_path_loss(250.0)) == pytest.approx(250.0)


def test_status_helpers():
    s = P.status_from_names(["injured", "WATER"])
    assert s == P.ST_INJURED | P.ST_WATER and P.status_names(s) == ["INJURED", "WATER"]
    assert P.class_name(P.CLS_WHISTLE | P.CLS_AUTH) == "WHISTLE+AUTH"


def test_identity_level_in_fix_class_byte():
    """Match level rides in class bits 5-6: layout stays 48 bytes, other bits are untouched."""
    k = bytes(range(32))
    for base in (P.CLS_UNKNOWN, P.CLS_WHISTLE | P.CLS_AUTH, P.CLS_TAP | P.CLS_SIM, P.CLS_TAG_RSSI):
        for lvl in range(4):
            cls = P.with_identity(base, lvl)
            assert P.identity_level(cls) == lvl and cls & ~P.CLS_ID_MASK == base
            sess = P.EagleSession(3, k, k)
            frame = sess.encrypt_fix(P.FixBody(42.0, -71.0, 10, 4, cls, 0.7, 1_800_000_000))
            assert len(frame) == P.FIX_LEN == 48
            rep = P.NestSession(k, max_age_s=0).decrypt_fix(frame)
            assert P.identity_level(rep.body.cls) == lvl and rep.body.cls & 0x0F == base & 0x0F
            assert rep.authenticated is False                    # identity never implies a tag
    assert P.with_identity(P.with_identity(P.CLS_TAP, 3), 1) == P.with_identity(P.CLS_TAP, 1)
    assert P.class_name(P.with_identity(P.CLS_WHISTLE | P.CLS_AUTH, P.ID_FACE)) == "WHISTLE+AUTH+ID-FACE"
    assert P.class_name(P.CLS_TAP) == "TAP"
    with pytest.raises(ValueError):
        P.with_identity(0, 4)
