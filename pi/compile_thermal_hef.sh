#!/usr/bin/env bash
# Compile fusion/models/thermal_person.onnx (YOLOv8n, 4 classes, 320 px) to a HEF for the AI HAT+.
# Runs on an x86-64 Linux PC with the Hailo Dataflow Compiler and Model Zoo (Hailo Developer Zone),
# NOT on the Pi. Then copy the HEF to the Pi as ~/eagle-models/thermal_person.hef; eagle-runtime picks it
# up on the next start (life.thermal.model.prefer: hailo first) and logs "thermal person: hailo (NPU)".
#   bash pi/compile_thermal_hef.sh hailo8  ~/calib_thermal    # AI HAT+ 26 TOPS
#   bash pi/compile_thermal_hef.sh hailo8l ~/calib_thermal    # AI HAT+ 13 TOPS
# Calibration images: apps/export_thermal_calib.py over a few --record sessions (night and day, with
# and without people); a few hundred to ~1000 frames.
# NOT RUN HERE. The command follows Hailo community examples (DFC 3.32 / Model Zoo 2.16); the Model Zoo's
# yolov8n recipe assumes a 640 px input, so a 320 px model may need its YAML edited (input shape) or
# --yaml with a copy: check `hailomz compile --help` and the Model Zoo docs for your version.
set -euo pipefail
ARCH="${1:?usage: compile_thermal_hef.sh hailo8|hailo8l CALIB_DIR}"
CALIB="${2:?usage: compile_thermal_hef.sh hailo8|hailo8l CALIB_DIR}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ONNX="$(dirname "$HERE")/fusion/models/thermal_person.onnx"
command -v hailomz >/dev/null || { echo "hailomz not found: install the Hailo Model Zoo + Dataflow Compiler"; exit 1; }
hailomz compile yolov8n --ckpt "$ONNX" --hw-arch "$ARCH" --calib-path "$CALIB" --classes 4 --performance
ls -l ./*.hef
echo "copy to the Pi:  scp yolov8n.hef pi@<pi>:~/eagle-models/thermal_person.hef"
