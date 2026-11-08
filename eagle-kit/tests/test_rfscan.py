"""BLE allowlist: strangers' devices must never leak, only be counted."""
import importlib.util

import numpy as np
import pytest

from eagle.fusion import LifeList
from eagle.rfscan import (Allowlist, BleakSource, RfScanner, SimRfSource, WifiProbeStub, distance_sigma,
                          rssi_to_distance)


def test_allowlist_never_leaks_other_identifiers():
    rng = np.random.default_rng(0)
    strangers = [(f"5A:{k:02X}:77:{rng.integers(0, 256):02X}:13:{k:02X}", f"Alice-Phone-{k}-secret", (k, 3, 1))
                 for k in range(25)] + [("66:66:66:66:66:66", None, (1, 1, 1))]
    ours = [("C0:FF:EE:00:00:42", "TEAM-RADIO-2", (4, 9, 0.3)), ("AA:BB:CC:DD:EE:01", "whatever", (2, 2, 0.3))]
    logs = []
    sc = RfScanner(Allowlist([{"name": "team-radio", "label": "RADIO-2"}, {"address": "aa:bb:cc:dd:ee:01",
                                                                           "label": "TAG-7"}]),
                   SimRfSource(strangers + ours, (0, 0, 3), seed=1), log=logs.append)
    hits, anon = sc.scan(10.0)
    assert anon == len(strangers) and sorted(h.label for h in hits) == ["RADIO-2", "TAG-7"]
    life = LifeList(perch_xy=(0, 0))
    life.update(sc.detections(hits, (0, 0), 10.0), now=10.0)
    life.rf_anonymous = anon
    blob = " ".join(logs) + repr(hits) + repr(vars(sc)) + repr(sc.allow) + life.summary(10.0) + \
        repr([vars(t) for t in life.tracks])
    for addr, name, _ in strangers:
        assert addr.upper() not in blob.upper()
        if name:
            assert name.lower() not in blob.lower()
    assert "AA:BB:CC:DD:EE:01" not in " ".join(logs)          # even ours are logged by label only
    assert f"{anon} non-allowlisted" in life.summary(10.0)


def test_rssi_model_and_wide_sigma():
    d = rssi_to_distance(-59 - 20 * np.log10(8.0), -59, 2.0)
    assert d == pytest.approx(8.0)
    assert distance_sigma(8.0, 2.0, 6.0) > 0.5 * 8.0


def test_stubs_and_missing_bleak():
    with pytest.raises(NotImplementedError):
        WifiProbeStub().scan()
    if importlib.util.find_spec("bleak") is None:
        with pytest.raises(RuntimeError, match="pip install bleak"):
            BleakSource()
