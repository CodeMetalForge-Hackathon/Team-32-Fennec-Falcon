"""Local ENU metres <-> WGS84 latitude/longitude around a fixed origin.

Uses pymap3d (exact ECEF-based conversion) when it is installed; otherwise a local
tangent-plane approximation with the WGS84 meridian / prime-vertical radii of curvature,
which agrees with the exact conversion to a few centimetres over ~1 km.
"""
from __future__ import annotations

import math

try:  # optional
    import pymap3d as _pm  # type: ignore
except Exception:  # pragma: no cover - depends on environment
    _pm = None

WGS84_A = 6378137.0
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)


def radii(lat_deg: float) -> tuple:
    """(meridian radius M, prime-vertical radius N) in metres at latitude ``lat_deg``."""
    s = math.sin(math.radians(lat_deg))
    w = 1.0 - WGS84_E2 * s * s
    return WGS84_A * (1.0 - WGS84_E2) / w ** 1.5, WGS84_A / math.sqrt(w)


class LocalFrame:
    """ENU frame anchored at (lat0, lon0, h0); h is height above the WGS84 ellipsoid."""

    def __init__(self, lat0: float, lon0: float, h0: float = 0.0, use_pymap3d: bool | None = None):
        self.lat0, self.lon0, self.h0 = float(lat0), float(lon0), float(h0)
        self.use_pymap3d = (_pm is not None) if use_pymap3d is None else (use_pymap3d and _pm is not None)
        self._M, self._N = radii(self.lat0)

    def to_geodetic(self, e: float, n: float, u: float = 0.0) -> tuple:
        """ENU metres -> (lat_deg, lon_deg, h_m)."""
        if self.use_pymap3d:
            lat, lon, h = _pm.enu2geodetic(e, n, u, self.lat0, self.lon0, self.h0)
            return float(lat), float(lon), float(h)
        lat = self.lat0 + math.degrees(n / (self._M + self.h0))
        lon = self.lon0 + math.degrees(e / ((self._N + self.h0) * math.cos(math.radians(self.lat0))))
        return lat, lon, self.h0 + u

    def to_enu(self, lat: float, lon: float, h: float | None = None) -> tuple:
        """(lat_deg, lon_deg, h_m) -> ENU metres."""
        h = self.h0 if h is None else h
        if self.use_pymap3d:
            e, n, u = _pm.geodetic2enu(lat, lon, h, self.lat0, self.lon0, self.h0)
            return float(e), float(n), float(u)
        n = math.radians(lat - self.lat0) * (self._M + self.h0)
        e = math.radians(lon - self.lon0) * (self._N + self.h0) * math.cos(math.radians(self.lat0))
        return e, n, h - self.h0


def enu_to_geodetic(e, n, u, lat0, lon0, h0=0.0, use_pymap3d=None):
    return LocalFrame(lat0, lon0, h0, use_pymap3d).to_geodetic(e, n, u)


def geodetic_to_enu(lat, lon, h, lat0, lon0, h0=0.0, use_pymap3d=None):
    return LocalFrame(lat0, lon0, h0, use_pymap3d).to_enu(lat, lon, h)


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance on a 6371 km sphere (sanity checks only)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(a))
