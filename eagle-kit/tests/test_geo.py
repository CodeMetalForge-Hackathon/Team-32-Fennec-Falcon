import importlib.util

import pytest

from eagle.geo import LocalFrame, haversine_m, radii

LAT0, LON0, H0 = 42.3550, -71.0655, 10.0


@pytest.mark.parametrize("use", [False, True])
def test_roundtrip(use):
    if use and importlib.util.find_spec("pymap3d") is None:
        pytest.skip("pymap3d not installed")
    f = LocalFrame(LAT0, LON0, H0, use_pymap3d=use)
    for e, n, u in [(0, 0, 0), (120.5, -80.25, 3.0), (-900, 1200, -5)]:
        lat, lon, h = f.to_geodetic(e, n, u)
        e2, n2, u2 = f.to_enu(lat, lon, h)
        assert abs(e2 - e) < 1e-6 and abs(n2 - n) < 1e-6 and abs(u2 - u) < 1e-6


def test_small_area_matches_great_circle():
    f = LocalFrame(LAT0, LON0, H0, use_pymap3d=False)
    lat, lon, _ = f.to_geodetic(0, 1000, 0)
    assert abs(haversine_m(LAT0, LON0, lat, lon) - 1000) < 3.0     # sphere vs ellipsoid
    M, _ = radii(LAT0)
    assert 111_000 < M * 3.141592653589793 / 180 < 111_200          # ~111.1 km per degree here


def test_approximation_vs_pymap3d():
    pytest.importorskip("pymap3d")
    exact = LocalFrame(LAT0, LON0, H0, use_pymap3d=True)
    approx = LocalFrame(LAT0, LON0, H0, use_pymap3d=False)
    for e, n in [(1000, 0), (0, 1000), (-700, 700)]:
        a = exact.to_geodetic(e, n, 0)
        b = approx.to_geodetic(e, n, 0)
        # horizontal disagreement in metres
        assert haversine_m(a[0], a[1], b[0], b[1]) < 0.10      # ~7 cm at 1 km
