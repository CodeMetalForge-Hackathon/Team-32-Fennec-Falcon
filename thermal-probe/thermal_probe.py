#!/usr/bin/env python3
"""thermal_probe.py - find, view and stream a USB (UVC) thermal camera with OpenCV.

Built for HTI phone thermal imagers (256x192, Hikvision-class core) and similar USB-C
cameras (InfiRay P2 Pro, Topdon TC001, PureThermal + Lepton). Works on Windows
(DirectShow, Media Foundation) and Linux (V4L2, e.g. the Pi 5).

    pip install opencv-python numpy            # optional on Windows: pip install pygrabber

    python thermal_probe.py                    # scan every camera, backend, size and format
    python thermal_probe.py --quick            # shorter scan (most likely modes only)
    python thermal_probe.py --view             # live false-colour view with the best mode found
    python thermal_probe.py --view --index 1 --backend dshow --size 256x384 --fourcc YUY2 --raw
    python thermal_probe.py --list             # camera names (DirectShow / V4L2) and ffmpeg modes
    python thermal_probe.py --view --ffmpeg "USB Camera" --size 256x192   # read via ffmpeg
    python thermal_probe.py --view --rtsp rtsp://127.0.0.1:8554/eagle      # publish to MediaMTX

Viewer keys: q quit | s save PNG + raw .npy | p palette | h hot-spot boxes on/off

What the scan reports for each working mode (the "kind"):
    y16    16-bit raw frames (radiometric if the camera is in that mode; PureThermal TLinear:
           deg C = raw/100 - 273.15)
    split  double-height YUYV: top half = picture, bottom half = 16-bit raw data
           (InfiRay P2 Pro style 256x384; deg C is often raw/64 - 273.15 - check with --scale 64)
    yuyv   raw YUYV bytes; we use the luma channel (an 8-bit AGC picture)
    gray8  8-bit single-channel picture
    bgr    colour picture (OpenCV converted it; raw data lost)
Only y16/split carry raw values. The rest are pictures whose grey levels are relative.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time

import numpy as np

try:
    import cv2  # type: ignore
except ImportError:  # pragma: no cover
    sys.exit("OpenCV missing: pip install opencv-python numpy")

IS_WIN = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"

BACKENDS = {
    "dshow": getattr(cv2, "CAP_DSHOW", None),
    "msmf": getattr(cv2, "CAP_MSMF", None),
    "v4l2": getattr(cv2, "CAP_V4L2", None),
    "avf": getattr(cv2, "CAP_AVFOUNDATION", None),
    "any": cv2.CAP_ANY,
}
DEFAULT_BACKENDS = ["dshow", "msmf"] if IS_WIN else (["v4l2", "any"] if IS_LINUX else ["any"])

# Native sizes of common USB thermal cores, plus the double-height "image + raw" modes.
SIZES = [(256, 192), (256, 384), (256, 196), (256, 392), (160, 120), (384, 288), (384, 576), (640, 512)]
QUICK_SIZES = [(256, 192), (256, 384)]
Y16_SIZES = [(256, 192), (160, 120), (384, 288), (640, 512)]
KIND_RANK = {"y16": 5, "split": 4, "yuyv": 3, "gray8": 2, "bgr": 1}


# ---------------------------------------------------------------- frame decoding
def decode(frame: np.ndarray, w: int, h: int):
    """Turn whatever OpenCV/ffmpeg returned into (picture_u8 HxW, raw_u16 or None, kind)."""
    a = np.asarray(frame)
    if a.dtype == np.uint16:                                  # Y16 with CONVERT_RGB off
        if a.ndim == 3:
            a = a[:, :, 0]
        if a.ndim < 2 or a.shape[0] == 1:
            a = a.reshape(h, w)
        return to_u8(a), a, "y16"
    if a.ndim == 3 and a.shape[2] == 3:                       # already converted to BGR
        return cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), None, "bgr"
    if a.ndim == 3 and a.shape[2] == 2:                       # YUYV as HxWx2
        h, w = a.shape[:2]
        a = a.reshape(-1)
    if a.ndim == 2 and a.shape[0] > 1 and a.shape[1] > 2 and a.size != w * h * 2:
        return a.astype(np.uint8), None, "gray8"             # plain 8-bit picture
    b = a.reshape(-1).astype(np.uint8, copy=False)
    if b.size == w * h * 2:                                   # raw YUYV buffer
        yuyv = b.reshape(h, w, 2)
        if h >= 2 * 120 and h % 2 == 0 and (h // 2) in (96, 120, 144, 192, 196, 288, 292, 512):
            top = np.ascontiguousarray(yuyv[: h // 2, :, 0])
            raw = b[w * (h // 2) * 2:].view("<u2").reshape(h // 2, w)
            if raw.std() > 0:                                 # bottom half really holds data
                return top, raw, "split"
        return np.ascontiguousarray(yuyv[:, :, 0]), None, "yuyv"
    if b.size == w * h:
        return b.reshape(h, w), None, "gray8"
    raise ValueError(f"unexpected frame {a.shape} {a.dtype} for {w}x{h}")


def to_u8(raw: np.ndarray) -> np.ndarray:
    """Robust 2-98 % stretch of a 16-bit frame to 8 bits."""
    lo, hi = np.percentile(raw, (2, 98))
    if hi <= lo:
        hi = lo + 1
    return np.clip((raw.astype(np.float32) - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- OpenCV attempts
def _run_with_timeout(fn, timeout):
    box = {}

    def work():
        try:
            box["value"] = fn()
        except Exception as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, "timeout"
    if "error" in box:
        return None, f"error: {box['error']}"
    return box.get("value"), None


def open_capture(index, backend, size=None, fourcc=None, convert_rgb=None, timeout=6.0):
    api = BACKENDS.get(backend)
    if api is None:
        return None, f"backend {backend} not in this OpenCV build"

    params = []
    if fourcc:
        params += [cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc.ljust(4)[:4])]
    if size:
        params += [cv2.CAP_PROP_FRAME_WIDTH, int(size[0]), cv2.CAP_PROP_FRAME_HEIGHT, int(size[1])]
    if convert_rgb is not None:
        params += [cv2.CAP_PROP_CONVERT_RGB, 1 if convert_rgb else 0]

    def _open():
        cap = None
        if params:                       # some cameras only open when the mode is given up front
            try:
                cap = cv2.VideoCapture(index, api, params)
            except (TypeError, cv2.error):
                cap = None
        if cap is None or not cap.isOpened():
            if cap is not None:
                cap.release()
            cap = cv2.VideoCapture(index, api)
        if not cap.isOpened():
            cap.release()
            return None
        if fourcc:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc.ljust(4)[:4]))
        if size:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
        if convert_rgb is not None:
            cap.set(cv2.CAP_PROP_CONVERT_RGB, 1 if convert_rgb else 0)
        return cap

    cap, err = _run_with_timeout(_open, timeout)
    if err:
        return None, err
    if cap is None:
        return None, "not opened"
    return cap, None


def try_mode(index, backend, size=None, fourcc=None, convert_rgb=None, frames=8, timeout=6.0):
    """Open with these settings, read a few frames, report what came back."""
    rec = {"index": index, "backend": backend, "size": list(size) if size else None,
           "fourcc": fourcc, "convert_rgb": convert_rgb, "ok": False}
    cap, err = open_capture(index, backend, size, fourcc, convert_rgb, timeout)
    if err:
        rec["error"] = err
        return rec, None
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or (size or (0, 0))[0])
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or (size or (0, 0))[1])
    rec["reported"] = [w, h]

    def _read():
        got = None
        for _ in range(frames):
            ok, f = cap.read()
            if ok and f is not None and np.asarray(f).size:
                got = f
        return got

    frame, err = _run_with_timeout(_read, timeout)
    if err == "timeout":                     # leave the blocked capture alone; never release mid-read
        rec["error"] = "read timeout"
        return rec, None
    cap.release()
    if frame is None:
        rec["error"] = err or "no frames"
        return rec, None
    a = np.asarray(frame)
    rec["frame"] = [list(a.shape), str(a.dtype)]
    for (ww, hh) in ([(w, h)] if w and h else []) + SIZES:
        try:
            pic, raw, kind = decode(a, ww, hh)
            break
        except ValueError:
            continue
    else:
        rec["error"] = f"cannot decode {a.shape} {a.dtype}"
        return rec, None
    rec.update(ok=True, kind=kind, picture=list(pic.shape), raw=bool(raw is not None),
               picture_std=round(float(pic.std()), 2))
    return rec, (pic, raw)


def scan(indices, backends, quick=False, outdir="probe_out"):
    os.makedirs(outdir, exist_ok=True)
    results, best = [], None
    sizes = QUICK_SIZES if quick else SIZES
    for backend in backends:
        for idx in indices:
            base, data = try_mode(idx, backend)
            results.append(base)
            print(fmt(base))
            if base.get("error", "").startswith("backend"):
                continue
            if base.get("error") == "not opened":
                # maybe it only opens with its mode given up front; if not, nothing is there
                probes = [try_mode(idx, backend, size=s, fourcc="YUY2", convert_rgb=False) for s in QUICK_SIZES]
                if not any(r["ok"] or r.get("error") not in ("not opened",) for r, _ in probes):
                    continue
            modes = [dict(convert_rgb=False)]
            modes += [dict(size=s, fourcc="YUY2", convert_rgb=False) for s in sizes]
            if not quick:
                modes += [dict(size=s, convert_rgb=False) for s in sizes]
            modes += [dict(size=s, fourcc="Y16", convert_rgb=False) for s in (Y16_SIZES[:2] if quick else Y16_SIZES)]
            for m in modes:
                rec, d = try_mode(idx, backend, **m)
                results.append(rec)
                print(fmt(rec))
                if rec["ok"]:
                    tag = f"i{idx}_{backend}_{rec['kind']}_{'x'.join(map(str, rec.get('size') or rec['reported']))}"
                    pic, raw = d
                    cv2.imwrite(os.path.join(outdir, tag + ".png"), colorize(pic, 0))
                    if raw is not None:
                        np.save(os.path.join(outdir, tag + "_raw.npy"), raw)
                    if best is None or score(rec) > score(best):
                        best = rec
            if base["ok"] and (best is None or score(base) > score(best)):
                best = base
    return results, best


def score(rec):
    if not rec.get("ok"):
        return -1
    flat_penalty = 0 if rec.get("picture_std", 0) > 0.5 else -3
    return KIND_RANK.get(rec["kind"], 0) * 10 + flat_penalty


def fmt(rec):
    m = f"idx {rec['index']} {rec['backend']:<5} size {rec.get('size') or '-'!s:<11} fourcc {rec.get('fourcc') or '-':<4} " \
        f"rgb {rec.get('convert_rgb') if rec.get('convert_rgb') is not None else '-'!s:<5}"
    if rec["ok"]:
        return f"  OK   {m} -> {rec['kind']:<5} frame {rec['frame'][0]} {rec['frame'][1]} picture {rec['picture']}" \
               f"{' +raw16' if rec['raw'] else ''}"
    return f"  --   {m} -> {rec.get('error')}"


# ---------------------------------------------------------------- device names / ffmpeg
def device_names():
    names = {}
    if IS_WIN:
        try:
            from pygrabber.dshow_graph import FilterGraph  # type: ignore
            for i, n in enumerate(FilterGraph().get_input_devices()):
                names[i] = n
        except Exception:  # noqa: BLE001
            pass
        if not names and shutil.which("ffmpeg"):
            out = subprocess.run(["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                                 capture_output=True, text=True).stderr
            vids = [m for m in re.findall(r'"([^"]+)"\s*\(video\)', out)]
            names = dict(enumerate(vids))
    elif IS_LINUX:
        for d in sorted(os.listdir("/sys/class/video4linux")) if os.path.isdir("/sys/class/video4linux") else []:
            try:
                with open(f"/sys/class/video4linux/{d}/name") as fh:
                    names[int(d.replace("video", ""))] = fh.read().strip()
            except (OSError, ValueError):
                pass
    return names


def ffmpeg_modes(name_or_dev):
    if not shutil.which("ffmpeg"):
        return "ffmpeg not found (Windows: winget install Gyan.FFmpeg)"
    if IS_WIN:
        cmd = ["ffmpeg", "-hide_banner", "-f", "dshow", "-list_options", "true", "-i", f"video={name_or_dev}"]
    else:
        cmd = ["ffmpeg", "-hide_banner", "-f", "v4l2", "-list_formats", "all", "-i", str(name_or_dev)]
    return subprocess.run(cmd, capture_output=True, text=True).stderr


class FfmpegReader:
    """Reads raw frames through ffmpeg (stdout pipe) when OpenCV cannot open the camera."""

    def __init__(self, source, w, h, pix_fmt="yuyv422", extra=None):
        self.w, self.h, self.pix_fmt = w, h, pix_fmt
        self.bpp = 2 if pix_fmt in ("yuyv422", "gray16le") else 1
        if extra:                                   # test hook: full input args
            inp = extra
        elif IS_WIN:
            inp = ["-f", "dshow", "-video_size", f"{w}x{h}", "-i", f"video={source}"]
        else:
            inp = ["-f", "v4l2", "-video_size", f"{w}x{h}", "-i", str(source)]
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", *inp,
               "-f", "rawvideo", "-pix_fmt", pix_fmt, "-"]
        self.p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=10 ** 7)
        self.n = w * h * self.bpp

    def read(self):
        buf = self.p.stdout.read(self.n)
        if len(buf) < self.n:
            return False, None
        a = np.frombuffer(buf, np.uint8)
        if self.pix_fmt == "gray16le":
            a = a.view("<u2").reshape(self.h, self.w)
        return True, a

    def release(self):
        self.p.kill()


# ---------------------------------------------------------------- viewing
PALETTES = [("inferno", cv2.COLORMAP_INFERNO), ("ironbow", cv2.COLORMAP_HOT),
            ("jet", cv2.COLORMAP_JET), ("white-hot", None)]


def colorize(pic_u8, pal_index):
    _, cmap = PALETTES[pal_index % len(PALETTES)]
    return cv2.cvtColor(pic_u8, cv2.COLOR_GRAY2BGR) if cmap is None else cv2.applyColorMap(pic_u8, cmap)


def hot_spots(img, k=6.0, min_area=4):
    """Warm blobs: value above median + k * MAD. Returns boxes (x, y, w, h, peak)."""
    f = img.astype(np.float32)
    med = float(np.median(f))
    mad = float(np.median(np.abs(f - med))) or 1.0
    mask = (f > med + k * mad).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    boxes = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area >= min_area:
            boxes.append((int(x), int(y), int(w), int(h), float(f[y:y + h, x:x + w].max())))
    return boxes


class RtspOut:
    """Pipes BGR frames to ffmpeg -> H.264 -> RTSP (MediaMTX) or to a file for testing."""

    def __init__(self, url, w, h, fps, nvenc=False):
        enc = ["-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ll"] if nvenc else \
              ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency"]
        out = ["-f", "rtsp", "-rtsp_transport", "tcp", url] if url.startswith("rtsp://") else ["-y", url]
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-f", "rawvideo", "-pix_fmt", "bgr24",
               "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", *enc, "-pix_fmt", "yuv420p",
               "-g", str(int(fps)), "-b:v", "1M", *out]
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    def write(self, bgr):
        try:
            self.p.stdin.write(bgr.tobytes())
        except (BrokenPipeError, OSError):
            print("RTSP: ffmpeg stopped (is MediaMTX running?)")

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=5)
        except Exception:  # noqa: BLE001
            self.p.kill()


def view(args, best):
    w, h = args.size if args.size else (best or {}).get("size") or (best or {}).get("reported") or (256, 192)
    if args.ffmpeg:
        cap = FfmpegReader(args.ffmpeg, w, h, args.pix_fmt,
                           extra=shlex.split(args.ffmpeg_input) if args.ffmpeg_input else None)
    else:
        idx = args.index if args.index is not None else best["index"]
        backend = args.backend or best["backend"]
        fourcc = args.fourcc if args.fourcc else (best or {}).get("fourcc")
        conv = False if (args.raw or (best and best.get("convert_rgb") is False)) else None
        cap, err = open_capture(idx, backend, (w, h) if (args.size or (best and best.get("size"))) else None,
                                fourcc, conv)
        if err:
            sys.exit(f"cannot open camera {idx} via {backend}: {err}")
    pal, show_boxes, scale = 0, True, args.upscale
    out = None
    t0, n = time.time(), 0
    print("viewer: q quit | s save | p palette | h hot spots")
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            print("read failed")
            break
        pic, raw, kind = decode(frame, w, h)
        src = raw if raw is not None else pic
        disp = cv2.resize(colorize(pic, pal), (pic.shape[1] * scale, pic.shape[0] * scale),
                          interpolation=cv2.INTER_NEAREST)
        if show_boxes:
            for (x, y, bw, bh, peak) in hot_spots(src):
                cv2.rectangle(disp, (x * scale, y * scale), ((x + bw) * scale, (y + bh) * scale), (255, 255, 255), 1)
                label = f"{peak / args.scale - 273.15 + args.offset:.1f}C" if (raw is not None and args.scale) else f"{peak:.0f}"
                cv2.putText(disp, label, (x * scale, max(12, y * scale - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                            (255, 255, 255), 1, cv2.LINE_AA)
        n += 1
        fps = n / max(1e-3, time.time() - t0)
        cy, cx = src.shape[0] // 2, src.shape[1] // 2
        centre = src[cy, cx]
        ctext = f"{centre / args.scale - 273.15 + args.offset:.1f}C" if (raw is not None and args.scale) else f"{centre}"
        info = f"{kind} {pic.shape[1]}x{pic.shape[0]} {fps:4.1f} fps  centre {ctext}  [{PALETTES[pal][0]}]"
        cv2.drawMarker(disp, (cx * scale, cy * scale), (255, 255, 255), cv2.MARKER_CROSS, 12, 1)
        cv2.putText(disp, info, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
        if args.rtsp:
            if out is None:
                out = RtspOut(args.rtsp, disp.shape[1], disp.shape[0], args.fps, args.nvenc)
            out.write(disp)
        if args.headless:
            if n >= args.frames:
                break
            continue
        cv2.imshow("thermal_probe", disp)
        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"):
            break
        if k == ord("p"):
            pal += 1
        if k == ord("h"):
            show_boxes = not show_boxes
        if k == ord("s"):
            stamp = time.strftime("%H%M%S")
            cv2.imwrite(f"thermal_{stamp}.png", disp)
            np.save(f"thermal_{stamp}_{'raw' if raw is not None else 'pic'}.npy", src)
            print("saved", stamp)
    cap.release()
    if out:
        out.close()
    if not args.headless:
        cv2.destroyAllWindows()
    return n


def kit_snippet(best, hfov=56.0):
    w, h = best.get("size") or best["reported"]
    ph = h // 2 if best["kind"] == "split" else h
    src = "uvc_y16" if best["kind"] == "y16" else "gray8"
    note = "  # kit reads the picture mode; the raw half needs --raw here" if best["kind"] == "split" else ""
    return (f"life:\n  thermal:\n    source: {src}   # scan found '{best['kind']}' frames\n"
            f"    camera: {{hfov_deg: {hfov}, width: {w}, height: {ph}}}   # HTI HT-203 class: 56 x 42 deg\n"
            f"    uvc: {{device: {best['index']}, width: {w}, height: {ph}}}{note}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", type=int)
    ap.add_argument("--max-index", type=int, default=5)
    ap.add_argument("--backend", choices=[k for k, v in BACKENDS.items() if v is not None])
    ap.add_argument("--size", type=lambda s: tuple(int(x) for x in s.lower().split("x")))
    ap.add_argument("--fourcc")
    ap.add_argument("--raw", action="store_true", help="CONVERT_RGB off (needed for raw/YUYV/Y16)")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--view", action="store_true")
    ap.add_argument("--ffmpeg", help="read through ffmpeg: DirectShow camera name (Windows) or /dev/videoN")
    ap.add_argument("--ffmpeg-input", help=argparse.SUPPRESS)   # test hook: raw ffmpeg input args
    ap.add_argument("--pix-fmt", default="yuyv422", help="ffmpeg output: yuyv422 (raw bytes) | gray | gray16le")
    ap.add_argument("--scale", type=float, default=0.0, help="raw -> K divisor, e.g. 64 (InfiRay) or 100 (Lepton)")
    ap.add_argument("--offset", type=float, default=0.0, help="deg C added after conversion (calibration)")
    ap.add_argument("--upscale", type=int, default=3)
    ap.add_argument("--rtsp", help="publish, e.g. rtsp://127.0.0.1:8554/eagle (run mediamtx first)")
    ap.add_argument("--nvenc", action="store_true", help="NVIDIA H.264 encoder instead of libx264")
    ap.add_argument("--fps", type=float, default=25)
    ap.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--frames", type=int, default=50, help=argparse.SUPPRESS)
    ap.add_argument("--out", default="probe_out")
    args = ap.parse_args(argv)

    print(f"OpenCV {cv2.__version__} on {platform.system()} | backends to try: {args.backend or DEFAULT_BACKENDS}")
    names = device_names()
    if names:
        for i, n in names.items():
            print(f"  camera {i}: {n}")
    if args.list:
        for i, n in names.items():
            print(f"\n== {n} ==\n{ffmpeg_modes(n if IS_WIN else f'/dev/video{i}')}")
        return 0

    best = None
    if args.view and (args.ffmpeg or args.index is not None):
        best = {"index": args.index, "backend": args.backend or DEFAULT_BACKENDS[0], "size": args.size,
                "fourcc": args.fourcc, "convert_rgb": False if args.raw else None}
    else:
        indices = [args.index] if args.index is not None else list(range(args.max_index + 1))
        backends = [args.backend] if args.backend else DEFAULT_BACKENDS
        results, best = scan(indices, backends, args.quick, args.out)
        with open(os.path.join(args.out, "thermal_probe.json"), "w") as fh:
            json.dump({"best": best, "results": results, "names": names}, fh, indent=1)
        print(f"\nresults + preview PNGs: {os.path.abspath(args.out)}")
        if not best:
            print("\nNo mode returned frames. Check, in order:\n"
                  "  1. Windows: Settings > Privacy & security > Camera > 'Let desktop apps access your camera' = On\n"
                  "  2. Close every app that may hold the camera (Camera app, vendor app, Teams/Zoom, browsers)\n"
                  "  3. python thermal_probe.py --list   (shows the name and the modes Windows offers)\n"
                  "  4. python thermal_probe.py --view --ffmpeg \"<camera name>\" --size 256x192\n"
                  "  5. Plug it into the Pi 5 (Linux V4L2): v4l2-ctl --list-formats-ext -d /dev/video0")
            return 1
        print(f"\nBEST: {fmt(best).strip()}")
        name = names.get(best["index"])
        if name:
            print(f"      camera name: {name}")
        cmd = f"python thermal_probe.py --view --index {best['index']} --backend {best['backend']}"
        if best.get("size"):
            cmd += f" --size {best['size'][0]}x{best['size'][1]}"
        if best.get("fourcc"):
            cmd += f" --fourcc {best['fourcc']}"
        if best.get("convert_rgb") is False:
            cmd += " --raw"
        print(f"      view it:     {cmd}")
        print("\nEagle kit config (config/local.yaml):\n" + kit_snippet(best))
        if not args.view:
            return 0
    view(args, best)
    return 0


if __name__ == "__main__":
    sys.exit(main())
