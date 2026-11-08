#!/usr/bin/env python3
"""Recorded thermal frames -> PNG images for fine-tuning the thermal person model and for the
Hailo compiler's calibration set (pi/compile_thermal_hef.sh).

    python apps/export_thermal_calib.py ~/field_logs/20261108-021500 ~/field_logs/20261108-031000 \
        --out ~/calib_thermal --max 1024

Reads thermal_*.npz written by ``--record`` (eagle/record.py) and writes each frame the way the
model sees it (eagle/infer.py: min..max stretch, white-hot, 3 channels), at the camera's native
size; the compiler applies the model's own resize. Frames are spread evenly over the recordings.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eagle.infer import thermal_to_gray  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recordings", nargs="+", help="--record run directories (eagle/record.py)")
    ap.add_argument("--out", required=True, help="output folder for PNG files")
    ap.add_argument("--max", type=int, default=1024, help="at most this many images")
    args = ap.parse_args(argv)
    import cv2
    files = sorted(f for r in args.recordings for f in Path(r).expanduser().glob("thermal_*.npz"))
    stacks = []
    for f in files:
        z = np.load(f)
        stacks.append(z["centi_c"].astype(np.float32) / 100.0 if "centi_c" in z else z["temps_c"])
    n = sum(len(s) for s in stacks)
    if not n:
        print("no thermal_*.npz frames found")
        return 1
    keep = set(np.linspace(0, n - 1, min(args.max, n)).round().astype(int).tolist())
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    k = written = 0
    for s in stacks:
        for fr in s:
            if k in keep:
                g = thermal_to_gray(fr.astype(np.float32))
                cv2.imwrite(str(out / f"thermal_{k:06d}.png"), cv2.cvtColor(g, cv2.COLOR_GRAY2BGR))
                written += 1
            k += 1
    print(f"{written} images from {n} frames in {len(files)} file(s) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
