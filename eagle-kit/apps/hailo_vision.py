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
``--show-fps``, ...). Camera Module 3 Wide NoIR: libcamera cannot detect the NoIR variant, so this
app points ``LIBCAMERA_RPI_TUNING_FILE`` at ``imx708_wide_noir.json`` before the camera opens
(``--tuning-file`` to override, ``--tuning-file none`` to leave libcamera's default).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

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
    ap.add_argument("--tuning-file", default=DEFAULT_TUNING, help="libcamera tuning file name/path, or none")
    ap.add_argument("--profile", default=None, help="target_profile.npz from apps/enroll_target.py (identity)")
    ap.add_argument("--config", default=None, help="eagle config (identity thresholds); default example.yaml")
    ap.add_argument("--models-dir", default="~/eagle-models", help="apps/fetch_models.py output (detection HEF)")
    ours, rest = ap.parse_known_args(argv)
    if not any(a in ("-i", "--input") or a.startswith("--input=") for a in rest):
        rest = ["--input", "rpi"] + rest
    if not any(a == "--hef-path" or a.startswith("--hef-path=") for a in rest):
        hef = local_detection_hef(ours.models_dir)
        if hef:                                   # use the fetched HEF so hailo-apps never downloads one
            rest += ["--hef-path", hef]
    return ours, rest


def local_detection_hef(models_dir: str) -> str | None:
    """The detection HEF from apps/fetch_models.py, if exactly one architecture was fetched."""
    d = Path(os.path.expanduser(models_dir)) / "hailo"
    found = sorted(d.glob("*/yolov5m_wo_spp.hef")) if d.is_dir() else []
    return str(found[0]) if len(found) == 1 else None


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    ours, rest = split_args(argv)
    if any(a in ("-h", "--help") for a in argv):
        print(__doc__)
    tuning = find_tuning_file(ours.tuning_file)
    if tuning:
        os.environ["LIBCAMERA_RPI_TUNING_FILE"] = tuning           # read by libcamera when Picamera2 opens
        print(f"libcamera tuning file: {tuning}", flush=True)
    elif ours.tuning_file and ours.tuning_file.lower() != "none":
        print(f"WARNING: tuning file {ours.tuning_file} not found in {TUNING_DIRS}; using libcamera's default "
              "(colours/exposure will be off on a NoIR module)", flush=True)

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
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from eagle.config import load_config
        from eagle.identity import IdentityMatcher, TargetProfile, make_runtime_embedders
        cfg = load_config(ours.config).get("identity", {})
        matcher = IdentityMatcher(TargetProfile.load(ours.profile), make_runtime_embedders(cfg), cfg)
        print(f"identity: matching against {ours.profile}", flush=True)

    writer = RateLimitedWriter(ours.jsonl, ours.rate)
    print(f"writing person boxes to {writer.path}", flush=True)

    class UserData(app_callback_class):
        pass

    def callback(element, buffer, user_data):
        if buffer is None:
            return
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
        identity = None
        if matcher is not None and boxes:
            frame = get_numpy_from_buffer(buffer, fmt, w, h)            # RGB
            identity = [matcher.update(tid, frame, b[:4]) for tid, b in zip(ids, boxes)]
        writer.write(vision_line(time.time(), boxes, w, h, ids, identity), has_boxes=bool(boxes))

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
