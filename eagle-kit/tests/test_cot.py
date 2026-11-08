import datetime as dt
import socket
import threading

import pytest

from eagle import protocol as P
from eagle.cot import (AUTHENTICATED_TYPE, UNCONFIRMED_TYPE, TcpSender, UdpMulticastSender, UdpUnicastSender,
                       build_cot, parse_cot)
from eagle.link import RxFrame
from eagle.nest import NestStation
from xml.etree import ElementTree as ET


def test_cot_parses_with_required_attributes():
    now = dt.datetime(2026, 11, 8, 16, 0, 0, 123000, tzinfo=dt.timezone.utc)
    xml = build_cot("eagle-1-42", 42.3550767, -71.0653398, hae=11.0, ce=3.0, callsign="SAR",
                    remarks="test", now=now, stale_s=300)
    ev = ET.fromstring(xml)
    assert ev.tag == "event"
    for a in ("version", "uid", "type", "how", "time", "start", "stale"):
        assert ev.get(a), a
    assert ev.get("version") == "2.0" and ev.get("type") == UNCONFIRMED_TYPE
    assert ev.get("time") == "2026-11-08T16:00:00.123Z" and ev.get("stale") == "2026-11-08T16:05:00.123Z"
    pt = ev.find("point")
    for a in ("lat", "lon", "hae", "ce", "le"):
        assert pt.get(a) is not None, a
    assert float(pt.get("lat")) == pytest.approx(42.3550767) and float(pt.get("le")) == 9999999.0
    assert ev.find("detail/contact").get("callsign") == "SAR"
    assert ev.find("detail/remarks").text == "test"
    assert xml.startswith(b"<?xml")


def test_cot_rejects_bad_coordinates():
    with pytest.raises(ValueError):
        build_cot("x", 91.0, 0.0)


def _nest(cfg):
    return NestStation(cfg, senders=[], log=lambda m: None, sim=True)


def test_type_switches_when_authenticated(cfg, keys):
    nest = _nest(cfg)
    sess = P.EagleSession(1, keys["tag_hmac"], keys["fix_aes"], counter=P.CounterStore(None, start=1))
    now = 1_800_000_000
    plain = sess.encrypt_fix(P.FixBody(42.0, -71.0, 10, 4, P.CLS_WHISTLE, 0.8, now))
    rep, xml = nest.handle(RxFrame(plain, -100, 5), now=now)
    assert parse_cot(xml)["type"] == UNCONFIRMED_TYPE
    t = P.TagLogic(7, keys["tag_hmac"], keys["covert"], status=P.ST_INJURED)
    ch, nonce = sess.make_challenge(500, -9)
    reply = sess.verify_reply(t.on_frame(ch, -70).frame, nonce)
    auth = sess.encrypt_fix(P.FixBody(42.0, -71.0, 10, 4, P.CLS_WHISTLE | P.CLS_AUTH, 0.8, now,
                                      reply.status_wire, 7, P.nonce_counter(nonce) & 0xFFFF))
    rep, xml = nest.handle(RxFrame(auth, -100, 5), now=now)
    c = parse_cot(xml)
    assert c["type"] == AUTHENTICATED_TYPE and c["uid"].endswith("tag7") and "INJURED" in c["remarks"]
    assert nest.handle(RxFrame(auth, -100, 5), now=now) is None          # replay rejected


def test_type_strings_configurable(cfg, keys):
    from eagle.config import deep_merge
    nest = _nest(deep_merge(cfg, {"cot": {"unconfirmed_type": "a-n-G"}}))
    sess = P.EagleSession(1, keys["tag_hmac"], keys["fix_aes"])
    f = sess.encrypt_fix(P.FixBody(1.0, 2.0, t_unix=5))
    _, xml = nest.handle(RxFrame(f, -90, 10), now=5)
    assert parse_cot(xml)["type"] == "a-n-G"


def test_udp_unicast_sender_delivers():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(2)
    xml = build_cot("u", 1.0, 2.0)
    s = UdpUnicastSender("127.0.0.1", rx.getsockname()[1])
    s.send(xml)
    assert rx.recvfrom(65535)[0] == xml
    s.close()
    rx.close()


def test_tcp_sender_delivers():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    got = []

    def accept():
        c, _ = srv.accept()
        c.settimeout(2)
        got.append(c.recv(65535))
        c.close()
    th = threading.Thread(target=accept)
    th.start()
    xml = build_cot("t", 1.0, 2.0)
    s = TcpSender("127.0.0.1", srv.getsockname()[1])
    s.send(xml)
    th.join(3)
    s.close()
    srv.close()
    assert got and got[0] == xml


def test_multicast_sender_defaults():
    s = UdpMulticastSender(ttl=0)              # ttl 0: never leaves this host
    assert s.addr == ("239.2.3.1", 6969)
    try:
        s.send(build_cot("m", 1.0, 2.0))
    except OSError as e:                       # sandboxes may have no multicast route
        pytest.skip(f"no multicast route here: {e}")
    finally:
        s.close()
