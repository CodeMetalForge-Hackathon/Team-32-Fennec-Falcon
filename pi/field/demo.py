#!/usr/bin/env python3
"""Full-screen demo view for recording: http://<pi>:8081 (hotspot: http://10.42.0.1:8081).

One screen that shows the search the way a judge should see it: a big verdict banner (TARGET FOUND /
POSSIBLE TARGET / HUMAN, NOT THE TARGET / HUMAN, UNIDENTIFIED / NO HUMAN), the camera with the
TARGET/POSSIBLE/PERSON boxes hailo_vision draws, the live thermal picture, what the microphones heard,
one chip per sensor, a log of verdict changes, and whether the Pi has any network route at all.

Like the control page it never opens a sensor. It reads what the services already write:
  ~/eagle/vision*.jsonl        person boxes + identity [level, appearance, face] (hailo_vision)
  ~/eagle/cam_<name>.jpg       camera snapshots with boxes drawn (hailo_vision)
  ~/field_logs/live_thermal.npy latest thermal frame (life_node --record)
  ~/field_logs/latest/events.jsonl HEAT / SOUND detections and the life list (life_node --record)
  ~/field_logs/nest.log        what the Nest decrypted
Standard library only (numpy optional, for the thermal picture), reusing dashboard.py's helpers.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("eagle_dashboard", HERE / "dashboard.py")
dash = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dash)

TARGET_NAME = os.environ.get("EAGLE_TARGET_NAME", "Mitch")
CAMERAS = ("standard", "noir")
VISION_WINDOW_S = 2.5        # identity/boxes newer than this count as "now"
HEAT_WINDOW_S = 6.0          # thermal detections arrive about every 0.5 s
SOUND_WINDOW_S = 10.0        # a sound event is classified over ~1-3 s of audio, then reported
TARGET_HOLD_S = 3.0          # keep TARGET FOUND up this long after the last level-3 frame
HUMAN_SOUNDS = {"VOICE": "voice", "DISTRESS": "shout / distress", "BREATH": "breathing", "COUGH": "cough",
                "SIGNAL": "whistle / knock", "MOVE": "footsteps / movement"}
LEVEL_NAMES = {1: "appearance", 2: "strong appearance", 3: "face verified"}


# ------------------------------------------------------------------ inputs
def latest_vision_lines(eagle_dir: Path, n: int = 40) -> list:
    files = sorted(eagle_dir.glob("vision*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for p in files[:2]:                                   # standard + noir instances
        for line in dash.tail_lines(p, n, max_bytes=64_000):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict) and "boxes" in d:
                d["_src"] = p.stem.replace("vision_", "").replace("vision", "camera") or "camera"
                out.append(d)
    return out


def vision_state(lines: list, now: float) -> dict:
    """People in view and the best identity across cameras over the last VISION_WINDOW_S."""
    recent = [d for d in lines if now - float(d.get("t", 0)) <= VISION_WINDOW_S]
    people, best, face_not_target = 0, None, None
    for d in recent:
        people = max(people, len(d.get("boxes") or []))
        for ident in d.get("identity") or []:
            lv, app, face = int(ident[0]), ident[1], ident[2]
            cand = {"level": lv, "appearance": app, "face": face, "t": float(d.get("t", 0))}
            if best is None or (lv, face or -1) > (best["level"], best["face"] or -1):
                best = cand
            if lv == 0 and face is not None:
                face_not_target = face if face_not_target is None else max(face_not_target, face)
    return {"people": people, "best": best, "face_not_target": face_not_target,
            "age_s": (now - max(float(d.get("t", 0)) for d in lines)) if lines else None}


def life_state(run_dir: Path | None, now: float) -> dict:
    heat, sounds, persons = None, [], 0
    if run_dir:
        for line in dash.tail_lines(run_dir / "events.jsonl", 600):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            age = now - float(e.get("t", 0))
            if e.get("event") == "detection":
                if e.get("kind") == "HEAT" and age <= HEAT_WINDOW_S:
                    t = e.get("temp_c")
                    heat = t if heat is None or (t is not None and t > heat) else heat
                elif e.get("kind") == "SOUND" and age <= SOUND_WINDOW_S and e.get("category") in HUMAN_SOUNDS:
                    sounds.append((age, e["category"]))
            elif e.get("event") == "life_list" and age <= HEAT_WINDOW_S:
                persons = len(e.get("persons") or [])
    cats = []
    for _, c in sorted(sounds):
        if c not in cats:
            cats.append(c)
    return {"heat_c": heat, "sounds": cats, "tracks": persons}


def network_state() -> str:
    """'offline' when the kernel has no default route (nothing beyond the Pi's own hotspot)."""
    try:
        with open("/proc/net/route") as f:
            rows = [l.split() for l in f.readlines()[1:]]
        return "online" if any(r[1] == "00000000" and r[7] == "00000000" for r in rows if len(r) > 7) else "offline"
    except OSError:
        return "unknown"


# ------------------------------------------------------------------ verdict
class Verdict:
    def __init__(self):
        self.lock = threading.Lock()
        self.last_target_t = -1e9
        self.last_key = None
        self.log = deque(maxlen=12)

    def decide(self, vis: dict, life: dict, now: float) -> dict:
        best = vis["best"]
        heat, sounds = life["heat_c"], life["sounds"]
        human_evidence = bool(vis["people"] or heat is not None or sounds)
        with self.lock:
            if best and best["level"] >= 3:
                self.last_target_t = now
            if now - self.last_target_t <= TARGET_HOLD_S:
                key, level = "target", 3
                title = f"TARGET FOUND: {TARGET_NAME.upper()}"
            elif best and best["level"] in (1, 2):
                key, level = "possible", best["level"]
                title = f"POSSIBLE TARGET: {TARGET_NAME.upper()}"
            elif vis["people"] and vis["face_not_target"] is not None:
                key, level = "not_target", 0
                title = f"HUMAN DETECTED: NOT {TARGET_NAME.upper()}"
            elif human_evidence:
                key, level = "human", 0
                title = "HUMAN DETECTED: NOT YET IDENTIFIED"
            else:
                key, level = "none", 0
                title = "NO HUMAN DETECTED"
            parts = []
            if best and best["face"] is not None and best["level"] >= 3:
                parts.append(f"face match {best['face']:.2f}")
            elif best and best["level"] in (1, 2):
                parts.append(f"{LEVEL_NAMES[best['level']]} {best['appearance']:.2f}")
            if vis["face_not_target"] is not None and key == "not_target":
                parts.append(f"face seen, match {vis['face_not_target']:.2f} (below threshold)")
            if heat is not None:
                parts.append(f"body heat {heat:.1f} °C")
            if sounds:
                parts.append("heard " + ", ".join(HUMAN_SOUNDS[c] for c in sounds))
            if vis["people"]:
                parts.append(f"{vis['people']} person{'s' if vis['people'] != 1 else ''} in view")
            sub = " · ".join(parts) if parts else "scanning: thermal, cameras and microphones"
            if key != self.last_key:
                self.log.appendleft({"time": time.strftime("%H:%M:%S"), "key": key, "title": title, "sub": sub})
                self.last_key = key
            return {"key": key, "level": level, "title": title, "sub": sub, "log": list(self.log)}


def state(logs: Path, eagle_dir: Path, verdict: Verdict) -> dict:
    now = time.time()
    run = logs / "latest"
    run_dir = run.resolve() if run.exists() else None
    vis = vision_state(latest_vision_lines(eagle_dir), now)
    life = life_state(run_dir, now)
    v = verdict.decide(vis, life, now)
    best = vis["best"]
    chips = {
        "heat": {"on": life["heat_c"] is not None,
                 "text": f"{life['heat_c']:.1f} °C warm body" if life["heat_c"] is not None else "no warm body"},
        "sound": {"on": bool(life["sounds"]),
                  "text": ", ".join(HUMAN_SOUNDS[c] for c in life["sounds"]) if life["sounds"] else "no human sounds"},
        "vision": {"on": bool(vis["people"]),
                   "text": f"{vis['people']} person(s)" if vis["people"] else "no person in view"},
        "face": {"on": bool(best and best["level"] >= 3),
                 "text": (f"{TARGET_NAME} {best['face']:.2f}" if best and best["level"] >= 3 and best["face"] is not None
                          else f"not {TARGET_NAME} ({vis['face_not_target']:.2f})" if vis["face_not_target"] is not None
                          else "no face close enough")},
    }
    nest = [l for l in dash.tail_lines(logs / "nest.log", 300) if "RX" in l or "COT" in l or "FIX" in l][-3:]
    return {"time": time.strftime("%H:%M:%S"), "verdict": v, "chips": chips, "network": network_state(),
            "target_enrolled": (eagle_dir / "target_profile.npz").exists(), "nest": nest,
            "ages": {"vision_s": None if vis["age_s"] is None else round(vis["age_s"], 1),
                     "thermal_s": dash.age_s(logs / "live_thermal.npy"),
                     **{f"cam_{c}_s": dash.age_s(eagle_dir / f"cam_{c}.jpg") for c in CAMERAS}}}


# ------------------------------------------------------------------ page
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Eagle: search view</title>
<style>
:root{--bg:#0b0d10;--card:#151a20;--fg:#eef1f4;--mut:#8d96a0;--green:#22c55e;--amber:#f5a524;--red:#ef4444;--blue:#3b82f6}
*{box-sizing:border-box}html,body{height:100%}body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.35 system-ui,-apple-system,sans-serif;display:flex;flex-direction:column;padding:12px;gap:12px}
#banner{border-radius:14px;padding:16px 22px;background:#252b33;transition:background .25s}
#banner.target{background:var(--green);color:#04210f}#banner.possible{background:var(--amber);color:#2a1800}
#banner.not_target{background:var(--red);color:#fff}#banner.human{background:var(--blue);color:#fff}
#title{font-size:clamp(26px,3.6vw,52px);font-weight:800;letter-spacing:.01em}
#sub{font-size:clamp(14px,1.5vw,20px);opacity:.9;margin-top:4px}
#main{flex:1;display:grid;grid-template-columns:2fr 1fr;gap:12px;min-height:0}
.card{background:var(--card);border-radius:14px;padding:10px;display:flex;flex-direction:column;min-height:0}
.label{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.08em;margin:0 0 6px}
.cam{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;background:#000;border-radius:10px;overflow:hidden}
.cam img{max-width:100%;max-height:100%;object-fit:contain}
#side{display:flex;flex-direction:column;gap:12px;min-height:0}#side .card{flex:1}
#chips{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.chip{border-radius:10px;padding:8px 10px;background:#1f252d;border:2px solid transparent}
.chip.on{border-color:var(--green);background:#11281a}.chip b{display:block;font-size:13px;color:var(--mut)}
.chip.on b{color:var(--green)}.chip span{font-size:15px}
#log{font:13px/1.5 ui-monospace,monospace;color:var(--mut);overflow:hidden}
#log .target{color:var(--green)}#log .possible{color:var(--amber)}#log .not_target{color:#ff8080}#log .human{color:#8ab4ff}
#foot{display:flex;gap:18px;font-size:13px;color:var(--mut);flex-wrap:wrap}.ok{color:var(--green)}.warn{color:var(--amber)}
@media(max-width:800px){#main{grid-template-columns:1fr}}
</style></head><body>
<div id="banner"><div id="title">Starting...</div><div id="sub"></div></div>
<div id="main">
  <div class="card"><div class="label">Camera: <span id="camname">standard</span></div>
    <div class="cam"><img id="cam" alt="camera"></div></div>
  <div id="side">
    <div class="card"><div class="label">Thermal</div><div class="cam"><img id="thermal" alt="thermal"></div></div>
    <div class="card" style="flex:0 0 auto"><div class="label">Sensors</div><div id="chips"></div></div>
    <div class="card"><div class="label">Verdict log</div><div id="log"></div></div>
  </div>
</div>
<div id="foot"><span id="clock"></span><span id="net"></span><span id="enrolled"></span>
<span>Processing: on-device (Raspberry Pi 5 + Hailo-8), no cloud</span><span id="nest"></span></div>
<script>
const $=id=>document.getElementById(id);const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
let cam='standard';
async function poll(){let s;try{s=await (await fetch('/state')).json()}catch(e){$('clock').textContent='(no connection)';return}
  const v=s.verdict;$('banner').className=v.key;$('title').textContent=v.title;$('sub').textContent=v.sub;
  const names={heat:'Heat',sound:'Sound',vision:'Vision',face:'Face ID'};
  $('chips').innerHTML=Object.entries(s.chips).map(([k,c])=>`<div class="chip ${c.on?'on':''}"><b>${names[k]}</b><span>${esc(c.text)}</span></div>`).join('');
  $('log').innerHTML=v.log.map(l=>`<div class="${l.key}">${l.time}  ${esc(l.title)}</div>`).join('')||'waiting for the first detection';
  $('clock').textContent=s.time;
  $('net').innerHTML=s.network==='offline'?'<span class="ok">● Network: OFFLINE (no internet)</span>':'<span class="warn">● Network: '+s.network+'</span>';
  $('enrolled').innerHTML=s.target_enrolled?'<span class="ok">Target enrolled</span>':'<span class="warn">No target enrolled</span>';
  $('nest').textContent=s.nest.length?'Rescuer link: '+s.nest[s.nest.length-1].slice(0,90):'';
  const a=s.ages;cam=(a.cam_standard_s!=null&&a.cam_standard_s<5)||a.cam_noir_s==null||a.cam_noir_s>5?'standard':'noir';$('camname').textContent=cam==='standard'?'standard':'NoIR wide';
}
function loadImg(id,url,ms){const el=$(id);let busy=false;setInterval(()=>{if(busy)return;busy=true;const im=new Image();
  im.onload=()=>{el.src=im.src;busy=false};im.onerror=()=>{busy=false};im.src=(typeof url==='function'?url():url)+'?'+Date.now()},ms)}
loadImg('cam',()=>'/cam/'+cam+'.jpg',333);loadImg('thermal','/thermal.png',500);
poll();setInterval(poll,500);
</script></body></html>"""


def make_handler(logs: Path, eagle_dir: Path, verdict: Verdict):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif path == "/state":
                self._send(200, json.dumps(state(logs, eagle_dir, verdict), default=str).encode(), "application/json")
            elif path == "/thermal.png":
                r = dash.thermal_png(logs / "live_thermal.npy", scale=3)
                self._send(200, r[0], "image/png") if r else self.send_error(404)
            elif path.startswith("/cam/") and path.endswith(".jpg") and path[5:-4] in CAMERAS:
                try:
                    self._send(200, (eagle_dir / f"cam_{path[5:-4]}.jpg").read_bytes(), "image/jpeg")
                except OSError:
                    self.send_error(404)
            else:
                self.send_error(404)

        def log_message(self, *a):
            pass
    return H


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--logs", default="~/field_logs")
    ap.add_argument("--eagle", default="~/eagle")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8081)
    a = ap.parse_args(argv)
    logs, eagle_dir = Path(os.path.expanduser(a.logs)), Path(os.path.expanduser(a.eagle))
    ThreadingHTTPServer((a.host, a.port), make_handler(logs, eagle_dir, Verdict())).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
