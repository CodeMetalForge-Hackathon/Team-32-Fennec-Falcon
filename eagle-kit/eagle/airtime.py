"""LoRa time on air (Semtech formula) and receiver sensitivity.

Time on air (SX127x datasheet / AN1200.13; identical to the SX126x formula for SF7-SF12):

    T_sym      = 2^SF / BW
    T_preamble = (n_preamble + 4.25) * T_sym
    n_payload  = 8 + max(ceil((8*PL - 4*SF + 28 + 16*CRC - 20*IH) / (4*(SF - 2*DE))) * (CR + 4), 0)
    T_packet   = T_preamble + n_payload * T_sym

PL = PHY payload bytes, CRC = 1 if the payload CRC is on, IH = 1 for implicit header,
DE = 1 when low-data-rate optimisation is on (mandatory when T_sym >= 16 ms, i.e.
SF11/SF12 at 125 kHz; RadioLib switches it on automatically by the same rule),
CR = 1..4 for coding rate 4/5..4/8 (this module takes the RadioLib-style denominator 5..8).
SX126x SF5/SF6 use 6.25 preamble symbols and no "+8" in the numerator (SX1261/2 datasheet).

Run ``python -m eagle.airtime`` for the table used in the README.
"""
from __future__ import annotations

import math

SNR_LIMIT_DB = {5: -2.5, 6: -5.0, 7: -7.5, 8: -10.0, 9: -12.5, 10: -15.0, 11: -17.5, 12: -20.0}


def symbol_time(sf: int, bw_hz: float) -> float:
    return (2 ** sf) / float(bw_hz)


def low_data_rate_opt(sf: int, bw_hz: float) -> bool:
    return symbol_time(sf, bw_hz) >= 0.016


def time_on_air(payload_len: int, sf: int = 7, bw_hz: float = 125e3, cr: int = 5, *,
                preamble_len: int = 8, explicit_header: bool = True, crc: bool = True,
                low_dr_opt: bool | None = None) -> float:
    """LoRa packet duration in seconds. ``cr`` is the denominator: 5 -> 4/5 ... 8 -> 4/8."""
    if not 5 <= sf <= 12:
        raise ValueError("SF must be 5..12")
    if not 5 <= cr <= 8:
        raise ValueError("cr is the coding-rate denominator 5..8 (4/5..4/8)")
    if not 0 <= payload_len <= 255:
        raise ValueError("payload must be 0..255 bytes")
    ts = symbol_time(sf, bw_hz)
    de = low_data_rate_opt(sf, bw_hz) if low_dr_opt is None else bool(low_dr_opt)
    ih = 0 if explicit_header else 1
    crv = cr - 4
    if sf >= 7:
        num = 8 * payload_len - 4 * sf + 28 + 16 * int(crc) - 20 * ih
        n_pre = preamble_len + 4.25
    else:  # SX126x SF5/SF6
        num = 8 * payload_len - 4 * sf + 20 + 16 * int(crc) - 20 * ih
        n_pre = preamble_len + 6.25
    n_payload = 8 + max(math.ceil(num / (4.0 * (sf - 2 * int(de)))) * (crv + 4), 0)
    return (n_pre + n_payload) * ts


def sensitivity_dbm(sf: int, bw_hz: float, noise_figure_db: float = 6.0) -> float:
    """Receiver sensitivity = thermal noise in BW + NF + demodulator SNR limit.

    With NF = 6 dB this reproduces the AN1200.13 tables (e.g. SF10/125 kHz -> -132 dBm).
    """
    return noise_floor_dbm(bw_hz, noise_figure_db) + SNR_LIMIT_DB[sf]


def noise_floor_dbm(bw_hz: float, noise_figure_db: float = 6.0) -> float:
    return -174.0 + 10.0 * math.log10(bw_hz) + noise_figure_db


TABLE_SETTINGS = [(7, 125), (7, 250), (7, 500), (9, 125), (12, 125)]
TABLE_SIZES = [20, 32, 48, 225]


def airtime_table(settings=TABLE_SETTINGS, sizes=TABLE_SIZES, cr: int = 5, preamble_len: int = 8):
    """Rows of (label, [ms for each size]) with explicit header and CRC on."""
    rows = []
    for sf, bw in settings:
        rows.append((f"SF{sf}/BW{bw}", [1e3 * time_on_air(n, sf, bw * 1e3, cr, preamble_len=preamble_len)
                                        for n in sizes]))
    return rows


def format_table(settings=TABLE_SETTINGS, sizes=TABLE_SIZES, cr: int = 5) -> str:
    head = "| Setting | " + " | ".join(f"{n} B" for n in sizes) + " |"
    sep = "|---|" + "---|" * len(sizes)
    lines = [head, sep]
    for label, vals in airtime_table(settings, sizes, cr):
        lines.append(f"| {label} | " + " | ".join(f"{v:.1f} ms" for v in vals) + " |")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    print("LoRa time on air, CR 4/5, 8-symbol preamble, explicit header, CRC on")
    print(format_table())
