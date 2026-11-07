#!/usr/bin/env python3
"""Raspberry Pi: HTI / Hikvision thermal imager (USB 2BDF:0102) -> RTSP stream + raw temperature log.

Pipeline: V4L2 capture (raw 256x392 YUYV) -> degrees C -> colorized frame with min/max/center
overlay -> FFmpeg H.264 -> MediaMTX at rtsp://localhost:8554/thermal (MediaMTX re-serves it as
RTSP, WebRTC and HLS, and records it). Raw temperatures are saved separately because H.264 destroys them.

Frame layout (same as stream.py on Windows):
  rows   0-191  raw 16-bit little-endian counts, kelvin = raw / 16
  rows 192-195  metadata, rows 196-387 camera's own preview, rows 388-391 metadata

Run:  python3 thermal_rtsp.py               (auto-find camera, stream to MediaMTX)
      python3 thermal_rtsp.py --fake        (synthetic frames, for testing without the camera)
      python3 thermal_rtsp.py --check       (grab a few frames, print temps, exit)
"""
import argparse
import csv
import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

WIDTH, HEIGHT = 256, 392
SENSOR_H = 192
FRAME_BYTES = WIDTH * HEIGHT * 2
COUNTS_PER_KELVIN = 16.0
CAMERA_NAMES = ("UVC Camera", "HikCamera")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- capture
def candidate_devices():
    """V4L2 nodes whose name matches the thermal camera, then all others."""
    named, other = [], []
    for d in sorted(glob.glob("/sys/class/video4linux/video*"), key=lambda p: int(p.rsplit("video", 1)[1])):
        try:
            name = Path(d, "name").read_text().strip()
        except OSError:
            name = ""
        dev = "/dev/" + os.path.basename(d)
        (named if any(n in name for n in CAMERA_NAMES) else other).append(dev)
    return named + other


class OpenCVReader:
    def __init__(self, dev):
        self.cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            raise RuntimeError("open failed")
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUYV"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
        self.cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
        for _ in range(10):
            if self.read() is not None:
                return
        self.close()
        raise RuntimeError("no raw 256x392 frames")

    def read(self):
        ok, frame = self.cap.read()
        if not ok:
            return None
        b = frame.tobytes()
        return b if len(b) == FRAME_BYTES else None

    def close(self):
        self.cap.release()


class FFmpegReader:
    """Fallback: let FFmpeg pull raw YUYV from V4L2 if OpenCV converts the frames."""

    def __init__(self, dev):
        self.p = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "v4l2", "-input_format", "yuyv422",
             "-video_size", f"{WIDTH}x{HEIGHT}", "-i", dev, "-f", "rawvideo", "-"],
            stdout=subprocess.PIPE, bufsize=FRAME_BYTES * 4)
        if self.read() is None:
            self.close()
            raise RuntimeError("ffmpeg got no frames")

    def read(self):
        b = self.p.stdout.read(FRAME_BYTES)
        return b if b and len(b) == FRAME_BYTES else None

    def close(self):
        self.p.kill()
        self.p.wait()


class FakeReader:
    """Synthetic scene: ~12 C ground with a ~33 C person-sized blob drifting across."""

    def __init__(self, *_):
        self.t0 = time.time()
        yy, xx = np.mgrid[0:SENSOR_H, 0:WIDTH]
        self.yy, self.xx = yy, xx

    def read(self):
        time.sleep(1 / 25)
        t = time.time() - self.t0
        cx, cy = 128 + 90 * np.sin(t / 3), 110
        c = 12 + 2 * self.yy / SENSOR_H + np.random.normal(0, 0.15, self.yy.shape)
        c += 21 * np.exp(-(((self.xx - cx) / 6) ** 2 + ((self.yy - cy) / 18) ** 2))   # person ~33 C
        c += 16 * np.exp(-(((self.xx - 60) / 7) ** 2 + ((self.yy - 160) / 4) ** 2))     # animal ~28 C
        c += 60 * np.exp(-(((self.xx - 220) / 3) ** 2 + ((self.yy - 30) / 3) ** 2))     # engine/fire ~72 C (ignored)
        raw = np.zeros((HEIGHT, WIDTH), np.uint16)
        raw[:SENSOR_H] = ((c + 273.15) * COUNTS_PER_KELVIN).astype(np.uint16)
        return raw.astype("<u2").tobytes()

    def close(self):
        pass


def open_camera(device=None, reader="auto"):
    devs = [device] if device else candidate_devices()
    kinds = {"auto": (OpenCVReader, FFmpegReader), "opencv": (OpenCVReader,), "ffmpeg": (FFmpegReader,)}[reader]
    for dev in devs:
        for kind in kinds:
            try:
                r = kind(dev)
                log(f"camera: {dev} via {kind.__name__}")
                return r
            except Exception as e:
                log(f"  {dev} {kind.__name__}: {e}")
    return None


# ---------------------------------------------------------------- processing
def to_celsius(buf):
    counts = np.frombuffer(buf, dtype="<u2", count=WIDTH * SENSOR_H).reshape(SENSOR_H, WIDTH)
    return counts.astype(np.float32) / COUNTS_PER_KELVIN - 273.15


def detect(temps, t_lo=26.0, t_hi=42.0, delta=5.0, min_area=6, max_frac=0.35):
    """Warm-body candidates from raw temperatures.

    A pixel is a candidate if it is between t_lo and t_hi C and at least `delta` C above the
    scene median. Connected regions are filtered by size. Returns a list of dicts sorted by
    peak temperature. This finds warm bodies; it cannot tell a person from a large animal.
    """
    ambient = float(np.median(temps))
    thresh = max(t_lo, ambient + delta)
    mask = ((temps >= thresh) & (temps <= t_hi)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    # the cooling edge of anything hotter than a body (engine, fire, sun-heated rock) passes
    # through the body range; drop regions that touch such hot pixels
    too_hot = cv2.dilate((temps > t_hi).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    dets = []
    max_area = max_frac * temps.size
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < min_area or area > max_area:
            continue
        sel = labels == i
        if too_hot[sel].any():
            continue
        region = temps[sel]
        peak = float(region.max())
        # rough confidence: warmer than ambient, compact, upright-ish shapes score higher
        contrast = min((peak - ambient) / 15.0, 1.0)
        fill = area / float(w * h)
        conf = round(0.6 * contrast + 0.4 * min(fill / 0.5, 1.0), 2)
        dets.append({"x": x, "y": y, "w": w, "h": h, "area": area, "t_peak": round(peak, 1),
                     "t_mean": round(float(region.mean()), 1), "cx": round(float(cents[i][0]), 1),
                     "cy": round(float(cents[i][1]), 1), "conf": conf})
    dets.sort(key=lambda d: d["t_peak"], reverse=True)
    return dets, round(ambient, 1), round(thresh, 1)


def draw_detections(view, dets, scale):
    for d in dets:
        p1 = (d["x"] * scale, d["y"] * scale)
        p2 = ((d["x"] + d["w"]) * scale, (d["y"] + d["h"]) * scale)
        cv2.rectangle(view, p1, p2, (0, 255, 0), 2)
        text = f"warm {d['t_peak']:.1f}C"
        org = (p1[0], max(p1[1] - 4, 12))
        fs = 0.3 * scale
        cv2.putText(view, text, org, cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(view, text, org, cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 255, 0), 1, cv2.LINE_AA)


def render(temps, scale, cmap=cv2.COLORMAP_INFERNO):
    t_min, t_max = float(temps.min()), float(temps.max())
    # cap the color range just above body temperature so a fire or engine in frame
    # doesn't wash people out (hotter things simply saturate to white)
    c_max = min(t_max, max(t_min + 8.0, 40.0))
    gray = (np.clip((temps - t_min) / max(c_max - t_min, 0.5), 0, 1) * 255).astype(np.uint8)
    view = cv2.applyColorMap(gray, cmap)
    if scale != 1:
        view = cv2.resize(view, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
    h, w = temps.shape
    cy, cx = h // 2, w // 2
    hi = np.unravel_index(np.argmax(temps), temps.shape)
    t_ctr = float(temps[cy, cx])

    def pt(rc):
        return int((rc[1] + 0.5) * scale), int((rc[0] + 0.5) * scale)

    fs = 0.35 * scale
    cv2.drawMarker(view, pt((cy, cx)), (255, 255, 255), cv2.MARKER_CROSS, 8 * scale, 1)
    cv2.drawMarker(view, pt(hi), (0, 0, 255), cv2.MARKER_TRIANGLE_UP, 7 * scale, 1)
    text = f"max {t_max:.1f}  ctr {t_ctr:.1f}  min {t_min:.1f} C"
    org = (4, int(11 * scale))
    cv2.putText(view, text, org, cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(view, text, org, cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), 1, cv2.LINE_AA)
    return view, {"t_min": round(t_min, 2), "t_max": round(t_max, 2), "t_ctr": round(t_ctr, 2),
                  "hot_x": int(hi[1]), "hot_y": int(hi[0])}


# ---------------------------------------------------------------- output
def start_encoder(w, h, fps, url, bitrate, encoder):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
           "-an", "-c:v", encoder]
    if encoder == "libx264":
        cmd += ["-preset", "ultrafast", "-tune", "zerolatency", "-profile:v", "baseline"]
    cmd += ["-pix_fmt", "yuv420p", "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", bitrate,
            "-g", str(fps), "-f", "rtsp", "-rtsp_transport", "tcp", url]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", help="V4L2 device, e.g. /dev/video0 (default: auto)")
    ap.add_argument("--reader", choices=["auto", "opencv", "ffmpeg"], default="auto")
    ap.add_argument("--fake", action="store_true", help="synthetic frames, no camera needed")
    ap.add_argument("--check", action="store_true", help="grab frames, print temperatures, exit")
    ap.add_argument("--url", default="rtsp://localhost:8554/thermal")
    ap.add_argument("--scale", type=int, default=2, help="upscale before encoding (1 = native 256x192)")
    ap.add_argument("--fps", type=int, default=25)
    ap.add_argument("--bitrate", default="600k")
    ap.add_argument("--encoder", default="libx264", help="libx264, or h264_v4l2m2m on a Pi 4")
    ap.add_argument("--out", default=str(Path.home() / "thermal_data"), help="raw temperature + telemetry dir")
    ap.add_argument("--raw-every", type=float, default=1.0, help="seconds between saved raw frames (0 = off)")
    ap.add_argument("--no-detect", action="store_true", help="disable warm-body detection")
    ap.add_argument("--det-min", type=float, default=26.0, help="lowest temperature counted as a warm body (C)")
    ap.add_argument("--det-max", type=float, default=42.0, help="highest temperature counted as a warm body (C)")
    ap.add_argument("--det-delta", type=float, default=5.0, help="must be this many C above scene median")
    ap.add_argument("--det-min-area", type=int, default=6, help="smallest region in sensor pixels")
    ap.add_argument("--min-free-gb", type=float, default=2.0, help="pause raw saves below this free space")
    ap.add_argument("--udp", default="", help="also send telemetry JSON to host:port, e.g. 10.42.0.255:5005")
    args = ap.parse_args()

    if args.check:
        cam = FakeReader() if args.fake else open_camera(args.device, args.reader)
        if cam is None:
            sys.exit("thermal camera not found")
        for _ in range(5):
            buf = cam.read()
            if buf:
                t = to_celsius(buf)
                _, s = render(t, 1)
                dets, amb, thr = detect(t)
                log("frame ok", s, f"ambient {amb} C, {len(dets)} warm target(s)",
                    [(d["t_peak"], d["w"], d["h"]) for d in dets[:3]])
        cam.close()
        return

    session = Path(args.out) / time.strftime("%Y%m%d_%H%M%S")
    session.mkdir(parents=True, exist_ok=True)
    tel = open(session / "telemetry.csv", "w", newline="")
    tw = csv.DictWriter(tel, fieldnames=["time", "t_min", "t_max", "t_ctr", "hot_x", "hot_y", "ambient", "n_targets", "top_t", "top_cx", "top_cy"])
    tw.writeheader()
    detf = open(session / "detections.jsonl", "a")
    udp = None
    if args.udp:
        host, port = args.udp.rsplit(":", 1)
        udp = (socket.socket(socket.AF_INET, socket.SOCK_DGRAM), (host, int(port)))
        udp[0].setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    log("saving raw temps + telemetry to", session)

    w, h = WIDTH * args.scale, SENSOR_H * args.scale
    cam = enc = None
    last_raw = last_tel = 0.0
    low_disk = False
    n = 0
    t_fps = time.time()
    try:
        while True:
            if cam is None:
                cam = FakeReader() if args.fake else open_camera(args.device, args.reader)
                if cam is None:
                    log("camera not found, retrying in 3 s")
                    time.sleep(3)
                    continue
            if enc is None or enc.poll() is not None:
                log("starting encoder ->", args.url)
                enc = start_encoder(w, h, args.fps, args.url, args.bitrate, args.encoder)
            buf = cam.read()
            if buf is None:
                log("frame read failed, reopening camera")
                cam.close()
                cam = None
                time.sleep(1)
                continue
            temps = to_celsius(buf)
            view, stats = render(temps, args.scale)
            dets = []
            if not args.no_detect:
                dets, amb, _ = detect(temps, args.det_min, args.det_max, args.det_delta, args.det_min_area)
                draw_detections(view, dets, args.scale)
                top = dets[0] if dets else {}
                stats.update({"ambient": amb, "n_targets": len(dets), "top_t": top.get("t_peak"),
                              "top_cx": top.get("cx"), "top_cy": top.get("cy")})
            try:
                enc.stdin.write(view.tobytes())
            except (BrokenPipeError, OSError):
                log("encoder exited (is MediaMTX running?), restarting in 2 s")
                enc.kill()
                enc = None
                time.sleep(2)
                continue
            now = time.time()
            if args.raw_every and now - last_raw >= args.raw_every:
                last_raw = now
                if shutil.disk_usage(session).free > args.min_free_gb * 1e9:
                    np.save(session / f"{now:.2f}.npy", temps.astype(np.float16))
                elif not low_disk:
                    low_disk = True
                    log(f"less than {args.min_free_gb} GB free: pausing raw saves, still streaming")
            if now - last_tel >= 0.2:
                row = {"time": round(now, 2), **stats}
                tw.writerow(row)
                if dets:
                    detf.write(json.dumps({"time": row["time"], "targets": dets}) + "\n")
                    row = {**row, "targets": dets[:5]}
                if udp:
                    try:
                        udp[0].sendto(json.dumps(row).encode(), udp[1])
                    except OSError:
                        pass  # no route to the telemetry network (e.g. hotspot off); keep streaming
                last_tel = now
            n += 1
            if now - t_fps >= 10:
                tel.flush()
                detf.flush()
                log(f"{n / (now - t_fps):.1f} fps  max {stats['t_max']} C  targets {stats.get('n_targets', '-')}")
                n, t_fps = 0, now
    except KeyboardInterrupt:
        pass
    finally:
        tel.close()
        detf.close()
        if cam:
            cam.close()
        if enc:
            enc.kill()


if __name__ == "__main__":
    main()
