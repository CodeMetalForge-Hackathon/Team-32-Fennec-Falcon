"""Pinhole camera on the perch: pixel <-> ray <-> floor."""
import numpy as np
import pytest

from eagle.camgeo import Camera


@pytest.mark.parametrize("heading,tilt,roll", [(0, 35, 0), (30, 20, 0), (250, 60, 4), (123, 45, -7)])
def test_pixel_ground_round_trip(heading, tilt, roll):
    cam = Camera.from_fov(57, 160, 120, position=(2.0, -1.0, 3.0), heading_deg=heading, tilt_down_deg=tilt,
                          roll_deg=roll)
    rng = np.random.default_rng(0)
    u, v = rng.uniform(0, 159, 500), rng.uniform(0, 119, 500)
    g = cam.pixel_to_ground(u, v, ground_z=0.15)
    ok = np.all(np.isfinite(g), axis=1)
    assert ok.mean() > 0.5
    assert np.allclose(g[ok, 2], 0.15)
    uu, vv, front = cam.project(g[ok])
    assert front.all() and np.max(np.abs(uu - u[ok])) < 1e-6 and np.max(np.abs(vv - v[ok])) < 1e-6
    # and the other way: floor points -> pixels -> floor points
    pts = np.c_[rng.uniform(-3, 3, 50), rng.uniform(3, 9, 50), np.zeros(50)]
    pts[:, :2] = pts[:, :2] @ np.array([[np.cos(np.radians(-heading)), -np.sin(np.radians(-heading))],
                                        [np.sin(np.radians(-heading)), np.cos(np.radians(-heading))]]).T
    pts[:, :2] += [2.0, -1.0]
    pu, pv, f = cam.project(pts)
    back = cam.pixel_to_ground(pu[f], pv[f], 0.0)
    assert np.allclose(back, pts[f], atol=1e-6)


def test_centre_pixel_geometry():
    cam = Camera.from_fov(57, 160, 120, position=(0, 0, 3), heading_deg=0, tilt_down_deg=35)
    p = cam.pixel_to_ground(cam.cx, cam.cy)
    assert p[0] == pytest.approx(0.0, abs=1e-9) and p[1] == pytest.approx(3 / np.tan(np.radians(35)))
    right = cam.pixel_to_ground(159, cam.cy)
    assert right[0] > 0                      # image right = east when facing north
    assert cam.hfov_deg == pytest.approx(57) and cam.vfov_deg == pytest.approx(44.31, abs=0.01)
    assert not np.all(np.isfinite(cam.pixel_to_ground(cam.cx, 0.0 - 200)))   # above the horizon: no hit
    _, _, front = cam.project([0, -5, 0])
    assert not front                        # behind the camera


def test_footprint_and_covariance_grow_with_range():
    cam = Camera.from_fov(57, 160, 120, position=(0, 0, 3), heading_deg=0, tilt_down_deg=35)
    near, far = cam.pixel_ground_area(80, 110), cam.pixel_ground_area(80, 10)
    assert far > 5 * near > 0
    c_near, c_far = cam.ground_covariance(80, 110), cam.ground_covariance(80, 10)
    assert np.linalg.eigvalsh(c_far).max() > np.linalg.eigvalsh(c_near).max()
    s = cam.scaled(320, 240)
    assert np.allclose(s.pixel_to_ground(2 * 40 + 0.5, 2 * 30 + 0.5), cam.pixel_to_ground(40, 30), atol=1e-9)
