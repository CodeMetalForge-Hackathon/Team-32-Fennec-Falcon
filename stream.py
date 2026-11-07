"""Live view for a UVC thermal camera.

Keys: q quit, c cycle colormap, s save snapshot to captures/.
Run with:  python stream.py --index 1
"""
import argparse
import time
from pathlib import Path

import cv2
import numpy as np

COLORMAPS = [cv2.COLORMAP_INFERNO, cv2.COLORMAP_JET, cv2.COLORMAP_HOT, None]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=int, default=1, help="camera index (see find_cameras.py)")
    ap.add_argument("--scale", type=int, default=3, help="display upscale factor")
    args = ap.parse_args()

    cap = cv2.VideoCapture(args.index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        raise SystemExit(f"Could not open camera {args.index}. Run find_cameras.py.")

    Path("captures").mkdir(exist_ok=True)
    cmap_i, last, fps = 0, time.time(), 0.0

    while True:
        ok, frame = cap.read()
        if not ok:
            print("Frame grab failed")
            break

        h, w = frame.shape[:2]
        # Many thermal cams (TC001, P2, etc.) stack image on top, raw data below.
        if h / w > 1.3:
            frame = frame[: h // 2]

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        cmap = COLORMAPS[cmap_i]
        view = cv2.applyColorMap(gray, cmap) if cmap is not None else cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        view = cv2.resize(view, None, fx=args.scale, fy=args.scale, interpolation=cv2.INTER_CUBIC)

        now = time.time()
        fps = 0.9 * fps + 0.1 / max(now - last, 1e-6)
        last = now
        cv2.putText(view, f"{fps:4.1f} fps  {w}x{h}", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        cv2.imshow("thermal", view)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord("c"):
            cmap_i = (cmap_i + 1) % len(COLORMAPS)
        if key == ord("s"):
            name = f"captures/{time.strftime('%Y%m%d_%H%M%S')}.png"
            cv2.imwrite(name, view)
            print("saved", name)

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
