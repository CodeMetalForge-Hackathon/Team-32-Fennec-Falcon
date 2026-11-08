import pytest

from eagle.airtime import (airtime_table, format_table, low_data_rate_opt, sensitivity_dbm, symbol_time,
                           time_on_air)

# Semtech AN1200.13 "LoRa Modem Designer's Guide", Tables 2-4: 10-byte payload, 8-symbol
# preamble, explicit header, CRC off (the values reproduce exactly with these settings).
AN1200_13 = [
    # sf, bw_hz, cr, ms
    (10, 125e3, 6, 264.2),
    (10, 250e3, 6, 132.1),
    (10, 500e3, 6, 66.0),
    (12, 250e3, 6, 528.4),
    (8, 250e3, 6, 39.2),
    (10, 250e3, 5, 123.9),
    (10, 250e3, 8, 148.5),
]

# The Things Network forum "Fair Use Policy explained" (EU868, 125 kHz, CR 4/5, CRC on):
# application payload + 13 bytes LoRaWAN overhead = PHY payload.
TTN = [
    (7, 8 + 13, 56.58),
    (12, 8 + 13, 1482.75),
    (7, 55 + 13, 123.14),
    (12, 55 + 13, 2957.31),
]


@pytest.mark.parametrize("sf,bw,cr,ms", AN1200_13)
def test_semtech_design_guide_values(sf, bw, cr, ms):
    assert time_on_air(10, sf, bw, cr, crc=False) * 1e3 == pytest.approx(ms, abs=0.06)


@pytest.mark.parametrize("sf,pl,ms", TTN)
def test_ttn_reference_values(sf, pl, ms):
    assert time_on_air(pl, sf, 125e3, 5) * 1e3 == pytest.approx(ms, abs=0.006)


def test_low_data_rate_optimisation_rule():
    assert low_data_rate_opt(11, 125e3) and low_data_rate_opt(12, 125e3)
    assert not low_data_rate_opt(10, 125e3) and not low_data_rate_opt(12, 500e3)
    assert symbol_time(7, 125e3) == pytest.approx(1.024e-3)


def test_fix_burst_airtime():
    assert time_on_air(48, 7, 125e3, 5) * 1e3 == pytest.approx(97.536, abs=1e-3)
    assert time_on_air(20, 7, 125e3, 5) * 1e3 == pytest.approx(56.576, abs=1e-3)


def test_table_shape_and_monotonic():
    rows = airtime_table()
    assert [r[0] for r in rows] == ["SF7/BW125", "SF7/BW250", "SF7/BW500", "SF9/BW125", "SF12/BW125"]
    for _, vals in rows:
        assert len(vals) == 4 and vals == sorted(vals)
    assert "225 B" in format_table()


@pytest.mark.parametrize("sf,bw,dbm", [(10, 125e3, -132), (10, 250e3, -129), (10, 500e3, -126),
                                       (12, 250e3, -134), (8, 250e3, -124)])
def test_sensitivity_matches_design_guide(sf, bw, dbm):
    assert sensitivity_dbm(sf, bw) == pytest.approx(dbm, abs=0.5)


def test_bad_arguments():
    with pytest.raises(ValueError):
        time_on_air(10, 13)
    with pytest.raises(ValueError):
        time_on_air(10, 7, cr=1)
    with pytest.raises(ValueError):
        time_on_air(300, 7)
