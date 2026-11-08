"""Synthetic scenes for ``apps/eagle_runtime.py --sim``: the detections the sensors would report,
no hardware. Use them to check the whole chain (gate -> sealed FIX -> Nest -> ATAK) on the Pi
before the sensors are plugged in. Timelines of the quick scenes are compressed (he taps the code
after 40 s, not 10 min); the cough and unconscious scenes run at real mission length, so use
``--speed 0`` (as fast as possible) for them.

Each scene returns ``(scene(t) -> [(sensor, [LifeDetection])], duration_s, story, expected)`` with
``t`` in seconds since the start and positions relative to the perch.
"""
from __future__ import annotations

import math

import numpy as np

from . import protocol as P
from .fusion import LifeDetection, iso_cov


def _heat(t, xy, shape=None):
    d = LifeDetection("HEAT", t, xy=np.asarray(xy, float), cov=iso_cov(0.4), temp_c=31.0, source="thermal")
    d.shape = shape
    return d


def _az(xy, origin):
    d = np.asarray(xy, float) - origin
    return math.degrees(math.atan2(d[1], d[0]))


def _code(t, xy, origin, *, point=True, name="tap_2_3", az_err=0.0):
    return LifeDetection("SOUND", t, xy=np.asarray(xy, float) if point else None, cov=iso_cov(1.0) if point else None,
                         origin=origin, az_deg=_az(xy, origin) + az_err, sigma_az_deg=2.0, category="SIGNAL",
                         status_bits=P.ST_INJURED, source=f"code:{name}")


def _sound(t, cat, xy, origin):
    return LifeDetection("SOUND", t, xy=np.asarray(xy, float), cov=iso_cov(1.0), origin=origin,
                         az_deg=_az(xy, origin), sigma_az_deg=2.0, category=cat, source="sounds")


def make_scene(name: str, perch_xy=(0.0, 0.0)):
    o = np.asarray(perch_xy, float)[:2]
    him = o + [12.0, 5.0]

    def every(t, period):
        return abs(t / period - round(t / period)) < 1e-6

    if name == "night":
        taps = (40.0, 70.0, 100.0)

        def scene(t):
            out = []
            if every(t, 2.0):
                out.append(("thermal", [_heat(t, him, 0.8)]))
            if any(abs(t - x) < 1e-6 for x in taps):
                out.append(("audio", [_code(t, him, o)]))
            return out
        return scene, 300.0, "02:00: he lies 13 m away, warm, and taps the briefed code 3 times", \
            "SEND at ~100 s (code rule), second burst 30-120 s later"

    if name == "cough":
        coughs = (1200.0, 3000.0, 4800.0, 6600.0)

        def scene(t):
            out = [("thermal", [_heat(t, him)])] if every(t, 60.0) else []
            if any(abs(t - x) < 1e-6 for x in coughs):
                out.append(("audio", [_sound(t, "COUGH", him, o)]))
            return out
        return scene, 7200.0, "silent, injured: warm (faint shape) and coughs at 20, 50, 80, 110 min", \
            "SEND at ~80 min, like simulator v2 (run with --speed 0)"

    if name == "patrol":
        def scene(t):
            base = o + [-30.0 + 1.2 * t, 20.0]
            out = []
            if every(t, 2.0):
                out.append(("thermal", [_heat(t, base + [k * 3.0, 0.0], 0.9) for k in range(3)]))
            if every(t, 15.0):
                out.append(("audio", [_sound(t, "VOICE", base, o)]))
            return out
        return scene, 60.0, "three people walk past together, talking", "never SEND (group, moving)"

    if name == "unconscious":
        def scene(t):
            return [("thermal", [_heat(t, him, 0.8)])] if every(t, 60.0) else []
        return scene, 9000.0, "unconscious and silent: warm and still for 2.5 h, nothing else", \
            "never SEND: one modality (thermal), whatever P reaches (run with --speed 0)"

    if name == "bearing":
        far = o + [60.0, 50.0]
        times = [0.0, 40.0, 80.0] + [float(x) for x in range(120, 480, 60)]

        def scene(t):
            if any(abs(t - x) < 1e-6 for x in times):
                return [("audio", [_code(t, far, o, point=False, az_err=2.0 * (int(t // 40) % 2))])]
            return []
        return scene, 600.0, "14:00, thermal blind: the code is heard on one bearing, never seen", \
            "SEND at ~5 min: bearing-only report, circle over the ray"

    if name == "crowded":
        taps = (40.0, 70.0, 100.0)
        stranger = o + [-8.0, 6.0]

        def scene(t):
            out = []
            if every(t, 2.0):
                out.append(("thermal", [_heat(t, him, 0.8), _heat(t, stranger, 0.4)]))
            if any(abs(t - x) < 1e-6 for x in taps):
                out.append(("audio", [_code(t, him, o)]))
            return out
        return scene, 1200.0, "as night, but an unknown person sits 10 m from the perch", \
            "HOLD + REQUEST_RELOCATE; default mode sends after the 15 min injured hold, strict never"

    raise ValueError(f"unknown scene {name!r}; choose from {', '.join(SCENES)}")


SCENES = ("night", "cough", "patrol", "unconscious", "bearing", "crowded")
