# Next steps for Claude Code on the Pi

Read this whole file, then follow it. It replaces the remaining steps of the earlier sar-integration prompt.

## Change of plan

1. First, tell Alex in one or two sentences what has been taking the time so far.
2. Do not install the `insightface` or `torch` pip packages on this Pi, and stop any build of them. `eagle/identity.py` only needs `onnxruntime` plus the buffalo_l ONNX files.
3. Run `apps/fetch_models.py` without `--export-osnet`. OSNet is optional tonight; face and CLIP are enough.
4. Skip the distance test (scoring people at 3, 7 and 15 m). We will do it in the field.

## Priority order

1. Get `life_node` running with thermal (source `hti`), audio (preset `respeaker_xvf3800`) and vision, sending over `--transport udp` to `nest_station` on this Pi with `--print-cot`. Confirm the decrypted FIX output prints.
2. Make sure `apps/enroll_target.py --live` and `--verify` work on the NoIR camera. Report to Alex when steps 1 and 2 work.
3. Field prep, in this order:
   - Offline check: with Wi-Fi off, confirm everything above still starts. Turn Wi-Fi back on afterwards.
   - Add a `--record` flag (to `fusion/app.py` on thermal-cam, and to `life_node` if simple) that saves to `~/field_logs/<timestamp>/`: thermal temperature arrays (npz, 2 per second), NoIR frames (JPEG, 1 per second), raw microphone channels (WAV), and every detection and verdict (JSONL). Report how many hours of recording fit on the disk.
   - Power: report `vcgencmd get_throttled` and whether `config.txt` has `usb_max_current_enable`. Do not change `config.txt` without asking.
   - Make the field app start on boot without a monitor.
   - Write `FIELD_TEST.md`: power-on order, joining the THERMAL-PI hotspot, dashboard URL, starting and stopping recording, clean shutdown.
4. Ask Alex before running `hotspot_on.sh`, since it drops venue Wi-Fi.

## Rules

- Never push to `main` or `eagle-v2`. Commit to `sar-integration` (and `thermal-cam` for `fusion/` changes) with the existing git identity.
- Also report: Hailo chip (Hailo-8 or 8L) and HailoRT version, and the XVF3800 channel counts, if not reported yet.
