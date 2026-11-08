"""Eagle radio behaviour: poll the tag once, then ONE encrypted FIX burst at minimum power.

Used by apps/eagle_node.py with a real transport and by apps/sim_demo.py with the mock.
The Eagle never transmits except (a) one CHALLENGE when it wants to confirm a person and
(b) one FIX per detection. Both use the lowest power that closes the link.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from . import protocol as P
from .airtime import time_on_air
from .link import RxFrame, Transport


@dataclass
class PollResult:
    reply: P.Reply | None
    nonce: bytes
    counter: int
    tx_dbm: int
    path_loss_est: float
    airtime_s: float
    rx: RxFrame | None = None
    note: str = ""


@dataclass
class SendResult:
    frame: bytes
    tx_dbm: int
    airtime_s: float
    path_loss_est: float
    pl_source: str


class EagleRadio:
    def __init__(self, cfg: dict, transport: Transport, session: P.EagleSession, *, log=print,
                 clock=time.time):
        self.cfg, self.t, self.s, self.log, self.clock = cfg, transport, session, log, clock
        r = cfg.get("radio", {})
        self.sf, self.bw_khz, self.cr = int(r.get("sf", 7)), float(r.get("bw_khz", 125)), int(r.get("cr", 5))
        self.min_dbm, self.max_dbm = int(r.get("min_dbm", -9)), int(r.get("max_dbm", 22))
        self.margin = float(r.get("margin_db", 10.0))
        self.nf = float(r.get("noise_figure_db", 6.0))
        self.pl_model = r.get("path_loss", {})
        self.nest_link = r.get("nest_link", {})
        self.beacon_max_age = float(r.get("beacon_max_age_s", 300))
        self.poll_cfg = cfg.get("poll", {})
        self.force_dbm = r.get("force_dbm")          # demo only: fixed power instead of power control

    def _power(self, needed: int) -> tuple:
        if self.force_dbm is None:
            return needed, ""
        return int(min(max(int(self.force_dbm), self.min_dbm), self.max_dbm)), f" FORCED (control wanted {needed:+d})"

    # ---- link budget -----------------------------------------------------------------------
    @property
    def sensitivity(self) -> float:
        return P.lora_sensitivity(self.sf, self.bw_khz, self.nf)

    def model_path_loss(self, d_m: float) -> float:
        m = self.pl_model
        return P.log_distance_path_loss(d_m, m.get("pl_d0_db", 31.7), m.get("exponent", 3.0), m.get("d0_m", 1.0))

    def nest_path_loss(self) -> tuple:
        """(estimated path loss to the Nest, where it came from)."""
        age = self.clock() - self.s.nest_path_loss_t
        if self.s.nest_path_loss is not None and age <= self.beacon_max_age:
            return self.s.nest_path_loss, f"Nest beacon {age:.0f} s ago"
        if self.nest_link.get("path_loss_db") is not None:
            return float(self.nest_link["path_loss_db"]), "configured path loss"
        d = float(self.nest_link.get("distance_m", 1000.0))
        return self.model_path_loss(d), f"model at {d:.0f} m"

    def handle(self, rx: RxFrame) -> None:
        """Process an unsolicited frame (only Nest BEACONs matter to an Eagle)."""
        if rx.payload and rx.payload[0] == P.MSG_BEACON:
            try:
                pl = self.s.on_beacon(rx.payload, rx.rssi, self.clock())
                self.log(f"BEACON  from Nest, rssi {rx.rssi:.0f} dBm -> path loss {pl:.1f} dB")
            except P.ProtocolError as e:
                self.log(f"BEACON  rejected: {e}")

    def drain(self) -> None:
        while True:
            rx = self.t.recv(timeout=0)
            if rx is None:
                return
            self.handle(rx)

    # ---- tag poll -------------------------------------------------------------------------
    def poll_tag(self, est_range_m: float | None = None) -> PollResult:
        """Send ONE challenge at the power needed for ``est_range_m`` and wait for the reply."""
        window_ms = int(self.poll_cfg.get("reply_window_ms", 500))
        rng = est_range_m if est_range_m else float(self.poll_cfg.get("blind_range_m", 150.0))
        pl = self.model_path_loss(rng)
        tag_sens = self.poll_cfg.get("tag_sens_dbm") or self.sensitivity
        tx, forced = self._power(P.required_tx_dbm(tag_sens, pl, self.margin, self.min_dbm, self.max_dbm))
        frame, nonce = self.s.make_challenge(window_ms, tx)
        ctr = P.nonce_counter(nonce)
        toa = time_on_air(len(frame), self.sf, self.bw_khz * 1e3, self.cr)
        self.log(f"POLL    CHALLENGE ctr={ctr} {len(frame)} B @ {tx:+d} dBm{forced} (model PL {pl:.0f} dB at "
                 f"{rng:.0f} m), airtime {toa * 1e3:.1f} ms")
        toa = self.t.send(frame, tx)
        wait = window_ms / 1e3 + time_on_air(P.REPLY_LEN, self.sf, self.bw_khz * 1e3, self.cr) + 0.25
        t_end = time.monotonic() + wait
        while time.monotonic() < t_end:
            rx = self.t.recv(timeout=0.05)
            if rx is None:
                continue
            if rx.payload and rx.payload[0] == P.MSG_REPLY:
                try:
                    rep = self.s.verify_reply(rx.payload, nonce)
                except P.ProtocolError as e:
                    self.log(f"POLL    reply rejected: {e}")
                    continue
                self.log(f"POLL    REPLY tag {rep.tag_id} verified, rssi {rx.rssi:.0f} dBm, tag heard us at "
                         f"{rep.rssi_heard} dBm, battery {rep.battery if rep.battery <= 100 else '?'}%, "
                         f"status on air 0x{rep.status_wire:02x} (masked; only the Nest can read it)")
                return PollResult(rep, nonce, ctr, tx, pl, toa, rx)
            self.handle(rx)
        self.log("POLL    no valid reply")
        return PollResult(None, nonce, ctr, tx, pl, toa, None, "no reply")

    # ---- FIX ------------------------------------------------------------------------------
    def send_fix(self, body: P.FixBody, poll: PollResult | None = None) -> SendResult:
        if poll is not None and poll.reply is not None:
            body.cls |= P.CLS_AUTH
            body.tag_id = poll.reply.tag_id
            body.status = poll.reply.status_wire          # stays masked end to end
            body.chal_ctr16 = poll.counter & 0xFFFF
        frame = self.s.encrypt_fix(body)
        pl, src = self.nest_path_loss()
        tx, forced = self._power(P.required_tx_dbm(self.sensitivity, pl, self.margin, self.min_dbm, self.max_dbm))
        toa = self.t.send(frame, tx)
        self.log(f"TX      FIX {len(frame)} B @ {tx:+d} dBm{forced} SF{self.sf}/BW{self.bw_khz:g} CR4/{self.cr}, "
                 f"airtime {toa * 1e3:.1f} ms (path loss {pl:.1f} dB from {src}, margin {self.margin:.0f} dB)")
        return SendResult(frame, tx, toa, pl, src)

    # ---- LIFE (multi-person life list) -------------------------------------------------------
    def send_life(self, frames: list) -> list:
        """Send already-encrypted LIFE frames (one burst each) at the lowest power that reaches
        the Nest. Returns one :class:`SendResult` per frame."""
        out = []
        pl, src = self.nest_path_loss()
        for i, frame in enumerate(frames):
            tx, forced = self._power(P.required_tx_dbm(self.sensitivity, pl, self.margin, self.min_dbm, self.max_dbm))
            toa = self.t.send(frame, tx)
            self.log(f"TX      LIFE {i + 1}/{len(frames)} {len(frame)} B @ {tx:+d} dBm{forced} SF{self.sf}/BW{self.bw_khz:g} "
                     f"CR4/{self.cr}, airtime {toa * 1e3:.1f} ms (path loss {pl:.1f} dB from {src})")
            out.append(SendResult(frame, tx, toa, pl, src))
        return out
