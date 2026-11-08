import numpy as np
import pytest

from eagle.doa import unit_vector, vector_to_az_el, wrap180
from eagle.locate import (Bearing, body_to_world, circular_error, compass_bearing, dem_fix, dem_height,
                          error_ellipse, fox_fix, fox_fix_covariance, fuse_bearings, heading_to_yaw,
                          range_error)


def test_fox_fix_geometry():
    f = fox_fix(0.0, -45.0, [0, 0, 10])
    assert np.allclose(f.xyz, [10, 0, 0]) and abs(f.ground_range - 10) < 1e-9
    f = fox_fix(90.0, -30.0, [5, 5, 10])
    assert np.allclose(f.xyz, [5, 5 + 10 / np.tan(np.radians(30)), 0])
    f = fox_fix(180.0, -45.0, [0, 0, 10], source_height=2.0, floor_z=1.0)
    assert np.allclose(f.xyz, [-7, 0, 3])
    assert abs(f.slant_range - np.hypot(7, 7)) < 1e-9


def test_fox_fix_refuses_shallow_or_upward():
    with pytest.raises(ValueError):
        fox_fix(0.0, -1.0, [0, 0, 10])
    with pytest.raises(ValueError):
        fox_fix(0.0, 5.0, [0, 0, 10])
    with pytest.raises(ValueError):
        fox_fix(0.0, -30.0, [0, 0, 0.2], source_height=0.5)


def test_range_error_matches_derivative():
    h, th, dth = 8.0, 25.0, 0.5
    r = lambda t: h / np.tan(np.radians(t))
    numeric = abs(r(th + dth) - r(th - dth)) / 2
    assert abs(range_error(h, th, dth) - numeric) / numeric < 0.01
    assert abs(range_error(10, 45, 1) - 10 * np.radians(1) / 0.5) < 1e-12


def test_fox_fix_covariance_axes():
    f = fox_fix(0.0, -30.0, [0, 0, 10])
    cov = fox_fix_covariance(f, 1.0, 2.0)
    s_r = range_error(10, 30, 2.0)
    s_c = f.ground_range * np.radians(1.0)
    assert np.allclose(np.diag(cov), [s_r ** 2, s_c ** 2])
    a, b, ang = error_ellipse(cov)
    assert abs(a - np.sqrt(5.991464547107979) * s_r) < 1e-9 and abs(ang % 180) < 1e-6
    assert abs(circular_error(cov) - s_r) < 1e-9


def test_fuse_bearings_known_crossing():
    fx = fuse_bearings([Bearing(0, 0, 45.0, 1.0), Bearing(10, 0, 135.0, 1.0)])
    assert fx.ok and np.allclose(fx.xy, [5, 5], atol=1e-9)
    assert np.allclose(fx.residuals_deg, 0, atol=1e-9)
    a, b, _ = fx.ellipse95
    assert a > 0 and b > 0 and np.all(np.linalg.eigvalsh(fx.cov) > 0)
    # three noisy bearings to a known target
    rng = np.random.default_rng(0)
    target = np.array([30.0, 40.0])
    stations = [(0, 0), (60, 0), (0, 80)]
    bs = []
    for x, y in stations:
        az, _ = vector_to_az_el([target[0] - x, target[1] - y, 0])
        bs.append(Bearing(x, y, az + rng.normal(0, 1.0), 1.0))
    fx = fuse_bearings(bs)
    assert np.linalg.norm(fx.xy - target) < 3.0
    # truth lies inside the 95 % ellipse in this example
    d = fx.xy - target
    assert d @ np.linalg.solve(fx.cov, d) < 5.991


def test_fuse_bearings_degenerate():
    assert not fuse_bearings([Bearing(0, 0, 0.0), Bearing(0, 10, 0.0)]).ok        # parallel
    fx = fuse_bearings([Bearing(0, 0, 45.0), Bearing(10, 0, 315.0)])               # cross behind
    assert not fx.ok
    with pytest.raises(ValueError):
        fuse_bearings([Bearing(0, 0, 45.0)])


def _ridge_dem():
    x = np.arange(0, 101, 1.0)
    prof = np.clip(10 - np.abs(x - 50) * 0.5, 0, None)       # 10 m ridge at x = 50, slopes 1:2
    return np.tile(prof, (41, 1))                             # rows = y 0..40


def test_dem_fix_on_ridge():
    dem = _ridge_dem()
    perch = [0.0, 20.0, 20.0]
    hit = dem_fix(perch, (0.0, -30.0), dem, (0.0, 0.0), 1.0)
    x = 35.0 / (0.5 + np.tan(np.radians(30)))                 # analytic: 20 - x tan30 = 0.5 (x - 30)
    assert hit is not None and abs(hit[0] - x) < 0.02 and abs(hit[1] - 20) < 1e-6
    assert abs(hit[2] - 0.5 * (x - 30)) < 0.02
    assert abs(dem_height(dem, (0, 0), 1.0, 40.0, 7.0) - 5.0) < 1e-9


def test_dem_fix_flat_equals_fox_fix_and_misses():
    flat = np.zeros((101, 101))
    perch = [50.0, 50.0, 8.0]
    hit = dem_fix(perch, unit_vector(30.0, -20.0), flat, (0, 0), 1.0, source_height=0.5)
    ff = fox_fix(30.0, -20.0, perch, source_height=0.5)
    assert np.allclose(hit, ff.xyz, atol=0.02)
    assert dem_fix(perch, (30.0, -1.0), flat, (0, 0), 1.0) is None              # leaves the grid
    with pytest.raises(ValueError):
        dem_fix([500.0, 0, 5], (0, -30), flat, (0, 0), 1.0)


def test_body_to_world_frames():
    # heading 0: array 0-deg mark points north
    az, el = body_to_world(0.0, -10.0, heading_deg=0.0)
    assert abs(compass_bearing(az) - 0.0) < 1e-9 and abs(el + 10) < 1e-9
    # left of the array (body az 90) when it faces east is north
    az, _ = body_to_world(90.0, 0.0, heading_deg=90.0)
    assert abs(compass_bearing(az)) < 1e-9 or abs(compass_bearing(az) - 360) < 1e-9
    assert abs(heading_to_yaw(90.0)) < 1e-12
    # az offset from the clap test
    az, _ = body_to_world(15.0, 0.0, heading_deg=0.0, az_offset_deg=15.0)
    assert abs(wrap180(compass_bearing(az))) < 1e-9
    # pitch up 10 deg lifts the forward direction
    _, el = body_to_world(0.0, 0.0, yaw_deg=0.0, pitch_deg=10.0)
    assert abs(el - 10.0) < 1e-9
    _, el = body_to_world(90.0, 0.0, yaw_deg=0.0, roll_deg=10.0)
    assert abs(el - 10.0) < 1e-9
