"""Passive radio presence: Bluetooth LE advertisements -> range-only RF detections.

PRIVACY RULE (the venue is full of other people's phones)
---------------------------------------------------------
Only devices on the configured allowlist (exact address, or a name substring) are reported
or stored. Every other advertisement is reduced, inside :meth:`RfScanner.scan`, to a single
anonymous integer count: its address, name, RSSI and payload are never printed, logged,
stored, hashed or returned. The raw scan result exists only as a local variable of that one
call. Keep it that way when you change this file.

Range: log-distance model ``RSSI(d) = rssi_1m - 10 n log10(d)`` (``rssi_1m`` and ``n`` from the
config). Body loss, multipath and antenna orientation make BLE RSSI swing by ~5-10 dB, so the
range carries a wide 1-sigma: d * ln(10) * shadow_db / (10 n) (about 70 % of d at 6 dB, n = 2).
These are range-only rings around the perch for :mod:`eagle.fusion`.

Sources: :class:`BleakSource` (``pip install bleak``; ``BleakScanner.discover(timeout,
return_adv=True)`` returns ``{address: (BLEDevice, AdvertisementData)}``) and
:class:`SimRfSource`. Wi-Fi probe-request sniffing is only a documented stub
(:class:`WifiProbeStub`): it needs a monitor-mode adapter, collects bystanders' MAC addresses
by design, and may be restricted by law and by the event rules.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Advert:
    """One advertisement as a source yields it. Only ever lives inside RfScanner.scan()."""
    address: str
    name: str | None
    rssi: float


@dataclass
class RfHit:
    """An allowlisted device: safe to log, store and send."""
    label: str                        # configured label (or the allowlisted address)
    rssi: float
    range_m: float
    sigma_range_m: float
    t: float = 0.0


def rssi_to_distance(rssi: float, rssi_1m: float = -59.0, exponent: float = 2.0) -> float:
    return float(10.0 ** ((rssi_1m - rssi) / (10.0 * exponent)))


def distance_sigma(d: float, exponent: float = 2.0, shadow_db: float = 6.0) -> float:
    return float(d * math.log(10.0) * shadow_db / (10.0 * exponent))


class Allowlist:
    """Allowlisted devices: ``[{address: "AA:BB:..", label: "TAG-7"}, {name: "EAGLE-TAG", label: ..}]``.
    Address match is exact (case-insensitive); name match is a case-insensitive substring."""

    def __init__(self, entries=()):
        self.by_addr, self.by_name = {}, []
        for e in entries or ():
            if isinstance(e, str):
                e = {"address": e} if e.count(":") == 5 else {"name": e}
            lab = e.get("label")
            if e.get("address"):
                self.by_addr[str(e["address"]).upper()] = lab or str(e["address"]).upper()
            if e.get("name"):
                self.by_name.append((str(e["name"]).lower(), lab or str(e["name"])))

    def match(self, address: str, name: str | None) -> str | None:
        lab = self.by_addr.get(str(address).upper())
        if lab:
            return lab
        if name:
            low = name.lower()
            for sub, lab in self.by_name:
                if sub and sub in low:
                    return lab
        return None

    def __repr__(self) -> str:
        return f"Allowlist({len(self.by_addr)} addresses, {len(self.by_name)} name patterns)"


class RfScanner:
    """Applies the allowlist to each scan (see the privacy rule in the module docstring)."""

    def __init__(self, allowlist: Allowlist, source, *, rssi_1m: float = -59.0, exponent: float = 2.0,
                 shadow_db: float = 6.0, log=print):
        self.allow, self.source = allowlist, source
        self.rssi_1m, self.n, self.shadow = float(rssi_1m), float(exponent), float(shadow_db)
        self.log = log or (lambda *a: None)
        self.anonymous_last = 0          # non-allowlisted devices in the last scan (a count, nothing else)
        self.scans = 0

    def scan(self, t: float = 0.0) -> tuple:
        """-> ([RfHit] for allowlisted devices, anonymous count of all other devices)."""
        hits, anon = {}, 0
        for adv in self.source.scan():                    # raw adverts: local to this loop only
            label = self.allow.match(adv.address, adv.name)
            if label is None:
                anon += 1
                continue
            d = rssi_to_distance(adv.rssi, self.rssi_1m, self.n)
            h = RfHit(label, float(adv.rssi), d, distance_sigma(d, self.n, self.shadow), t)
            if label not in hits or h.rssi > hits[label].rssi:
                hits[label] = h
        self.anonymous_last = anon
        self.scans += 1
        out = sorted(hits.values(), key=lambda h: h.label)
        self.log(f"RF      scan {self.scans}: {len(out)} allowlisted "
                 f"({', '.join(f'{h.label} {h.rssi:.0f} dBm ~{h.range_m:.1f} m' for h in out) or 'none'}), "
                 f"{anon} other device(s) counted anonymously")
        return out, anon

    def detections(self, hits, perch_xy, t: float) -> list:
        from .fusion import LifeDetection
        return [LifeDetection("RF", t, origin=np.asarray(perch_xy, float)[:2], range_m=h.range_m,
                              sigma_range_m=h.sigma_range_m, rf_id=h.label, source="ble") for h in hits]


class BleakSource:
    """One passive BLE scan per call through ``bleak`` (Linux/BlueZ on the Pi, macOS, Windows)."""

    def __init__(self, timeout_s: float = 4.0):
        try:
            import bleak  # type: ignore  # noqa: F401
        except ImportError as e:
            raise RuntimeError("pip install bleak (Pi: BlueZ is preinstalled on Raspberry Pi OS)") from e
        self.timeout = float(timeout_s)

    def scan(self):  # pragma: no cover - needs a radio
        import asyncio

        from bleak import BleakScanner  # type: ignore

        async def run():
            return await BleakScanner.discover(timeout=self.timeout, return_adv=True)
        found = asyncio.run(run())
        for address, (dev, adv) in found.items():
            yield Advert(address, adv.local_name or dev.name, float(adv.rssi))


class SimRfSource:
    """Simulated BLE world: devices at ENU positions, RSSI from the same log-distance model
    plus shadowing; ``perch`` is the scanner position."""

    def __init__(self, devices, perch_xyz, *, rssi_1m: float = -59.0, exponent: float = 2.0,
                 shadow_db: float = 4.0, seed: int = 0):
        self.devices = list(devices)                 # [(address, name, xyz)]
        self.perch = np.asarray(perch_xyz, float)
        self.rssi_1m, self.n, self.shadow = rssi_1m, exponent, shadow_db
        self.rng = np.random.default_rng(seed)

    def scan(self):
        for addr, name, xyz in self.devices:
            d = max(0.3, float(np.linalg.norm(np.asarray(xyz, float) - self.perch)))
            rssi = self.rssi_1m - 10 * self.n * np.log10(d) + self.rng.normal(0, self.shadow)
            yield Advert(addr, name, float(rssi))


class WifiProbeStub:
    """NOT IMPLEMENTED on purpose. Passive Wi-Fi probe sniffing would need a monitor-mode
    adapter (e.g. ``iw dev wlan1 set monitor none``) and a packet library, would collect every
    bystander's (often randomised) MAC address, and may be restricted by local law and the
    event rules. If you build it, apply the same allowlist-or-count rule as :class:`RfScanner`."""

    def scan(self):
        raise NotImplementedError("Wi-Fi probe sniffing is a documented stub (see class docstring)")


def make_rf_scanner(rcfg: dict, *, sim_devices=None, perch_xyz=(0, 0, 0), log=print, seed: int = 0):
    """``life.rf`` config -> RfScanner (source: bleak | sim)."""
    allow = Allowlist(rcfg.get("allowlist", []))
    kw = dict(rssi_1m=float(rcfg.get("rssi_1m_dbm", -59.0)), exponent=float(rcfg.get("exponent", 2.0)))
    if rcfg.get("source", "bleak") == "sim" or sim_devices is not None:
        src = SimRfSource(sim_devices or [], perch_xyz, seed=seed, **kw)
    else:
        src = BleakSource(float(rcfg.get("scan_s", 4.0)))
    return RfScanner(allow, src, shadow_db=float(rcfg.get("shadow_db", 6.0)), log=log, **kw)
