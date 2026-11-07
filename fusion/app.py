#!/usr/bin/env python3
"""Three-sensor human detection demo, fully offline on the Raspberry Pi.

Thermal (HTI/Hikvision imager): warm-body blobs from raw temperatures, plus an optional
    thermal person model (models/thermal_person.onnx) trained on drone thermal imagery.
Visual (Pi Camera Module 3, or a USB webcam): COCO YOLOv8n via OpenCV DNN, person vs animal.
Audio (USB microphone): Google YAMNet, 521 sound classes grouped into human vs animal sounds.

The three are fused into one verdict and shown on a dashboard at http://<pi>:8080 that works
with no internet. Every frame, score and verdict is computed locally.

Run:  python3 fusion/app.py                         (all real sensors)
      python3 fusion/app.py --thermal fake --rgb file --rgb-file img.jpg --audio file --audio-file a.wav
The thermal camera can only be used by one program: stop the streaming service first
(sudo systemctl stop thermal), or run this as the fusion service (see fusion/README.md).
"""
import argparse
import csv
import json
import subprocess
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MODELS = HERE / "models"
sys.path.insert(0, str(ROOT / "pi"))

COCO = ("person bicycle car motorcycle airplane bus train truck boat traffic_light fire_hydrant stop_sign "
        "parking_meter bench bird cat dog horse sheep cow elephant bear zebra giraffe backpack umbrella "
        "handbag tie suitcase frisbee skis snowboard sports_ball kite baseball_bat baseball_glove skateboard "
        "surfboard tennis_racket bottle wine_glass cup fork knife spoon bowl banana apple sandwich orange "
        "broccoli carrot hot_dog pizza donut cake chair couch potted_plant bed dining_table toilet tv laptop "
        "mouse remote keyboard cell_phone microwave oven toaster sink refrigerator book clock vase scissors "
        "teddy_bear hair_drier toothbrush").split()
COCO_ANIMALS = set(range(14, 24))            # bird .. giraffe
AUDIO_HUMAN = [i for i in range(0, 67) if i not in (5, 30)]   # speech, shouts, laughter, cough, footsteps...
AUDIO_ANIMAL = [i for i in range(67, 132) if i != 87]         # dog, cat, birds, insects, livestock...
HOLD_S = 1.5        # keep a positive reading this long so the verdict doesn't flicker
STALE_S = 3.0       # a sensor with no update for this long counts as offline


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ------------------------------------------------------------------ shared state
class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.jpeg = {}
        self.sensors = {k: {"status": "starting", "human": 0.0, "animal": 0.0, "detail": "", "t": 0.0,
                            "hold_h": 0.0, "hold_a": 0.0, "t_h": 0.0, "t_a": 0.0}
                        for k in ("thermal", "visual", "audio")}
        self.verdict = {"label": "STARTING", "level": "idle", "reasons": []}
        self.events = []
        self.stop = threading.Event()

    def frame(self, name, img, quality=80):
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if ok:
            with self.lock:
                self.jpeg[name] = buf.tobytes()

    def update(self, name, **kw):
        now = time.time()
        with self.lock:
            s = self.sensors[name]
            s.update(kw)
            s["t"] = now
            for key, hk, tk in (("human", "hold_h", "t_h"), ("animal", "hold_a", "t_a")):
                v = float(kw.get(key, 0.0))
                if v >= s[hk] or now - s[tk] > HOLD_S:
                    s[hk], s[tk] = v, now

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps({"sensors": self.sensors, "verdict": self.verdict,
                                          "events": self.events[-12:], "time": time.time()}))


# ------------------------------------------------------------------ YOLO via OpenCV DNN
class Yolo:
    """YOLOv8-style ONNX detector (output 1 x (4+nc) x N) run with cv2.dnn, no PyTorch needed."""

    def __init__(self, path, size=320):
        self.net = cv2.dnn.readNetFromONNX(str(path))
        self.size = size

    def __call__(self, bgr, conf=0.35, iou=0.5):
        h, w = bgr.shape[:2]
        s = self.size / max(h, w)
        nh, nw = int(round(h * s)), int(round(w * s))
        canvas = np.full((self.size, self.size, 3), 114, np.uint8)
        top, left = (self.size - nh) // 2, (self.size - nw) // 2
        canvas[top:top + nh, left:left + nw] = cv2.resize(bgr, (nw, nh))
        self.net.setInput(cv2.dnn.blobFromImage(canvas, 1 / 255.0, swapRB=True))
        out = self.net.forward()[0].T                        # N x (4+nc)
        scores = out[:, 4:]
        cls = scores.argmax(1)
        cf = scores[np.arange(len(cls)), cls]
        keep = cf >= conf
        out, cls, cf = out[keep], cls[keep], cf[keep]
        if not len(out):
            return []
        cx, cy, bw, bh = out[:, 0], out[:, 1], out[:, 2], out[:, 3]
        x = (cx - bw / 2 - left) / s
        y = (cy - bh / 2 - top) / s
        boxes = np.stack([x, y, bw / s, bh / s], 1)
        idx = cv2.dnn.NMSBoxes(boxes.tolist(), cf.tolist(), conf, iou)
        res = []
        for i in np.array(idx).reshape(-1):
            bx, by, bww, bhh = boxes[i]
            res.append({"cls": int(cls[i]), "conf": float(cf[i]),
                        "box": [int(max(bx, 0)), int(max(by, 0)), int(min(bx + bww, w)), int(min(by + bhh, h))]})
        return res


def draw_box(img, box, text, color, scale=0.5):
    x1, y1, x2, y2 = (int(v) for v in box)
    k = max(img.shape[1] / 640.0, 1.0)                 # keep labels readable on large frames
    t = max(int(round(2 * k)), 2)
    cv2.rectangle(img, (x1, y1), (x2, y2), color, t)
    org = (x1, max(y1 - int(6 * k), int(16 * k)))
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale * k, (0, 0, 0), t + 2, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale * k, color, max(t - 1, 1), cv2.LINE_AA)


def placeholder(text, w=640, h=480):
    img = np.full((h, w, 3), 24, np.uint8)
    cv2.putText(img, text, (20, h // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (180, 180, 180), 2, cv2.LINE_AA)
    return img


# ------------------------------------------------------------------ thermal
def thermal_worker(st, args):
    import thermal_rtsp as tr
    model_path = MODELS / "thermal_person.onnx"
    model = Yolo(model_path, 320) if model_path.exists() else None
    log("thermal person model:", model_path.name if model else "none (warm-body detection only)")
    scale = 3
    while not st.stop.is_set():
        cam = tr.FakeReader() if args.thermal == "fake" else tr.open_camera(args.thermal_device, args.thermal_reader)
        if cam is None:
            st.update("thermal", status="camera not found", human=0, animal=0, detail="retrying")
            st.frame("thermal", placeholder("thermal camera not found"))
            time.sleep(3)
            continue
        n, persons = 0, []
        while not st.stop.is_set():
            buf = cam.read()
            if buf is None:
                break
            temps = tr.to_celsius(buf)
            dets, ambient, _ = tr.detect(temps)
            view, stats = tr.render(temps, scale)
            if model and n % args.thermal_every == 0:
                t_min, t_max = float(temps.min()), float(temps.max())
                g = (np.clip((temps - t_min) / max(t_max - t_min, 0.5), 0, 1) * 255).astype(np.uint8)
                persons = [d for d in model(cv2.cvtColor(g, cv2.COLOR_GRAY2BGR), conf=0.30) if d["cls"] == 0]
            n += 1
            for d in dets:
                b = [d["x"] * scale, d["y"] * scale, (d["x"] + d["w"]) * scale, (d["y"] + d["h"]) * scale]
                draw_box(view, b, f"warm {d['t_peak']:.1f}C", (0, 255, 0), 0.5)
            for p in persons:
                draw_box(view, [v * scale for v in p["box"]], f"PERSON {p['conf']:.2f}", (0, 0, 255), 0.6)
            p_conf = max((p["conf"] for p in persons), default=0.0)
            warm = len(dets) > 0
            human = max(p_conf, 0.55 if warm else 0.0)
            if p_conf >= 0.3:
                detail = f"person shape {p_conf:.2f}, peak {dets[0]['t_peak'] if dets else stats['t_max']:.1f} C"
            elif warm:
                detail = f"{len(dets)} warm body {dets[0]['t_peak']:.1f} C (ambient {ambient:.1f} C)"
            else:
                detail = f"no warm body (ambient {ambient:.1f} C)"
            st.update("thermal", status="ok", human=human, animal=0.0, warm=warm, person_model=p_conf,
                      detail=detail, t_max=stats["t_max"])
            st.frame("thermal", view)
        cam.close()


# ------------------------------------------------------------------ visual (Camera Module 3)
def open_visual(args):
    if args.rgb == "picamera":
        from picamera2 import Picamera2
        cam = Picamera2()
        cam.configure(cam.create_video_configuration(main={"size": (640, 480), "format": "RGB888"}))
        cam.start()
        try:
            from libcamera import controls
            cam.set_controls({"AfMode": controls.AfModeEnum.Continuous})
        except Exception as e:
            log("autofocus not set:", e)
        return cam.capture_array, cam.stop          # RGB888 arrays are BGR-ordered, as OpenCV expects
    if args.rgb == "usb":
        cap = cv2.VideoCapture(args.rgb_index, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise RuntimeError(f"USB camera {args.rgb_index} not found")
        return (lambda: cap.read()[1]), cap.release
    if args.rgb == "file":
        img = cv2.imread(args.rgb_file)
        if img is None:
            raise RuntimeError(f"can't read {args.rgb_file}")
        return (lambda: (time.sleep(0.1), img.copy())[1]), (lambda: None)
    raise RuntimeError(args.rgb)


def visual_worker(st, args):
    model = Yolo(MODELS / "coco_yolov8n_320.onnx", 320)
    while not st.stop.is_set():
        try:
            read, close = open_visual(args)
        except Exception as e:
            st.update("visual", status=f"camera error: {e}"[:80], human=0, animal=0, detail="retrying")
            st.frame("visual", placeholder("visual camera not available"))
            time.sleep(3)
            continue
        log("visual camera:", args.rgb)
        try:
            while not st.stop.is_set():
                img = read()
                if img is None:
                    break
                if img.ndim == 3 and img.shape[2] == 4:
                    img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                dets = model(img, conf=0.35)
                people = [d for d in dets if d["cls"] == 0]
                animals = [d for d in dets if d["cls"] in COCO_ANIMALS]
                for d in dets:
                    if d["cls"] == 0:
                        draw_box(img, d["box"], f"PERSON {d['conf']:.2f}", (0, 0, 255), 0.6)
                    elif d["cls"] in COCO_ANIMALS:
                        draw_box(img, d["box"], f"{COCO[d['cls']]} {d['conf']:.2f}", (255, 160, 0), 0.6)
                    else:
                        draw_box(img, d["box"], f"{COCO[d['cls']]}", (160, 160, 160), 0.45)
                human = max((d["conf"] for d in people), default=0.0)
                animal = max((d["conf"] for d in animals), default=0.0)
                if people:
                    detail = f"{len(people)} person (best {human:.2f})"
                elif animals:
                    detail = f"{COCO[animals[0]['cls']]} {animal:.2f}, no person"
                else:
                    others = sorted({COCO[d['cls']] for d in dets})[:3]
                    detail = "no person" + (f" (sees {', '.join(others)})" if others else "")
                st.update("visual", status="ok", human=human, animal=animal, detail=detail)
                st.frame("visual", img)
        except Exception as e:
            log("visual error:", e)
        finally:
            close()
        time.sleep(1)


# ------------------------------------------------------------------ audio (USB microphone)
class AudioSource:
    def __init__(self, args):
        self.args = args
        if args.audio == "arecord":
            self.p = subprocess.Popen(["arecord", "-q", "-D", args.mic, "-f", "S16_LE", "-r", "16000",
                                       "-c", "1", "-t", "raw"], stdout=subprocess.PIPE)
        else:
            w = wave.open(args.audio_file)
            if w.getframerate() != 16000 or w.getnchannels() != 1:
                raise RuntimeError("audio file must be 16 kHz mono wav")
            self.data = w.readframes(w.getnframes())
            self.pos = 0

    def read(self, n):
        """n samples as float32, blocking."""
        if self.args.audio == "arecord":
            b = self.p.stdout.read(n * 2)
            if not b or len(b) < n * 2:
                raise RuntimeError("microphone stopped (check --mic, see arecord -l)")
        else:
            time.sleep(n / 16000)
            if self.pos + n * 2 > len(self.data):
                self.pos = 0
            b = self.data[self.pos:self.pos + n * 2]
            self.pos += n * 2
        return np.frombuffer(b, "<i2").astype(np.float32) / 32768.0

    def close(self):
        if self.args.audio == "arecord":
            self.p.kill()


def spectrogram(buf, w=640, h=240):
    win = 400
    frames = np.lib.stride_tricks.sliding_window_view(buf, win)[::160] * np.hanning(win)
    mag = np.abs(np.fft.rfft(frames, axis=1))[:, :160]             # 0-6.4 kHz
    db = 20 * np.log10(mag + 1e-6)
    img = np.clip((db + 80) / 80, 0, 1).T[::-1]
    img = cv2.applyColorMap((img * 255).astype(np.uint8), cv2.COLORMAP_MAGMA)
    return cv2.resize(img, (w, h), interpolation=cv2.INTER_LINEAR)


def audio_worker(st, args):
    from ai_edge_litert.interpreter import Interpreter
    names = [r["display_name"] for r in csv.DictReader(open(MODELS / "yamnet_class_map.csv"))]
    it = Interpreter(str(MODELS / "yamnet.tflite"))
    it.allocate_tensors()
    inp, outp = it.get_input_details()[0]["index"], it.get_output_details()[0]["index"]
    ring = np.zeros(16000 * 4, np.float32)
    while not st.stop.is_set():
        try:
            src = AudioSource(args)
        except Exception as e:
            st.update("audio", status=f"mic error: {e}"[:80], human=0, animal=0, detail="retrying")
            st.frame("audio", placeholder("microphone not available", 640, 240))
            time.sleep(3)
            continue
        log("audio source:", args.audio, args.mic if args.audio == "arecord" else args.audio_file)
        try:
            while not st.stop.is_set():
                hop = src.read(8000)                        # classify every 0.5 s on the last 0.975 s
                ring = np.concatenate([ring[len(hop):], hop])
                it.set_tensor(inp, ring[-15600:])
                it.invoke()
                sc = it.get_tensor(outp).reshape(-1)
                hi = max(AUDIO_HUMAN, key=lambda i: sc[i])
                ai = max(AUDIO_ANIMAL, key=lambda i: sc[i])
                top = [(names[i], round(float(sc[i]), 2)) for i in sc.argsort()[::-1][:4]]
                rms = float(np.sqrt(np.mean(ring[-8000:] ** 2)))
                level_db = round(20 * np.log10(rms + 1e-6), 1)
                human, animal = float(sc[hi]), float(sc[ai])
                if human >= 0.3:
                    detail = f"{names[hi]} {human:.2f}"
                elif animal >= 0.3:
                    detail = f"{names[ai]} {animal:.2f} (not human)"
                else:
                    detail = f"{top[0][0]} {top[0][1]:.2f}" if level_db > -50 else "quiet"
                st.update("audio", status="ok", human=human, animal=animal, detail=detail, top=top,
                          level_db=level_db)
                spec = spectrogram(ring[-16000 * 3:])
                cv2.putText(spec, detail, (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
                cv2.putText(spec, detail, (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1, cv2.LINE_AA)
                st.frame("audio", spec)
        except Exception as e:
            log("audio error:", e)
            st.update("audio", status=f"mic error: {e}"[:80], human=0, animal=0, detail="retrying")
        finally:
            src.close()
        time.sleep(1)


# ------------------------------------------------------------------ fusion
def fuse(st, args):
    snaps = Path(args.log) / time.strftime("%Y%m%d_%H%M%S")
    snaps.mkdir(parents=True, exist_ok=True)
    events_f = open(snaps / "events.jsonl", "a")
    last_label, last_snap = None, 0.0
    log("event log + snapshots:", snaps)
    while not st.stop.is_set():
        time.sleep(0.2)
        now = time.time()
        with st.lock:
            s = {k: dict(v) for k, v in st.sensors.items()}
        live = {k: v for k, v in s.items() if v["status"] == "ok" and now - v["t"] < STALE_S}
        h = {k: (v["hold_h"] if now - v["t_h"] <= HOLD_S else v["human"]) for k, v in live.items()}
        a = {k: (v["hold_a"] if now - v["t_a"] <= HOLD_S else v["animal"]) for k, v in live.items()}
        strong = [k for k, v in h.items() if v >= 0.5]
        names = {"thermal": "heat", "visual": "visual", "audio": "sound"}
        reasons = [f"{names[k]}: {live[k]['detail']}" for k in ("thermal", "visual", "audio") if k in live]
        th = live.get("thermal", {})
        if not live:
            label, level = "NO SENSORS ONLINE", "idle"
        elif len(strong) >= 2:
            label, level = f"HUMAN CONFIRMED ({len(strong)}/3: {' + '.join(names[k] for k in strong)})", "human"
        elif strong == ["visual"] and "thermal" in live and not th.get("warm"):
            label, level = "VISUAL ONLY: no heat signature (photo or screen?)", "maybe"
        elif strong == ["thermal"] and th.get("person_model", 0) < 0.3:
            label, level = "WARM OBJECT: not confirmed as human", "maybe"
        elif len(strong) == 1:
            label, level = f"POSSIBLE HUMAN ({names[strong[0]]} only)", "maybe"
        elif any(v >= 0.4 for v in a.values()):
            k = max(a, key=a.get)
            label, level = f"ANIMAL ({names[k]}): not human", "animal"
        else:
            label, level = "NO HUMAN DETECTED", "none"
        with st.lock:
            st.verdict = {"label": label, "level": level, "reasons": reasons,
                          "scores": {k: round(v, 2) for k, v in h.items()}}
            if label != last_label:
                ev = {"time": time.strftime("%H:%M:%S"), "label": label, "level": level, "reasons": reasons}
                st.events.append(ev)
                events_f.write(json.dumps(ev) + "\n")
                events_f.flush()
            jpegs = dict(st.jpeg)
        if label != last_label:
            log("VERDICT:", label)
            last_label = label
        if level == "human" and now - last_snap > 5:
            last_snap = now
            for k, b in jpegs.items():
                (snaps / f"{time.strftime('%H%M%S')}_{k}.jpg").write_bytes(b)


# ------------------------------------------------------------------ web dashboard
DASHBOARD = (HERE / "dashboard.html").read_text(encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    st = None

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = DASHBOARD.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/state":
            body = json.dumps(self.st.snapshot()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/stream/"):
            name = self.path.split("/")[2].split("?")[0]
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            last = None
            try:
                while not self.st.stop.is_set():
                    with self.st.lock:
                        b = self.st.jpeg.get(name)
                    if b is not None and b is not last:
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(b)).encode() + b"\r\n\r\n" + b + b"\r\n")
                        last = b
                    time.sleep(0.05)
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif self.path.startswith("/snapshot/"):
            name = self.path.split("/")[2]
            with self.st.lock:
                b = self.st.jpeg.get(name)
            if b is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        else:
            self.send_error(404)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--thermal", choices=["auto", "fake", "off"], default="auto")
    ap.add_argument("--thermal-device", default=None, help="e.g. /dev/video0 (default: auto)")
    ap.add_argument("--thermal-reader", choices=["auto", "opencv", "ffmpeg"], default="auto")
    ap.add_argument("--thermal-every", type=int, default=3, help="run the thermal person model every N frames")
    ap.add_argument("--rgb", choices=["picamera", "usb", "file", "off"], default="picamera")
    ap.add_argument("--rgb-index", type=int, default=0)
    ap.add_argument("--rgb-file")
    ap.add_argument("--audio", choices=["arecord", "file", "off"], default="arecord")
    ap.add_argument("--mic", default="default", help="ALSA device, e.g. plughw:2,0 (see arecord -l)")
    ap.add_argument("--audio-file", help="16 kHz mono wav, for testing")
    ap.add_argument("--log", default=str(Path.home() / "fusion_logs"))
    args = ap.parse_args()

    st = State()
    for name, fn, mode in (("thermal", thermal_worker, args.thermal), ("visual", visual_worker, args.rgb),
                           ("audio", audio_worker, args.audio)):
        if mode == "off":
            st.update(name, status="off", detail="disabled")
            st.frame(name, placeholder(f"{name} off", 640, 240 if name == "audio" else 480))
            continue
        threading.Thread(target=fn, args=(st, args), daemon=True, name=name).start()
    threading.Thread(target=fuse, args=(st, args), daemon=True, name="fusion").start()

    Handler.st = st
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    srv.daemon_threads = True
    log(f"dashboard: http://localhost:{args.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        st.stop.set()


if __name__ == "__main__":
    main()
