"""RockBLOCK 9603N (Iridium SBD) over its serial port: one mobile-originated message per call.

The relay design's last path: global, and the only one that confirms delivery (+SBDIX MO status
0-2 = the message reached the Iridium gateway). One 15-byte Fennec report costs one credit
(credits are per 50 bytes). AT command set per Iridium's ISU AT Command Reference; not run on
hardware here (tests drive it with a scripted serial port).
"""
from __future__ import annotations

import time


class IridiumError(RuntimeError):
    pass


class RockBlock:
    def __init__(self, port: str | None = None, baud: int = 19200, *, ser=None, timeout_s: float = 60.0,
                 attempts: int = 3, backoff_s: float = 10.0, log=print):
        if ser is None:
            import serial
            ser = serial.Serial(port, baud, timeout=1.0)
        self.ser, self.timeout_s, self.attempts, self.backoff_s, self.log = ser, timeout_s, attempts, backoff_s, log

    def _lines(self, until: tuple, timeout: float) -> list:
        out, t_end = [], time.monotonic() + timeout
        while time.monotonic() < t_end:
            raw = self.ser.readline()
            if not raw:
                continue
            line = raw.decode("ascii", "replace").strip()
            if not line:
                continue
            out.append(line)
            if line in until or line == "ERROR":
                return out
        raise IridiumError(f"modem timeout waiting for {until} (got {out})")

    def _cmd(self, cmd: str, until=("OK",), timeout: float = 5.0) -> list:
        self.ser.write((cmd + "\r").encode("ascii"))
        lines = self._lines(tuple(until), timeout)
        if lines[-1] == "ERROR":
            raise IridiumError(f"{cmd} -> ERROR")
        return lines

    def send(self, payload: bytes) -> dict:
        """Write the message to the MO buffer, then +SBDIX until the gateway takes it."""
        if not 1 <= len(payload) <= 340:
            raise IridiumError("SBD MO messages are 1-340 bytes")
        self._cmd("AT")
        self._cmd("AT&K0")                                   # no RTS/CTS on the 3-wire RockBLOCK cable
        self._cmd(f"AT+SBDWB={len(payload)}", until=("READY",))
        self.ser.write(payload + (sum(payload) & 0xFFFF).to_bytes(2, "big"))
        res = self._lines(("0", "1", "2", "3"), 5.0)
        if res[-1] != "0":
            raise IridiumError(f"SBDWB rejected the message ({res[-1]}: 1 timeout, 2 bad checksum, 3 bad size)")
        self._lines(("OK",), 5.0)
        last = None
        for k in range(1, self.attempts + 1):
            lines = self._cmd("AT+SBDIX", timeout=self.timeout_s)
            row = next((ln for ln in lines if ln.startswith("+SBDIX:")), None)
            if row is None:
                raise IridiumError(f"no +SBDIX response: {lines}")
            mo, momsn, mt, mtmsn, mtlen, mtq = (int(x) for x in row.split(":", 1)[1].split(","))
            last = {"ok": mo <= 2, "mo_status": mo, "momsn": momsn, "mt_status": mt, "mt_queued": mtq, "attempts": k}
            self.log(f"SBD     attempt {k}: MO status {mo} ({'reached the gateway' if mo <= 2 else 'not sent'}), "
                     f"MOMSN {momsn}, MT queued {mtq}")
            if mo <= 2:
                return last
            if k < self.attempts:
                time.sleep(self.backoff_s)
        return last

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass
