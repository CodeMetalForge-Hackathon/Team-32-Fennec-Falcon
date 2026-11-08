"""Nest (base station) logic: receive -> decrypt/verify -> CoT -> ATAK."""
from __future__ import annotations

import time

from . import protocol as P
from .config import load_keys
from .cot import build_cot, senders_from_config
from .geo import LocalFrame
from .link import RxFrame


class NestStation:
    def __init__(self, cfg: dict, *, senders=None, log=print, sim: bool = False):
        keys = load_keys(cfg)
        n = cfg.get("nest", {})
        if keys["fix_aes"] is None:
            raise ValueError("keys.fix_aes is required on the Nest")
        if keys["covert"] is None:
            log("WARNING keys.covert not set: tag status and DURESS cannot be read")
        self.session = P.NestSession(keys["fix_aes"], keys["covert"], window=int(n.get("replay_window", 64)),
                                     max_age_s=float(n.get("max_age_s", 600)), nest_id=int(n.get("id", 1)),
                                     beacon_counter=P.CounterStore(None if sim else n.get("state_file")))
        self.cot_cfg = cfg.get("cot", {})
        self.senders = senders_from_config(self.cot_cfg) if senders is None else senders
        self.log = log
        self.sim = sim
        self.fennec, self.bearings, self.markers = None, [], {}
        if cfg.get("fennec"):
            from .fennec import Mission, NestCodec
            try:
                self.fennec = NestCodec(Mission.from_config(cfg), window=int(n.get("replay_window", 64)))
            except Exception as e:
                log(f"WARNING Fennec reports disabled: {e}")

    def cot_for(self, rep: P.FixReport, rx: RxFrame | None = None) -> bytes:
        c = self.cot_cfg
        b = rep.body
        prefix = c.get("uid_prefix", "eagle")
        if rep.authenticated:
            uid, ctype = f"{prefix}-{rep.eagle_id}-tag{b.tag_id}", c.get("authenticated_type", "a-f-G-U-C")
            who = f"TAG {b.tag_id}"
        else:
            uid, ctype = f"{prefix}-{rep.eagle_id}-{rep.counter}", c.get("unconfirmed_type", "a-u-G")
            who = "UNCONFIRMED"
        flags = P.status_names(rep.status) if rep.status_known else ["status unknown (no covert key)"]
        if rep.duress:
            who += " DURESS"
        remarks = (f"Eagle {rep.eagle_id} {P.class_name(b.cls)} conf {b.confidence:.2f}; "
                   f"status: {', '.join(flags) or 'none'}; ce(1-sigma) {b.ce_m:.0f} m; "
                   f"fix time {time.strftime('%H:%M:%SZ', time.gmtime(b.t_unix))}")
        if rx is not None:
            remarks += f"; link rssi {rx.rssi:.0f} dBm snr {rx.snr:.1f} dB"
        how = "m-s" if (b.cls & P.CLS_SIM) else c.get("how", "m-f")
        return build_cot(uid, b.lat, b.lon, hae=b.alt_m, ce=max(b.ce_m, 1.0), cot_type=ctype, how=how,
                         stale_s=float(c.get("stale_s", 300)), callsign=f"SAR {who}", remarks=remarks)

    def handle(self, rx: RxFrame, now: float | None = None):
        """-> (FixReport, cot_xml) for a valid FIX, (LifeReport, [cot_xml, ...]) for a valid
        LIFE frame, else None (logged)."""
        if not rx.payload:
            return None
        from .fennec import is_fennec
        if is_fennec(rx.payload) and self.fennec is not None:
            return self.handle_fennec(rx, now)
        if rx.payload[0] == P.MSG_LIFE:
            return self.handle_life(rx, now)
        if rx.payload[0] != P.MSG_FIX:
            self.log(f"RX      ignored frame type 0x{rx.payload[0]:02x} ({len(rx.payload)} B, rssi {rx.rssi:.0f})")
            return None
        try:
            rep = self.session.decrypt_fix(rx.payload, now=now)
        except P.ProtocolError as e:
            self.log(f"RX      FIX REJECTED: {e}")
            return None
        b = rep.body
        st = ", ".join(P.status_names(rep.status)) if rep.status_known else "unknown"
        self.log(f"RX      FIX eagle {rep.eagle_id} seq {rep.counter}: {b.lat:.7f}, {b.lon:.7f} alt {b.alt_m:.0f} m "
                 f"ce {b.ce_m:.0f} m class {P.class_name(b.cls)} conf {b.confidence:.2f} tag {b.tag_id or '-'} "
                 f"status [{st}]{'  *** DURESS ***' if rep.duress else ''} (rssi {rx.rssi:.0f} dBm, snr {rx.snr:.1f} dB)")
        xml = self.cot_for(rep, rx)
        for s in self.senders:
            try:
                s.send(xml)
            except OSError as e:
                self.log(f"COT     send failed ({type(s).__name__}): {e}")
        return rep, xml

    # ---- Fennec Fix (15 B) ----------------------------------------------------------------------------
    def _send(self, xml: bytes) -> None:
        for s in self.senders:
            try:
                s.send(xml)
            except OSError as e:
                self.log(f"COT     send failed ({type(s).__name__}): {e}")

    def handle_fennec(self, rx: RxFrame, now: float | None = None):
        """-> (decoded dict, [cot_xml, ...]) for a valid 15-byte Fennec report, else None (logged).
        FIX / MOVED / TEST: a marker with ce = the 95 % radius (MOVED moves its Eagle's nearest marker).
        BEARING: a marker at the Eagle, and a crossing fix once another Eagle's BEARING crosses it."""
        from . import fennec as F
        now = time.time() if now is None else now
        try:
            d = self.fennec.open(rx.payload, now)
        except F.FennecError as e:
            self.log(f"RX      FENNEC REJECTED: {e}")
            return None
        self.log(f"RX      {F.describe(d)} (15 B, rssi {rx.rssi:.0f} dBm, snr {rx.snr:.1f} dB)")
        c = self.cot_cfg
        prefix = c.get("uid_prefix", "eagle")
        stale = float(c.get("stale_s", 300))
        xmls, node, kind = [], d["eagle_id"], d["type_name"]
        if kind == "BEARING":
            self.bearings = [b for b in self.bearings if now - b["rx_t"] <= 1800 and b["eagle_id"] != node] + [
                {**d, "rx_t": now}]
            xmls.append(build_cot(f"{prefix}-{node}-brg{d['counter']}", d["own_lat"], d["own_lon"], ce=50.0,
                                  cot_type=c.get("unconfirmed_type", "a-u-G"), how=c.get("how", "m-f"), stale_s=stale,
                                  callsign=f"SAR BEARING {d['az_deg']:.0f}", remarks=F.describe(d)))
            for other in self.bearings[:-1]:
                x = F.crossing(other, d, self.fennec.frame)
                if x is not None and x[2] >= 15.0:
                    lat, lon, ang = x
                    self.log(f"FUSE    BEARINGs from Eagles {other['eagle_id']} and {node} cross at {lat:.6f}, "
                             f"{lon:.6f} ({ang:.0f} deg apart)")
                    xmls.append(build_cot(f"{prefix}-x{other['eagle_id']}-{node}", lat, lon, ce=100.0,
                                          cot_type=c.get("unconfirmed_type", "a-u-G"), how="h-e", stale_s=stale,
                                          callsign="SAR CROSS FIX", remarks=f"two bearings {ang:.0f} deg apart"))
        elif kind == "STATUS":
            pass
        else:
            lat, lon = d["lat"], d["lon"]
            if kind == "COMPROMISED":
                self.log(f"ALERT   *** EAGLE {node} COMPROMISED: its mission key is being wiped ***")
                uid, cs = f"{prefix}-{node}-lost", f"EAGLE {node} COMPROMISED"
            else:
                mine = self.markers.setdefault(node, [])
                uid = f"{prefix}-{node}-f{d['counter']}"
                if kind == "MOVED" and mine:
                    import math
                    uid = min(mine, key=lambda m: math.hypot(m[1] - lat, (m[2] - lon) * math.cos(math.radians(lat))))[0]
                    mine[:] = [m for m in mine if m[0] != uid]
                mine.append((uid, lat, lon))
                face = " FACE" if d["state"] & F.ST_FACE else ""
                cs = f"SAR {kind}{face}"
            how = "m-s" if kind == "TEST" else c.get("how", "m-f")
            xmls.append(build_cot(uid, lat, lon, ce=max(float(d.get("err95_m", 50.0)), 1.0),
                                  cot_type=c.get("unconfirmed_type", "a-u-G"), how=how, stale_s=stale, callsign=cs,
                                  remarks=F.describe(d)))
        for xml in xmls:
            self._send(xml)
        return d, xmls

    def beacon(self, transport, tx_dbm: int) -> float:
        frame = self.session.make_beacon(tx_dbm)
        toa = transport.send(frame, tx_dbm)
        self.log(f"TX      BEACON {len(frame)} B @ {tx_dbm:+d} dBm, airtime {toa * 1e3:.1f} ms")
        return toa

    # ---- LIFE: multi-person life list -> one CoT marker per person -----------------------------
    def life_position(self, rep: P.LifeReport, p: P.LifePerson) -> tuple:
        """(lat, lon, hae) of a person record: perch lat/lon + ENU offset, floor height."""
        return LocalFrame(rep.head.lat, rep.head.lon, rep.head.floor_hae_m).to_geodetic(p.east_m, p.north_m, 0.0)

    def cot_for_person(self, rep: P.LifeReport, p: P.LifePerson, rx: RxFrame | None = None) -> bytes:
        c = self.cot_cfg
        prefix = c.get("uid_prefix", "eagle")
        auth = bool(p.evidence & P.EV_TAG and p.tag_id)
        ctype = c.get("authenticated_type", "a-f-G-U-C") if auth else c.get("unconfirmed_type", "a-u-G")
        flags = [n for b, n in ((P.LF_INJURED, "INJURED"), (P.LF_DURESS, "DURESS")) if p.flags & b]
        lat, lon, hae = self.life_position(rep, p)
        rates = []
        if p.breath_bpm:
            rates.append(f"breath {p.breath_bpm:.0f}/min")
        if p.heart_bpm:
            rates.append(f"heart {p.heart_bpm:.0f} bpm")
        remarks = (f"Eagle {rep.eagle_id} life list #{p.track_id}: {p.status_name}"
                   f"{' ' + ' '.join(flags) if flags else ''}; evidence: {', '.join(P.evidence_names(p.evidence)) or 'none'}"
                   f"{'; ' + ', '.join(rates) if rates else ''}; life score {p.score:.2f}; ce(1-sigma) {p.ce_m:.1f} m")
        if auth:
            remarks += f"; tag {p.tag_id}" + ("" if p.tag_status is not None else " (status unknown: no covert key)")
        if p.status == P.LS_RF_ONLY:
            remarks += "; radio device only - somewhere on a ring of radius ce around the perch"
        remarks += f"; report time {time.strftime('%H:%M:%SZ', time.gmtime(rep.head.t_unix))}"
        if rx is not None:
            remarks += f"; link rssi {rx.rssi:.0f} dBm snr {rx.snr:.1f} dB"
        who = f"#{p.track_id} {p.status_name}" + (" " + " ".join(flags) if flags else "")
        return build_cot(f"{prefix}-{rep.eagle_id}-p{p.track_id}", lat, lon, hae=hae, ce=max(p.ce_m, 1.0),
                         cot_type=ctype, how=c.get("how", "m-f"), stale_s=float(c.get("stale_s", 300)),
                         callsign=f"SAR {who}", remarks=remarks)

    def handle_life(self, rx: RxFrame, now: float | None = None):
        try:
            rep = self.session.decrypt_life(rx.payload, now=now)
        except P.ProtocolError as e:
            self.log(f"RX      LIFE REJECTED: {e}")
            return None
        h = rep.head
        self.log(f"RX      LIFE eagle {rep.eagle_id} seq {rep.counter} list {h.list_seq} part {h.chunk_idx + 1}/"
                 f"{h.chunk_count}: {h.n} person(s) around {h.lat:.7f}, {h.lon:.7f} (rssi {rx.rssi:.0f} dBm)")
        xmls = []
        for p in rep.persons:
            flags = [n for b, n in ((P.LF_INJURED, "INJURED"), (P.LF_DURESS, "DURESS")) if p.flags & b]
            self.log(f"RX        #{p.track_id:<3} {p.status_name:<10} E{p.east_m:+6.1f} N{p.north_m:+6.1f} "
                     f"ce {p.ce_m:4.1f} m  breath {p.breath_bpm or 0:3.0f} heart {p.heart_bpm or 0:3.0f}  "
                     f"[{','.join(P.evidence_names(p.evidence))}]{' tag ' + str(p.tag_id) if p.tag_id else ''}"
                     f"{'  *** ' + ' '.join(flags) + ' ***' if flags else ''}")
            xml = self.cot_for_person(rep, p, rx)
            xmls.append(xml)
            for s in self.senders:
                try:
                    s.send(xml)
                except OSError as e:
                    self.log(f"COT     send failed ({type(s).__name__}): {e}")
        return rep, xmls
