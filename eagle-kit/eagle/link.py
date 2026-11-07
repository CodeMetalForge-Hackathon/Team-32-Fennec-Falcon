"""Radio transports. Every transport has the same tiny interface:

    configure(freq_mhz, sf, bw_khz, cr)       set modem parameters
    send(payload: bytes, tx_dbm: int) -> s     transmit ONE frame, returns time on air
    recv(timeout_s) -> RxFrame | None          next received frame (payload, rssi, snr)
    close()

* :class:`SerialTransport`     our firmware (firmware/eagle_radio) over USB serial.
  Line protocol (one command per line, 115200 baud):
      host -> radio   CFG <freq_mhz> <sf> <bw_khz> <cr>      e.g. "CFG 915.000 7 125 5"
                      TX <dbm> <hex>                          e.g. "TX -3 0301a1..."
                      PING
      radio -> host   READY eagle_radio <version>             on boot
                      OK CFG | OK TX <airtime_us> | PONG
                      ERR <code> <text>
                      RX <rssi_dbm> <snr_db> <hex>            every received frame
  The radio only ever transmits when told to (TX); it never beacons or acks.
* :class:`MeshtasticTransport` a stock Meshtastic node as the modem (optional package).
* :class:`UdpTransport`        "radio over UDP" for bench tests across processes/hosts.
* :class:`MockChannel` / :class:`MockTransport` / :class:`EnemyScanner` in-memory air with a
  log-distance path-loss model, used by tests and apps/sim_demo.py.
"""
from __future__ import annotations

import json
import queue
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .airtime import SNR_LIMIT_DB, noise_floor_dbm, time_on_air
from .protocol import log_distance_path_loss


class LinkError(Exception):
    pass


@dataclass
class RxFrame:
    payload: bytes
    rssi: float
    snr: float
    t: float = 0.0
    source: str | None = None


class Transport:
    name = "base"

    def __init__(self):
        self.freq_mhz, self.sf, self.bw_khz, self.cr = 915.0, 7, 125.0, 5

    def configure(self, freq_mhz: float, sf: int, bw_khz: float, cr: int) -> None:
        self.freq_mhz, self.sf, self.bw_khz, self.cr = float(freq_mhz), int(sf), float(bw_khz), int(cr)

    def airtime(self, n_bytes: int) -> float:
        return time_on_air(n_bytes, self.sf, self.bw_khz * 1e3, self.cr)

    def send(self, payload: bytes, tx_dbm: int) -> float:  # pragma: no cover - interface
        raise NotImplementedError

    def recv(self, timeout: float | None = None) -> RxFrame | None:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:
        pass


# ---- serial (our firmware) --------------------------------------------------------------------
def parse_radio_line(line: str):
    """Parse one line from the radio -> ('rx', RxFrame) | ('resp', str) | ('info', str)."""
    line = line.strip()
    if line.startswith("RX "):
        parts = line.split()
        if len(parts) != 4:
            return ("info", f"malformed: {line}")
        try:
            return ("rx", RxFrame(bytes.fromhex(parts[3]), float(parts[1]), float(parts[2]), time.time()))
        except ValueError:
            return ("info", f"malformed: {line}")
    if line.startswith(("OK", "ERR", "PONG")):
        return ("resp", line)
    return ("info", line)


class SerialTransport(Transport):
    name = "serial"

    def __init__(self, port: str | None = None, baud: int = 115200, *, ser=None, log=print):
        super().__init__()
        if ser is None:
            import serial  # pyserial
            ser = serial.Serial(port, baud, timeout=0.1)
        self.ser = ser
        self.log = log or (lambda *a: None)
        self.rx_q: queue.Queue = queue.Queue()
        self.resp_q: queue.Queue = queue.Queue()
        self.info: list = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def _reader(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self.ser.readline()
            except Exception as e:  # pragma: no cover - hardware
                self.log(f"[serial] read error: {e}")
                time.sleep(0.2)
                continue
            if not raw:
                continue
            kind, val = parse_radio_line(raw.decode("ascii", errors="replace"))
            if kind == "rx":
                self.rx_q.put(val)
            elif kind == "resp":
                self.resp_q.put(val)
            elif val:
                self.info.append(val)
                self.log(f"[radio] {val}")

    def _cmd(self, text: str, timeout: float) -> str:
        while not self.resp_q.empty():
            self.resp_q.get_nowait()
        self.ser.write((text + "\n").encode("ascii"))
        try:
            self.ser.flush()
        except Exception:  # pragma: no cover
            pass
        try:
            resp = self.resp_q.get(timeout=timeout)
        except queue.Empty as e:
            raise LinkError(f"radio did not answer {text.split()[0]!r} within {timeout:.1f} s") from e
        if resp.startswith("ERR"):
            raise LinkError(resp)
        return resp

    def ping(self, timeout: float = 2.0) -> bool:
        return self._cmd("PING", timeout) == "PONG"

    def configure(self, freq_mhz, sf, bw_khz, cr) -> None:
        super().configure(freq_mhz, sf, bw_khz, cr)
        self._cmd(f"CFG {self.freq_mhz:.3f} {self.sf} {self.bw_khz:g} {self.cr}", 3.0)

    def send(self, payload: bytes, tx_dbm: int) -> float:
        toa = self.airtime(len(payload))
        resp = self._cmd(f"TX {int(tx_dbm)} {payload.hex()}", toa + 3.0)
        parts = resp.split()
        if len(parts) >= 3 and parts[1] == "TX":
            try:
                return int(parts[2]) / 1e6
            except ValueError:
                pass
        return toa

    def recv(self, timeout: float | None = None) -> RxFrame | None:
        try:
            return self.rx_q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._stop.set()
        try:
            self.ser.close()
        except Exception:  # pragma: no cover
            pass


# ---- Meshtastic (optional) ----------------------------------------------------------------------
class MeshtasticTransport(Transport):
    """Use a stock Meshtastic node as the modem (``pip install meshtastic``).

    Limits: TX power is the node's lora.tx_power (no per-frame power control), Meshtastic
    adds its own packet header and channel encryption (airtime grows), and the node must be
    quiet: role CLIENT_HIDDEN, position and telemetry broadcasts off, hop_limit 0.
    """
    name = "meshtastic"

    def __init__(self, dev_path: str | None = None, channel_index: int = 0,
                 destination="^all", hop_limit: int = 0):
        super().__init__()
        try:
            import meshtastic.serial_interface  # type: ignore
            from pubsub import pub  # type: ignore
        except ImportError as e:  # pragma: no cover - optional
            raise LinkError("pip install meshtastic  (and pypubsub)") from e
        try:
            from meshtastic.protobuf import portnums_pb2  # type: ignore
        except ImportError:  # pragma: no cover - older package layout
            from meshtastic import portnums_pb2  # type: ignore
        self.portnum = portnums_pb2.PortNum.PRIVATE_APP
        self.channel_index, self.destination, self.hop_limit = channel_index, destination, hop_limit
        self.rx_q: queue.Queue = queue.Queue()
        pub.subscribe(self._on_receive, "meshtastic.receive")
        self.iface = meshtastic.serial_interface.SerialInterface(devPath=dev_path)

    def _on_receive(self, packet, interface=None):  # pragma: no cover - needs hardware
        d = packet.get("decoded", {})
        if d.get("portnum") in ("PRIVATE_APP", 256) and d.get("payload") is not None:
            self.rx_q.put(RxFrame(bytes(d["payload"]), float(packet.get("rxRssi", 0)),
                                  float(packet.get("rxSnr", 0)), time.time(), str(packet.get("fromId"))))

    def configure(self, freq_mhz, sf, bw_khz, cr) -> None:  # pragma: no cover
        super().configure(freq_mhz, sf, bw_khz, cr)  # modem preset is set on the node itself

    def send(self, payload: bytes, tx_dbm: int) -> float:  # pragma: no cover - needs hardware
        self.iface.sendData(payload, destinationId=self.destination, portNum=self.portnum,
                            wantAck=False, channelIndex=self.channel_index, hopLimit=self.hop_limit)
        return self.airtime(len(payload) + 16)  # rough: Meshtastic header overhead

    def recv(self, timeout: float | None = None) -> RxFrame | None:  # pragma: no cover
        try:
            return self.rx_q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:  # pragma: no cover
        self.iface.close()


# ---- UDP bench link -----------------------------------------------------------------------------
def _hostport(s: str) -> tuple:
    host, port = s.rsplit(":", 1)
    return host, int(port)


class UdpTransport(Transport):
    """Frames as UDP datagrams: lets eagle_node and nest_station talk without radios.
    RSSI = announced TX power - ``path_loss_db``; nothing is ever sent over the air."""
    name = "udp"

    def __init__(self, bind: str = "127.0.0.1:47001", peer: str = "127.0.0.1:47002",
                 path_loss_db: float = 100.0, noise_figure_db: float = 6.0):
        super().__init__()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(_hostport(bind))
        self.peer = _hostport(peer)
        self.path_loss_db, self.nf = float(path_loss_db), float(noise_figure_db)

    def send(self, payload: bytes, tx_dbm: int) -> float:
        msg = json.dumps({"hex": payload.hex(), "dbm": int(tx_dbm), "sf": self.sf, "bw": self.bw_khz})
        self.sock.sendto(msg.encode(), self.peer)
        return self.airtime(len(payload))

    def recv(self, timeout: float | None = None) -> RxFrame | None:
        self.sock.settimeout(timeout)
        try:
            data, addr = self.sock.recvfrom(4096)
        except (socket.timeout, BlockingIOError):
            return None
        m = json.loads(data.decode())
        rssi = m["dbm"] - self.path_loss_db
        return RxFrame(bytes.fromhex(m["hex"]), rssi, rssi - noise_floor_dbm(self.bw_khz * 1e3, self.nf),
                       time.time(), f"{addr[0]}:{addr[1]}")

    def close(self) -> None:
        self.sock.close()


# ---- in-memory mock air --------------------------------------------------------------------------
@dataclass
class PathLossModel:
    """Log-distance model: PL(d) = PL(d0) + 10 n log10(d/d0) [+ N(0, shadow_db)]."""
    pl_d0_db: float = 31.7      # free space at 1 m, 915 MHz
    exponent: float = 3.0       # 2 = free space, ~2.7-3.5 light/medium foliage or clutter
    d0_m: float = 1.0
    shadow_db: float = 0.0
    extra_db: float = 0.0       # walls, body loss, antenna mismatch...

    def __call__(self, d_m: float, rng: np.random.Generator | None = None) -> float:
        pl = log_distance_path_loss(d_m, self.pl_d0_db, self.exponent, self.d0_m) + self.extra_db
        if self.shadow_db and rng is not None:
            pl += float(rng.normal(0.0, self.shadow_db))
        return pl


@dataclass
class ScannerHit:
    t: float
    src: str
    n_bytes: int
    tx_dbm: int
    rx_dbm: float
    snr_db: float
    detected: bool
    airtime_s: float


class EnemyScanner:
    """A passive wideband listener (an SDR waterfall). It cannot decode; it only notices a
    burst whose in-band SNR (power over thermal noise in the LoRa bandwidth) reaches
    ``threshold_db``. LoRa receivers decode below the noise floor; energy detectors cannot."""

    def __init__(self, name: str, xyz, *, noise_figure_db: float = 8.0, threshold_db: float = 3.0,
                 antenna_gain_db: float = 0.0):
        self.name, self.xyz = name, np.asarray(xyz, dtype=float)
        self.nf, self.threshold_db, self.gain = noise_figure_db, threshold_db, antenna_gain_db
        self.hits: list = []

    def hear(self, t, src, n_bytes, tx_dbm, rx_dbm, bw_khz, airtime_s) -> ScannerHit:
        snr = rx_dbm + self.gain - noise_floor_dbm(bw_khz * 1e3, self.nf)
        hit = ScannerHit(t, src, n_bytes, tx_dbm, rx_dbm + self.gain, snr, snr >= self.threshold_db, airtime_s)
        self.hits.append(hit)
        return hit


@dataclass
class TxRecord:
    t: float
    src: str
    n_bytes: int
    tx_dbm: int
    airtime_s: float
    delivered: dict = field(default_factory=dict)   # node -> rssi (None = below sensitivity)


class MockChannel:
    """Shared simulated air. Nodes and scanners have ENU positions; every transmission is
    evaluated against every receiver with the path-loss model. A simulated clock advances
    by each frame's time on air (plus explicit ``advance`` calls)."""

    def __init__(self, path_loss: PathLossModel | None = None, *, freq_mhz: float = 915.0,
                 sf: int = 7, bw_khz: float = 125.0, cr: int = 5, noise_figure_db: float = 6.0,
                 seed: int = 0):
        self.path_loss = path_loss or PathLossModel()
        self.freq_mhz, self.sf, self.bw_khz, self.cr = freq_mhz, sf, bw_khz, cr
        self.nf = noise_figure_db
        self.rng = np.random.default_rng(seed)
        self.nodes: dict = {}
        self.scanners: list = []
        self.log: list = []
        self.clock = 0.0

    @property
    def noise_floor(self) -> float:
        return noise_floor_dbm(self.bw_khz * 1e3, self.nf)

    @property
    def sensitivity(self) -> float:
        return self.noise_floor + SNR_LIMIT_DB[self.sf]

    def add_node(self, name: str, xyz) -> "MockTransport":
        t = MockTransport(self, name, xyz)
        self.nodes[name] = t
        return t

    def add_scanner(self, scanner: EnemyScanner) -> EnemyScanner:
        self.scanners.append(scanner)
        return scanner

    def advance(self, seconds: float) -> None:
        self.clock += float(seconds)

    def distance(self, a, b) -> float:
        return float(np.linalg.norm(np.asarray(a, float) - np.asarray(b, float)))

    def transmit(self, src: "MockTransport", payload: bytes, tx_dbm: int) -> float:
        toa = time_on_air(len(payload), self.sf, self.bw_khz * 1e3, self.cr)
        rec = TxRecord(self.clock, src.name, len(payload), int(tx_dbm), toa)
        for name, node in self.nodes.items():
            if node is src:
                continue
            rssi = tx_dbm - self.path_loss(self.distance(src.xyz, node.xyz), self.rng)
            if rssi >= self.sensitivity:
                node.inbox.append(RxFrame(bytes(payload), rssi, rssi - self.noise_floor, self.clock + toa, src.name))
                rec.delivered[name] = rssi
            else:
                rec.delivered[name] = None
        for sc in self.scanners:
            rx = tx_dbm - self.path_loss(self.distance(src.xyz, sc.xyz), self.rng)
            sc.hear(self.clock, src.name, len(payload), int(tx_dbm), rx, self.bw_khz, toa)
        self.log.append(rec)
        self.clock += toa
        # simulated devices that answer by themselves (the tag) react after their delay
        for name, rssi in rec.delivered.items():
            node = self.nodes[name]
            if rssi is not None and node.responder is not None:
                resp = node.responder(node.inbox.pop())   # the responder consumes the frame
                if resp is not None:
                    reply, reply_dbm, delay_s = resp
                    self.advance(delay_s)
                    node.send(reply, reply_dbm)
        return toa


class MockTransport(Transport):
    name = "mock"

    def __init__(self, channel: MockChannel, name: str, xyz):
        super().__init__()
        self.channel, self.name, self.xyz = channel, name, np.asarray(xyz, dtype=float)
        self.inbox: deque = deque()
        # optional: callable(RxFrame) -> (payload, tx_dbm, delay_s) | None, for simulated tags
        self.responder = None
        self.configure(channel.freq_mhz, channel.sf, channel.bw_khz, channel.cr)

    def send(self, payload: bytes, tx_dbm: int) -> float:
        return self.channel.transmit(self, payload, tx_dbm)

    def recv(self, timeout: float | None = None) -> RxFrame | None:
        return self.inbox.popleft() if self.inbox else None


def make_transport(radio_cfg: dict, *, log=print, role: str = "eagle") -> Transport:
    """Build the transport named in ``radio.transport`` (serial | udp | meshtastic)."""
    kind = radio_cfg.get("transport", "serial")
    if kind == "serial":
        t: Transport = SerialTransport(radio_cfg.get("serial_port"), int(radio_cfg.get("baud", 115200)), log=log)
    elif kind == "udp":
        u = radio_cfg.get("udp", {})
        bind, peer = u.get("bind", "127.0.0.1:47001"), u.get("peer", "127.0.0.1:47002")
        if role == "nest":  # same config file on both ends: swap
            bind, peer = peer, bind
        t = UdpTransport(bind, peer, float(u.get("path_loss_db", 100.0)), float(radio_cfg.get("noise_figure_db", 6.0)))
    elif kind == "meshtastic":
        m = radio_cfg.get("meshtastic", {})
        t = MeshtasticTransport(m.get("dev_path"), int(m.get("channel_index", 0)),
                                m.get("destination", "^all"), int(m.get("hop_limit", 0)))
    else:
        raise LinkError(f"unknown transport {kind!r} (mock is only available inside the simulation)")
    t.configure(radio_cfg.get("freq_mhz", 915.0), radio_cfg.get("sf", 7), radio_cfg.get("bw_khz", 125),
                radio_cfg.get("cr", 5))
    return t
