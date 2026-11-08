import queue
import random
import socket

import pytest

from eagle import protocol as P
from eagle.link import (EnemyScanner, MockChannel, PathLossModel, SerialTransport, make_transport,
                        parse_radio_line)


def test_parse_radio_line():
    k, f = parse_radio_line("RX -87 9.5 deadBEEF\r\n")
    assert k == "rx" and f.payload == b"\xde\xad\xbe\xef" and f.rssi == -87 and f.snr == 9.5
    assert parse_radio_line("OK TX 56576") == ("resp", "OK TX 56576")
    assert parse_radio_line("ERR -2 nope")[0] == "resp"
    assert parse_radio_line("PONG") == ("resp", "PONG")
    assert parse_radio_line("READY eagle_radio 1") == ("info", "READY eagle_radio 1")
    assert parse_radio_line("RX -87 zz")[0] == "info"
    assert parse_radio_line("RX -87 1 xyz")[0] == "info"


class FakeFirmware:
    """Pretends to be firmware/eagle_radio over a serial port."""

    def __init__(self):
        self.out = queue.Queue()
        self.sent = []

    def write(self, data):
        line = data.decode().strip()
        if line == "PING":
            self.out.put(b"PONG\n")
        elif line.startswith("CFG "):
            self.out.put(b"OK CFG\n")
        elif line.startswith("TX "):
            _, dbm, hexs = line.split()
            self.sent.append((int(dbm), bytes.fromhex(hexs)))
            self.out.put(b"OK TX 56576\n")

    def readline(self):
        try:
            return self.out.get(timeout=0.05)
        except queue.Empty:
            return b""

    def flush(self):
        pass

    def close(self):
        pass


def test_serial_transport_round_trip():
    fw = FakeFirmware()
    fw.out.put(b"READY eagle_radio 1\n")
    t = SerialTransport(ser=fw, log=lambda *a: None)
    try:
        assert t.ping()
        t.configure(915.0, 7, 125, 5)
        toa = t.send(b"\x01" * 20, -3)
        assert fw.sent == [(-3, b"\x01" * 20)] and toa == pytest.approx(0.056576)
        fw.out.put(b"RX -101 -3.5 0102\n")
        f = t.recv(timeout=1.0)
        assert f.payload == b"\x01\x02" and f.rssi == -101 and f.snr == -3.5
        assert t.recv(timeout=0.05) is None
    finally:
        t.close()


def test_mock_channel_path_loss_and_sensitivity():
    ch = MockChannel(PathLossModel(exponent=3.0))
    a = ch.add_node("a", [0, 0, 0])
    near = ch.add_node("near", [100, 0, 0])
    far = ch.add_node("far", [20000, 0, 0])
    toa = a.send(b"x" * 48, 0)
    assert toa == pytest.approx(0.097536)
    f = near.recv()
    assert f.rssi == pytest.approx(0 - (31.7 + 60.0)) and f.snr == pytest.approx(f.rssi - ch.noise_floor)
    assert far.recv() is None                                   # below sensitivity
    assert ch.log[-1].delivered["far"] is None
    assert ch.clock == pytest.approx(toa)


def test_enemy_scanner_only_sees_strong_bursts():
    ch = MockChannel(PathLossModel(exponent=3.0))
    eagle = ch.add_node("eagle", [0, 0, 8])
    sc = ch.add_scanner(EnemyScanner("sdr", [1500, 0, 2], noise_figure_db=8, threshold_db=3))
    eagle.send(b"x" * 48, -9)
    eagle.send(b"x" * 48, 22)
    low, high = sc.hits
    assert not low.detected and high.detected
    assert high.snr_db - low.snr_db == pytest.approx(31.0)


def test_mock_responder_tag_loop():
    kt, kc = bytes(32), bytes(range(32))
    ch = MockChannel()
    eagle = ch.add_node("eagle", [0, 0, 8])
    tag_t = ch.add_node("tag", [15, 5, 0.5])
    tag = P.TagLogic(3, kt, kc, rng=random.Random(0))
    tag_t.responder = lambda rx: (lambda r: (r.frame, r.tx_dbm, r.delay_ms / 1e3) if r else None)(
        tag.on_frame(rx.payload, rx.rssi))
    sess = P.EagleSession(1, kt, bytes(32))
    frame, nonce = sess.make_challenge(500, -9)
    eagle.send(frame, -9)
    rx = eagle.recv()
    assert rx is not None and sess.verify_reply(rx.payload, nonce).tag_id == 3
    eagle.send(frame, -9)                                      # replay -> no answer
    assert eagle.recv() is None


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_udp_bench_transport_and_role_swap():
    p1, p2 = _free_port(), _free_port()
    cfg = {"transport": "udp", "sf": 7, "bw_khz": 125, "cr": 5,
           "udp": {"bind": f"127.0.0.1:{p1}", "peer": f"127.0.0.1:{p2}", "path_loss_db": 100.0}}
    node = make_transport(cfg)
    nest = make_transport(cfg, role="nest")
    try:
        node.send(b"\x03" * 48, 5)
        f = nest.recv(timeout=2.0)
        assert f.payload == b"\x03" * 48 and f.rssi == pytest.approx(-95.0)
        nest.send(b"\x04" * 15, 10)
        assert node.recv(timeout=2.0).rssi == pytest.approx(-90.0)
        assert node.recv(timeout=0.05) is None
    finally:
        node.close()
        nest.close()
