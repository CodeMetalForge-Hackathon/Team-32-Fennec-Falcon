#!/usr/bin/env python3
"""Person boxes from the Hailo AI HAT+ on the Pi camera -> eagle vision JSON lines.

Runs the hailo-apps detection pipeline (detector + tracker on the Hailo, Picamera2 source) and
appends one line per frame (rate-limited) to ``life.vision.jsonl`` for apps/life_node.py::

    {"t": 1730000000.12, "boxes": [[x1, y1, x2, y2, score], ...], "w": 1280, "h": 720, "ids": [3, 7]}

``boxes`` are pixels in the ``w`` x ``h`` frame; ``ids`` are the hailotracker ids (extra key,
ignored by older readers). With ``--profile`` (see apps/enroll_target.py) each line also carries
``"identity": [[level, appearance, face], ...]`` per box from :mod:`eagle.identity`.

Setup on the Pi 5 (once, with internet): install hailo-apps (https://github.com/hailo-ai/hailo-apps,
its install.sh downloads the detection HEF), then run from its venv::

    source ~/hailo-apps/setup_env.sh
    python apps/hailo_vision.py --jsonl ~/eagle/vision.jsonl              # Pi camera, NoIR wide tuning
    python apps/hailo_vision.py --jsonl /tmp/v.jsonl --input video.mp4    # any hailo-apps --input

Every other flag goes to hailo-apps (``--arch``, ``--hef-path``, ``--width``/``--height``,
``--show-fps``, ...). ``--camera SENSOR`` (default: ``camera.sensor`` in ``--config``) picks the Pi camera
by sensor name, e.g. ``imx708`` (Camera Module 3) or ``imx708_wide_noir`` (Wide NoIR); see
eagle/camera.py. The tuning file follows the sensor (``--tuning-file auto``): libcamera's default for
imx708, ``imx708_wide_noir.json`` for the NoIR. Without a sensor the first camera is used with the NoIR
tuning, as before. ``--tuning-file FILE`` / ``none`` override it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

TUNING_DIRS = ("/usr/share/libcamera/ipa/rpi/pisp",        # Raspberry Pi 5
               "/usr/share/libcamera/ipa/rpi/vc4",         # Raspberry Pi 4 and earlier
               "/usr/local/share/libcamera/ipa/rpi/pisp",
               "/usr/local/share/libcamera/ipa/rpi/vc4")
DEFAULT_TUNING = "imx708_wide_noir.json"


def find_tuning_file(name: str = DEFAULT_TUNING, dirs=TUNING_DIRS) -> str | None:
    """Absolute path of a libcamera tuning file (``name`` may already be a path), or None."""
    if not name or name.lower() == "none":
        return None
    p = Path(name)
    if p.is_absolute():
        return str(p) if p.is_file() else None
    for d in dirs:
        q = Path(d) / name
        if q.is_file():
            return str(q)
    return None


def bbox_to_pixels(xmin: float, ymin: float, width: float, height: float, frame_w: int, frame_h: int) -> list:
    """hailo HailoBBox (normalized 0..1, may slightly overshoot) -> [x1, y1, x2, y2] pixels, clipped."""
    x1 = min(max(xmin, 0.0), 1.0) * frame_w
    y1 = min(max(ymin, 0.0), 1.0) * frame_h
    x2 = min(max(xmin + width, 0.0), 1.0) * frame_w
    y2 = min(max(ymin + height, 0.0), 1.0) * frame_h
    return [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)]


def vision_line(t: float, boxes: list, w: int, h: int, ids=None, identity=None) -> str:
    """One eagle vision JSON line (see eagle/vision.py)."""
    d = {"t": round(float(t), 3), "boxes": [[*b[:4], round(float(b[4]), 3)] for b in boxes], "w": int(w), "h": int(h)}
    if ids is not None:
        d["ids"] = [int(i) for i in ids]
    if identity is not None:
        d["identity"] = [[int(lv), round(float(a), 3), None if f is None else round(float(f), 3)]
                         for lv, a, f in identity]
    return json.dumps(d, separators=(",", ":"))


class RateLimitedWriter:
    """Appends lines to a file: frames with boxes at most ``rate_hz`` per second, empty frames only
    as a heartbeat every ``heartbeat_s`` (so an empty frame never takes a slot from a person)."""

    def __init__(self, path: str, rate_hz: float = 2.0, heartbeat_s: float = 5.0, clock=time.time):
        self.path = os.path.expanduser(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "a", encoding="utf-8", buffering=1)      # line-buffered: readers tail it
        self.min_dt = 1.0 / rate_hz if rate_hz > 0 else 0.0
        self.heartbeat = float(heartbeat_s)
        self.clock, self.last_boxes, self.last_any = clock, -1e18, -1e18

    def write(self, line: str, has_boxes: bool) -> bool:
        now = self.clock()
        if has_boxes and now - self.last_boxes < self.min_dt:
            return False
        if not has_boxes and now - self.last_any < self.heartbeat:
            return False
        self.f.write(line + "\n")
        self.last_any = now
        if has_boxes:
            self.last_boxes = now
        return True

    def close(self) -> None:
        self.f.close()


def split_args(argv):
    """-> (our args, remaining argv for hailo-apps). Adds ``--input rpi`` when no input is given."""
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--jsonl", default="~/eagle/vision.jsonl", help="file life_node reads (life.vision.jsonl)")
    ap.add_argument("--rate", type=float, default=2.0, help="max lines per second (default 2)")
    ap.add_argument("--min-score", type=float, default=0.3, help="drop person boxes below this score")
    ap.add_argument("--label", default="person", help="detector label to keep")
    ap.add_argument("--camera", default=None,
                    help="Pi camera sensor name, e.g. imx708 or imx708_wide_noir (default: camera.sensor in --config)")
    ap.add_argument("--tuning-file", default="auto",
                    help="libcamera tuning file name/path; auto = by sensor; none = libcamera's default")
    ap.add_argument("--profile", default=None, help="target_profile.npz from apps/enroll_target.py (identity)")
    ap.add_argument("--config", default=None, help="eagle config (identity thresholds); default example.yaml")
    ap.add_argument("--models-dir", default="~/eagle-models", help="apps/fetch_models.py output (detection HEF)")
    ap.add_argument("--snapshot", default=None, metavar="JPG",
                    help="also save the latest frame (person boxes drawn) to this JPEG, e.g. ~/eagle/cam_standard.jpg "
                         "(the field page shows it)")
    ap.add_argument("--snapshot-hz", type=float, default=1.0, help="snapshot rate (default 1 per second)")
    ap.add_argument("--watchdog-s", type=float, default=60.0,
                    help="exit (status 4) when no camera frame arrived for this long, so systemd restarts us; 0 = off")
    ap.add_argument("--headless", choices=["auto", "yes", "no"], default="auto",
                    help="no video window (fakesink). auto: headless when neither DISPLAY nor WAYLAND_DISPLAY is set "
                         "(systemd at boot)")
    ours, rest = ap.parse_known_args(argv)
    if not any(a in ("-i", "--input") or a.startswith("--input=") for a in rest):
        rest = ["--input", "rpi"] + rest
    if not any(a == "--hef-path" or a.startswith("--hef-path=") for a in rest):
        hef = local_detection_hef(ours.models_dir, arch=_arg_value(rest, "--arch"))
        if hef:                                   # use the fetched HEF so hailo-apps never downloads one
            rest += ["--hef-path", hef]
    return ours, rest


def _arg_value(argv, name: str) -> str | None:
    """Value of ``name VALUE`` or ``name=VALUE`` in argv (last one wins), else None."""
    val = None
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            val = argv[i + 1]
        elif a.startswith(name + "="):
            val = a.split("=", 1)[1]
    return val


class Snapshotter:
    """Latest camera frame as a JPEG at most ``hz`` times a second. Written to a temp file and renamed, so
    a reader (the field page) never sees a half-written image."""

    def __init__(self, path, hz: float = 1.0, clock=time.monotonic):
        self.path = Path(os.path.expanduser(str(path)))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.dt = 1.0 / float(hz) if hz and hz > 0 else float("inf")
        self.clock, self.last = clock, -float("inf")

    def due(self) -> bool:
        return self.clock() - self.last >= self.dt

    def save(self, rgb, boxes=(), levels=None) -> bool:
        """``boxes``: [x0, y0, x1, y1, score] pixels; ``levels``: identity match level per box (or None)."""
        import cv2
        self.last = self.clock()
        img = np.ascontiguousarray(np.asarray(rgb)[..., :3][..., ::-1])          # RGB -> BGR for OpenCV
        for k, b in enumerate(boxes):
            x0, y0, x1, y1 = (int(round(v)) for v in b[:4])
            lvl = int(levels[k]) if levels and k < len(levels) else 0
            color = (0, 0, 255) if lvl >= 2 else (0, 200, 255) if lvl == 1 else (0, 255, 0)
            cv2.rectangle(img, (x0, y0), (x1, y1), color, 2)
            label = f"{b[4]:.2f}" + (f" ID{lvl}" if lvl else "") if len(b) > 4 else ""
            if label:
                cv2.putText(img, label, (x0, max(12, y0 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
        ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            return False
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_bytes(jpg.tobytes())
        os.replace(tmp, self.path)
        return True


class FrameWatchdog:
    """Calls ``on_stall`` once if ``kick`` was not called for ``timeout_s`` (a hung camera pipeline otherwise
    stays 'running' while writing nothing)."""

    def __init__(self, timeout_s: float, on_stall, clock=time.monotonic):
        self.timeout, self.on_stall, self.clock = float(timeout_s), on_stall, clock
        self.last = clock()
        self.fired = False

    def kick(self) -> None:
        self.last = self.clock()

    def check(self) -> bool:
        if self.timeout > 0 and not self.fired and self.clock() - self.last > self.timeout:
            self.fired = True
            self.on_stall(self.clock() - self.last)
        return self.fired

    def start(self, every_s: float = 1.0):
        import threading

        def loop():
            while not self.check():
                time.sleep(every_s)
        threading.Thread(target=loop, daemon=True, name="frame-watchdog").start()
        return self


def is_headless(mode: str, env=None) -> bool:
    env = os.environ if env is None else env
    if mode in ("yes", "no"):
        return mode == "yes"
    return not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


def local_detection_hef(models_dir: str, arch: str | None = None) -> str | None:
    """The detection HEF from apps/fetch_models.py: the one for ``arch`` when given (a HEF compiled for
    another architecture does not load), else the only one if exactly one architecture was fetched."""
    d = Path(os.path.expanduser(models_dir)) / "hailo"
    if arch:
        p = d / arch.lower() / "yolov5m_wo_spp.hef"
        return str(p) if p.is_file() else None
    found = sorted(d.glob("*/yolov5m_wo_spp.hef")) if d.is_dir() else []
    return str(found[0]) if len(found) == 1 else None


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    ours, rest = split_args(argv)
    if any(a in ("-h", "--help") for a in argv):
        print(__doc__)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from eagle.camera import camera_num, configured_sensor, tuning_for
    from eagle.config import load_config
    full_cfg = load_config(ours.config)
    rpi_input = _arg_value(rest, "--input") in ("rpi", None) and _arg_value(rest, "-i") in ("rpi", None)
    sensor = ours.camera or configured_sensor(full_cfg)
    cam_num = None
    if sensor and rpi_input:
        cam_num = camera_num(sensor)                                # raises with the camera list if absent
        print(f"camera: {sensor} (libcamera camera {cam_num})", flush=True)
    wanted = tuning_for(sensor if rpi_input else None, ours.tuning_file)
    tuning = find_tuning_file(wanted) if wanted else None
    os.environ.pop("LIBCAMERA_RPI_TUNING_FILE", None)               # never inherit another camera's tuning
    if tuning:
        os.environ["LIBCAMERA_RPI_TUNING_FILE"] = tuning           # read by libcamera when Picamera2 opens
        print(f"libcamera tuning file: {tuning}", flush=True)
    elif wanted:
        print(f"WARNING: tuning file {wanted} not found in {TUNING_DIRS}; using libcamera's default "
              "(colours/exposure will be off on a NoIR module)", flush=True)
    else:
        print("libcamera tuning file: libcamera's default for the sensor", flush=True)

    os.environ.setdefault("GST_PLUGIN_FEATURE_RANK", "vaapidecodebin:NONE")
    import gi                                                       # noqa: E402  (Pi only)
    gi.require_version("Gst", "1.0")
    import hailo                                                    # noqa: E402
    from hailo_apps.python.core.common.buffer_utils import get_caps_from_pad, get_numpy_from_buffer
    from hailo_apps.python.core.common.core import get_pipeline_parser
    from hailo_apps.python.core.gstreamer.gstreamer_app import app_callback_class
    from hailo_apps.python.pipeline_apps.detection.detection_pipeline import GStreamerDetectionApp

    matcher = None
    if ours.profile:
        from eagle.identity import IdentityMatcher, TargetProfile, make_runtime_embedders
        cfg = full_cfg.get("identity", {})
        matcher = IdentityMatcher(TargetProfile.load(ours.profile), make_runtime_embedders(cfg), cfg)
        print(f"identity: matching against {ours.profile}", flush=True)

    writer = RateLimitedWriter(ours.jsonl, ours.rate)
    print(f"writing person boxes to {writer.path}", flush=True)
    snap = Snapshotter(ours.snapshot, ours.snapshot_hz) if ours.snapshot else None
    if snap:
        print(f"snapshots ({ours.snapshot_hz:g}/s) to {snap.path}", flush=True)

    class UserData(app_callback_class):
        pass

    def stalled(dt):
        print(f"watchdog: no camera frame for {dt:.0f} s - exiting so the service restarts", flush=True)
        writer.close()
        os._exit(4)                                                 # the GStreamer main loop may be stuck
    watchdog = FrameWatchdog(ours.watchdog_s, stalled).start()

    def callback(element, buffer, user_data):
        if buffer is None:
            return
        watchdog.kick()
        fmt, w, h = get_caps_from_pad(element.get_static_pad("src"))
        if not w or not h:
            return
        boxes, ids = [], []
        for det in hailo.get_roi_from_buffer(buffer).get_objects_typed(hailo.HAILO_DETECTION):
            if det.get_label() != ours.label or det.get_confidence() < ours.min_score:
                continue
            bb = det.get_bbox()
            boxes.append(bbox_to_pixels(bb.xmin(), bb.ymin(), bb.width(), bb.height(), w, h)
                         + [det.get_confidence()])
            uid = det.get_objects_typed(hailo.HAILO_UNIQUE_ID)
            ids.append(uid[0].get_id() if len(uid) == 1 else -1)
        identity, frame = None, None
        if matcher is not None and boxes:
            frame = get_numpy_from_buffer(buffer, fmt, w, h)            # RGB
            identity = [matcher.update(tid, frame, b[:4]) for tid, b in zip(ids, boxes)]
        writer.write(vision_line(time.time(), boxes, w, h, ids, identity), has_boxes=bool(boxes))
        if snap is not None and snap.due():
            try:
                snap.save(get_numpy_from_buffer(buffer, fmt, w, h) if frame is None else frame, boxes,
                          [lv for lv, _, _ in identity] if identity else None)
            except Exception as e:                                  # never let a snapshot stop detection
                print(f"snapshot error: {type(e).__name__}: {e}", flush=True)

    if is_headless(ours.headless):
        # hailo-apps ends the pipeline in autovideosink and ignores --no-display for GStreamer apps; at boot
        # with no display server that sink can fail or grab the HDMI output. GStreamerApp reads this global
        # in __init__, before the pipeline string is built.
        import hailo_apps.python.core.gstreamer.gstreamer_app as gst_app
        gst_app.GST_VIDEO_SINK = "fakesink"
        print("headless: video sink fakesink", flush=True)
    if cam_num is not None:
        # hailo-apps opens Picamera2() (camera 0) in picamera_thread; give it the selected camera instead.
        import functools
        import hailo_apps.python.core.gstreamer.gstreamer_app as gst_app
        gst_app.Picamera2 = functools.partial(gst_app.Picamera2, cam_num)
    parser = get_pipeline_parser()
    sys.argv = [sys.argv[0]] + rest                                 # hailo-apps parses sys.argv itself
    user_data = UserData()
    user_data.use_frame = False
    try:
        GStreamerDetectionApp(callback, user_data, parser).run()
    finally:
        writer.close()


if __name__ == "__main__":
    main()
