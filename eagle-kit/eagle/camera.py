"""Pick a Pi camera by sensor name (libcamera's model string), not by port or index.

The tripod unit has two Camera Module 3 boards; which CSI port each sits in can change, the sensor
name does not:

    imx708            Camera Module 3, standard lens (66 deg HFOV), libcamera's default tuning
    imx708_wide_noir  Camera Module 3 Wide NoIR (102 deg HFOV), imx708_wide_noir.json tuning

Names match exactly ("imx708" never selects "imx708_wide_noir"). ``rpicam-hello --list-cameras``
prints them.
"""
from __future__ import annotations

# Tuning file per sensor; None = let libcamera choose (it picks imx708.json for the standard module).
TUNING = {
    "imx708": None,
    "imx708_wide": "imx708_wide.json",
    "imx708_noir": "imx708_noir.json",
    "imx708_wide_noir": "imx708_wide_noir.json",
}
LEGACY_TUNING = "imx708_wide_noir.json"     # what the apps used before camera selection existed


def camera_infos() -> list:
    """libcamera's camera list through Picamera2: [{"Num": 0, "Model": "imx708", ...}, ...]."""
    from picamera2 import Picamera2                 # apt install python3-picamera2
    return Picamera2.global_camera_info()


def camera_num(sensor: str, infos=None) -> int:
    """Index of the camera whose sensor model is exactly ``sensor``. Raises with the list if absent."""
    infos = camera_infos() if infos is None else infos
    for i in infos:
        if str(i.get("Model")) == sensor:
            return int(i["Num"])
    have = ", ".join(f"{i.get('Num')}: {i.get('Model')}" for i in infos) or "none"
    raise LookupError(f"no camera with sensor {sensor!r} (cameras: {have})")


def tuning_for(sensor: str | None, requested: str | None = "auto") -> str | None:
    """Tuning file name to use. ``requested`` 'auto' (or None) = by sensor; 'none' = libcamera's
    default; anything else = that file. With no sensor, 'auto' keeps the legacy NoIR tuning."""
    if requested is not None and str(requested).lower() not in ("auto", ""):
        return None if str(requested).lower() == "none" else str(requested)
    if not sensor:
        return LEGACY_TUNING
    return TUNING.get(sensor)


def configured_sensor(cfg: dict | None) -> str | None:
    """``camera.sensor`` from an eagle config, or None."""
    return ((cfg or {}).get("camera") or {}).get("sensor") or None
