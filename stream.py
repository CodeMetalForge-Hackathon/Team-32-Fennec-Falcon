"""Live radiometric view for the HTI / Hikvision (USB VID_2BDF PID_0102) thermal imager.

Keys: q quit, c cycle colormap, s save snapshot (PNG + temperature .npy) to captures/.
Run with:  python stream.py            (tries Media Foundation index 1, then scans others)
           python stream.py --index 2  (try a different index first)

Frame layout in the camera's 256x392 YUY2 mode (2 bytes per pixel):
  rows   0-191  raw 16-bit little-endian thermal counts, kelvin * 16
  rows 192-195  metadata (starts with magic DD CC BB AA)
  rows 196-387  the camera's own colorized YUYV preview (unused here)
  rows 388-391  metadata (starts with magic FF FF EE EE)
The Media Foundation backend (CAP_MSMF) with CONVERT_RGB=0 delivers these bytes untouched;
DirectShow forces a YUV->BGR conversion that destroys the raw data.
"""
import argparse
import time
from pathlib import Path

import cv2
import numpy as np

WIDTH, HEIGHT = 256, 392          # full YUY2 frame
SENSOR_H = 192                    # rows of raw thermal data at the top
FRAME_BYTES = WIDTH * HEIGHT * 2
COUNTS_PER_KELVIN = 16.0
DEFAULT_INDEX = 1                 # MSMF index of the thermal camera on this PC (auto-scans if wrong)
COLORMAPS = [cv2.COLORMAP_INFERNO, cv2.COLORMAP_JET, cv2.COLORMAP_HOT, None]


def open_camera(index=DEFAULT_INDEX, max_index=6):
    """Open the thermal camera via MSMF in raw 256x392 mode.

    Tries `index` first (opening an MSMF device takes ~3-5 s, so a good guess matters),
    then scans the other indices for a device that delivers raw 256x392 YUY2.
    """
    candidates = [index] + [i for i in range(max_index) if i != index]
    for i in candidates:
        cap = cv2.VideoCapture(i, cv2.CAP_MSMF, [cv2.CAP_PROP_FRAME_WIDTH, WIDTH,
                                                 cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT])
        if cap.isOpened() and (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                               int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))) == (WIDTH, HEIGHT):
            cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
            for _ in range(10):
                ok, frame = cap.read()
                if ok and frame.size == FRAME_BYTES:
                    return cap, i
        cap.release()
    raise SystemExit("Thermal camera not found (no MSMF device delivers raw 256x392 YUY2). "
                     "Is it plugged in, and is no other app using it? Run find_cameras.py.")


def to_celsius(frame):
    """Raw MSMF buffer -> (192, 256) float32 array of degrees C."""
    counts = np.frombuffer(frame.tobytes(), dtype="<u2", count=WIDTH * SENSOR_H).reshape(SENSOR_H, WIDTH)
    return counts.astype(np.float32) / COUNTS_PER_KELVIN - 273.15


def render(temps, cmap, scale):
    """Colorize a temperature array and draw min/max/center markers and readout."""
    t_min, t_max = float(temps.min()), float(temps.max())
    gray = ((temps - t_min) * (255.0 / max(t_max - t_min, 0.1))).astype(np.uint8)
    view = cv2.applyColorMap(gray, cmap) if cmap is not None else cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    view = cv2.resize(view, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    h, w = temps.shape
    cy, cx = h // 2, w // 2
    t_ctr = float(temps[cy, cx])
    lo = np.unravel_index(np.argmin(temps), temps.shape)
    hi = np.unravel_index(np.argmax(temps), temps.shape)

    def pt(rc):
        return int((rc[1] + 0.5) * scale), int((rc[0] + 0.5) * scale)

    def label(text, org, color):
        cv2.putText(view, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(view, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)

    c = pt((cy, cx))
    cv2.drawMarker(view, c, (255, 255, 255), cv2.MARKER_CROSS, 20, 2)
    label(f"{t_ctr:.1f}C", (c[0] + 8, c[1] - 8), (255, 255, 255))
    for rc, color, val in ((hi, (0, 0, 255), t_max), (lo, (255, 128, 0), t_min)):
        p = pt(rc)
        cv2.drawMarker(view, p, color, cv2.MARKER_TRIANGLE_UP, 14, 2)
        label(f"{val:.1f}C", (min(p[0] + 8, view.shape[1] - 60), max(p[1] - 8, 40)), color)
    return view, (t_min, t_max, t_ctr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=int, default=DEFAULT_INDEX,
                    help=f"MSMF camera index to try first (default {DEFAULT_INDEX}; others are scanned if it fails)")
    ap.add_argument("--scale", type=int, default=3, help="display upscale factor")
    args = ap.parse_args()

    cap, idx = open_camera(args.index)
    print(f"Thermal camera on MSMF index {idx}")

    Path("captures").mkdir(exist_ok=True)
    cmap_i, last, fps = 0, time.time(), 0.0

    while True:
        ok, frame = cap.read()
        if not ok or frame.size != FRAME_BYTES:
            print("Frame grab failed")
            break

        temps = to_celsius(frame)
        view, (t_min, t_max, t_ctr) = render(temps, COLORMAPS[cmap_i], args.scale)

        now = time.time()
        fps = 0.9 * fps + 0.1 / max(now - last, 1e-6)
        last = now
        bar = f"{fps:4.1f} fps  min {t_min:.1f}  max {t_max:.1f}  ctr {t_ctr:.1f} C"
        cv2.putText(view, bar, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(view, bar, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)

        cv2.imshow("thermal", view)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q") or cv2.getWindowProperty("thermal", cv2.WND_PROP_VISIBLE) < 1:
            break
        if key == ord("c"):
            cmap_i = (cmap_i + 1) % len(COLORMAPS)
        if key == ord("s"):
            name = f"captures/{time.strftime('%Y%m%d_%H%M%S')}"
            cv2.imwrite(name + ".png", view)
            np.save(name + "_celsius.npy", temps)
            print("saved", name + ".png", "and", name + "_celsius.npy")

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
