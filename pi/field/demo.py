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
import shutil
import signal
import subprocess
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
TARGET_HOLD_S = 3.0
SIGNAL_SHOW_S = 300.0       # keep SIGNAL SENT on screen this long after the Nest received the FIX          # keep TARGET FOUND up this long after the last level-3 frame
HUMAN_SOUNDS = {"VOICE": "voice", "DISTRESS": "shout / distress", "BREATH": "breathing", "COUGH": "cough",
                "SIGNAL": "whistle / knock", "MOVE": "footsteps / movement"}
LEVEL_NAMES = {1: "appearance", 2: "strong appearance", 3: "face verified"}


# ------------------------------------------------------------------ pictures
def thermal_view(path: Path):
    """-> (png, min C, max C, age s) of the latest thermal frame, or None. Denoised (3x3 mean) and stretched
    to the temperatures actually in view (2nd..99.7th percentile, at least 6 C wide) so a person stands out
    instead of sensor speckle. Sent at native resolution; the browser scales it smoothly."""
    try:
        import numpy as np
        c = np.load(path).astype(np.float32) / 100.0
    except Exception:
        return None
    h, w = c.shape
    k = np.pad(c, 1, mode="edge")
    c = sum(k[i:i + h, j:j + w] for i in range(3) for j in range(3)) / 9.0
    lo, hi = (float(v) for v in np.percentile(c, [2.0, 99.7]))
    if hi - lo < 6.0:
        lo = hi - 6.0
    idx = (np.clip((c - lo) / (hi - lo), 0, 1) * 255).astype(np.uint8)
    rows = b"".join(b"\x00" + idx[r].tobytes() for r in range(h))
    return dash._png(w, h, rows), float(c.min()), float(c.max()), dash.age_s(path)


def audio_view(path: Path):
    """-> (spectrogram png, level dBFS of the last 0.5 s, age s), or None. Last ~4 s, 0-8 kHz, low at the
    bottom, 70 dB of range below the loudest bin."""
    try:
        import numpy as np
        d = np.load(path)
        x = d["pcm"].astype(np.float32) / 32768.0
        fs = int(d["fs"])
    except Exception:
        return None
    n, hop = 512, 256
    if len(x) < n:
        return None
    frames = np.lib.stride_tricks.sliding_window_view(x, n)[::hop] * np.hanning(n)
    spec = 20 * np.log10(np.abs(np.fft.rfft(frames, axis=1)) + 1e-9)        # (T, 257)
    spec = spec[:, : n // 2]                                                # 0 .. fs/2
    top = max(float(spec.max()), -60.0)
    img = (np.clip((spec - (top - 70.0)) / 70.0, 0, 1) * 255).astype(np.uint8).T[::-1]   # (F, T), low freq bottom
    img = img.repeat(2, 1)
    h, w = img.shape
    rows = b"".join(b"\x00" + img[r].tobytes() for r in range(h))
    tail = x[-fs // 2:]
    level = float(20 * np.log10(np.sqrt(np.mean(tail ** 2)) + 1e-9))
    return dash._png(w, h, rows), level, dash.age_s(path)


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

    def note_signal(self, sig: dict) -> None:
        """One verdict-log line per FIX received by the Nest."""
        with self.lock:
            if sig["seq"] != getattr(self, "last_signal_seq", None):
                self.last_signal_seq = sig["seq"]
                self.log.appendleft({"time": time.strftime("%H:%M:%S"), "key": "signal",
                                     "title": f"SIGNAL SENT TO RESCUE HQ (FIX #{sig['seq']}, ID-{sig['level']})", "sub": ""})


FIX_RE = None


def target_signal(nest_log: Path, now: float):
    """Latest FIX the Nest received and decrypted that carries an identity match, from lines like
    '[09:44:12] RX      FIX eagle 1 seq 5: 25.1234567, 121.1234567 alt 0 m ce 2 m class UNKNOWN+ID-FACE conf 0.87 ...'.
    -> dict(age_s, lat, lon, ce_m, level, seq) or None. Only a message that actually arrived counts."""
    import re
    global FIX_RE
    if FIX_RE is None:
        FIX_RE = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] RX\s+FIX eagle \S+ seq (\d+): ([-\d.]+), ([-\d.]+) .*?ce (\d+) m "
                            r"class \S*ID-(FACE|STRONG|APPEARANCE)")
    for line in reversed(dash.tail_lines(nest_log, 400)):
        m = FIX_RE.search(line)
        if not m:
            continue
        hh, mm, ss = (int(v) for v in m.group(1, 2, 3))
        lt = time.localtime(now)
        t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, ss, 0, 0, -1))
        if t > now + 60:                                  # logged before midnight
            t -= 86400
        return {"age_s": round(now - t), "seq": int(m.group(4)), "lat": float(m.group(5)), "lon": float(m.group(6)),
                "ce_m": int(m.group(7)), "level": m.group(8)}
    return None


def state(logs: Path, eagle_dir: Path, verdict: Verdict) -> dict:
    now = time.time()
    run = logs / "latest"
    run_dir = run.resolve() if run.exists() else None
    vis = vision_state(latest_vision_lines(eagle_dir), now)
    life = life_state(run_dir, now)
    sig = target_signal(logs / "nest.log", now)
    if sig and sig["age_s"] <= SIGNAL_SHOW_S:
        verdict.note_signal(sig)
    else:
        sig = None
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
    th = thermal_view(logs / "live_thermal.npy")
    au = audio_view(logs / "live_audio.npz")
    feeds = {"thermal": {"age_s": None if th is None or th[3] is None else round(th[3], 1),
                         "min_c": None if th is None else round(th[1], 1), "max_c": None if th is None else round(th[2], 1)},
             "audio": {"age_s": None if au is None or au[2] is None else round(au[2], 1),
                       "level_db": None if au is None else round(au[1], 1)}}
    nest = [l for l in dash.tail_lines(logs / "nest.log", 300) if "RX" in l or "COT" in l or "FIX" in l][-3:]
    return {"time": time.strftime("%H:%M:%S"), "verdict": v, "chips": chips, "network": network_state(), "signal": sig,
            "target_enrolled": (eagle_dir / "target_profile.npz").exists(), "nest": nest, "feeds": feeds,
            "ages": {"vision_s": None if vis["age_s"] is None else round(vis["age_s"], 1),
                     "thermal_s": dash.age_s(logs / "live_thermal.npy"),
                     **{f"cam_{c}_s": dash.age_s(eagle_dir / f"cam_{c}.jpg") for c in CAMERAS}}}


# ------------------------------------------------------------------ page
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Eagle: search view</title>
<style>
:root{--bg:#0b0d10;--card:#151a20;--fg:#eef1f4;--mut:#8d96a0;--green:#22c55e;--amber:#f5a524;--red:#ef4444;--blue:#3b82f6}
*{box-sizing:border-box}html,body{height:100%;margin:0}body{background:var(--bg);color:var(--fg);
font:16px/1.35 system-ui,-apple-system,sans-serif;display:flex;flex-direction:column;padding:10px;gap:10px;overflow:hidden}
#banner{border-radius:12px;padding:10px 20px;background:#252b33;transition:background .25s;display:flex;align-items:baseline;gap:18px;flex-wrap:wrap}
#banner.target{background:var(--green);color:#04210f}#banner.possible{background:var(--amber);color:#2a1800}
#banner.not_target{background:var(--red);color:#fff}#banner.human{background:var(--blue);color:#fff}
#signal{margin-left:auto;display:none;align-items:center;gap:12px;background:#0f2a18;color:#d6ffe4;
border:3px solid var(--green);border-radius:12px;padding:8px 16px;animation:pulse 1.6s ease-in-out 3}
#signal svg{width:38px;height:38px;flex:none}#signal b{display:block;font-size:clamp(16px,1.7vw,26px);letter-spacing:.02em}
#signal span{font-size:13px;opacity:.85}@keyframes pulse{50%{box-shadow:0 0 0 10px rgba(34,197,94,.25)}}
#log .signal{color:#7dffb0;font-weight:700}
#title{font-size:clamp(24px,3.2vw,46px);font-weight:800}#sub{font-size:clamp(13px,1.3vw,19px);opacity:.92}
#views{flex:1;display:grid;grid-template-columns:1fr 1fr;gap:10px;min-height:0}
.card{background:var(--card);border-radius:12px;padding:8px;display:flex;flex-direction:column;min-height:0;position:relative}
.label{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.08em;margin:0 0 6px;display:flex;justify-content:space-between}
.view{flex:1;min-height:0;background:#000;border-radius:8px;overflow:hidden;position:relative}
.view img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}
#thermal{image-rendering:auto}#audio{object-fit:fill}
.stale{position:absolute;left:8px;right:8px;bottom:8px;background:rgba(239,68,68,.92);color:#fff;font-weight:700;
border-radius:8px;padding:6px 10px;font-size:14px;display:none;z-index:2}
#soundrow{height:19vh;min-height:120px;display:grid;grid-template-columns:1fr auto;gap:10px}
#sound .view{background:#0a0610}
#meterwrap{display:flex;align-items:center;gap:10px;margin-top:6px}
#meter{flex:1;height:14px;border-radius:7px;background:#1f252d;overflow:hidden}
#meterbar{height:100%;width:0;background:linear-gradient(90deg,#22c55e,#f5a524 70%,#ef4444);transition:width .15s}
#heard{font-size:15px;min-width:220px;text-align:right}#heard.on{color:var(--green);font-weight:700}
#side{width:330px;display:flex;flex-direction:column;gap:8px}
#chips{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.chip{border-radius:9px;padding:6px 9px;background:#1f252d;border:2px solid transparent}
.chip.on{border-color:var(--green);background:#11281a}.chip b{display:block;font-size:12px;color:var(--mut)}
.chip.on b{color:var(--green)}.chip span{font-size:14px}
#log{font:12px/1.45 ui-monospace,monospace;color:var(--mut);overflow:hidden;flex:1}
#log .target{color:var(--green)}#log .possible{color:var(--amber)}#log .not_target{color:#ff8080}#log .human{color:#8ab4ff}
.zoomable{cursor:zoom-in}
.cap{position:absolute;left:10px;top:10px;z-index:2;padding:6px 12px;border-radius:8px;font-weight:800;
font-size:clamp(14px,1.4vw,20px);background:rgba(37,43,51,.85);color:#cfd5db}
.cap.on{background:rgba(34,197,94,.92);color:#04210f}
.card.big .cap{font-size:clamp(22px,2.6vw,40px);padding:10px 18px}
.card.big{position:fixed;inset:10px;z-index:20;cursor:zoom-out;box-shadow:0 0 0 9999px rgba(0,0,0,.85)}
#hint{font-size:12px;color:var(--mut)}
#rec{border:0;border-radius:8px;padding:4px 12px;font:600 13px system-ui;background:#2b3036;color:var(--fg);cursor:pointer}
#rec.on{background:var(--red);color:#fff;animation:blink 1.2s infinite}@keyframes blink{50%{opacity:.6}}
#toast{position:fixed;left:50%;bottom:44px;transform:translateX(-50%);background:#222a33;color:#fff;padding:8px 14px;
border-radius:8px;font-size:14px;display:none;z-index:30;max-width:80vw}
#foot{display:flex;align-items:center;gap:16px;font-size:12px;color:var(--mut);flex-wrap:wrap}.ok{color:var(--green)}.warn{color:var(--amber)}
</style></head><body>
<div id="banner"><div id="title">Starting...</div><div id="sub"></div>
<div id="signal"><svg viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2" stroke-linecap="round">
<path d="M12 13v8M9 21h6"/><circle cx="12" cy="11" r="2" fill="#22c55e"/><path d="M8.5 7.5a5 5 0 0 0 0 7M15.5 7.5a5 5 0 0 1 0 7"/>
<path d="M5.6 4.6a9 9 0 0 0 0 12.8M18.4 4.6a9 9 0 0 1 0 12.8"/></svg>
<div><b>SIGNAL SENT TO RESCUE HQ</b><span id="sigtxt"></span></div></div></div>
<div id="views">
  <div class="card"><div class="label"><span>Camera: <span id="camname">standard</span></span><span id="camage"></span></div>
    <div class="view"><img id="cam" alt="camera"><div class="stale" id="cstale"></div></div></div>
  <div class="card"><div class="label"><span>Thermal</span><span id="trange"></span></div>
    <div class="view"><div class="cap" id="tcap">No warm body</div><img id="thermal" alt="thermal"><div class="stale" id="tstale"></div></div></div>
</div>
<div id="soundrow">
  <div class="card" id="sound"><div class="label"><span>Sound (last 4 s, 0 to 8 kHz)</span><span id="alevel"></span></div>
    <div class="view"><img id="audio" alt="sound"><div class="stale" id="astale"></div></div>
    <div id="meterwrap"><div id="meter"><div id="meterbar"></div></div><div id="heard">no human sounds</div></div></div>
  <div class="card" id="side"><div id="chips"></div><div id="log"></div></div>
</div>
<div id="toast"></div>
<div id="foot"><button id="rec" title="Record the screen (R)">● REC</button><a id="vids" href="/videos" target="_blank" style="color:var(--mut);font-size:12px">Recordings</a><span id="clock"></span><span id="net"></span><span id="enrolled"></span>
<span>All processing on-device (Raspberry Pi 5 + Hailo-8), no cloud</span><span id="hint">Click a panel (or press 1 camera, 2 thermal, 3 sound) to enlarge, Esc to return, R to record</span><span id="nest"></span></div>
<script>
const $=id=>document.getElementById(id);const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
let cam='standard';
const stale=(id,age,lim,msg)=>{const e=$(id);if(age==null||age>lim){e.style.display='block';e.textContent=msg+(age!=null?' (last update '+Math.round(age)+' s ago)':'')}else e.style.display='none'};
async function poll(){let s;try{s=await (await fetch('/state')).json()}catch(e){$('clock').textContent='(no connection to the Pi)';return}
  const v=s.verdict;$('banner').className=v.key;$('title').textContent=v.title;$('sub').textContent=v.sub;
  const names={heat:'Heat',sound:'Sound',vision:'Vision',face:'Face ID'};
  $('chips').innerHTML=Object.entries(s.chips).map(([k,c])=>`<div class="chip ${c.on?'on':''}"><b>${names[k]}</b><span>${esc(c.text)}</span></div>`).join('');
  $('log').innerHTML=v.log.slice(0,6).map(l=>`<div class="${l.key}">${l.time}  ${esc(l.title)}</div>`).join('')||'waiting for the first detection';
  const sg=s.signal,se=$('signal');
  if(sg){se.style.display='flex';const ag=sg.age_s<60?sg.age_s+' s ago':Math.round(sg.age_s/60)+' min ago';
    $('sigtxt').textContent=`Encrypted 48-byte FIX · ${sg.lat.toFixed(5)}, ${sg.lon.toFixed(5)} ±${sg.ce_m} m · ID-${sg.level} · ${ag}`}
  else se.style.display='none';
  const f=s.feeds,a=s.ages;
  stale('tstale',f.thermal.age_s,5,'THERMAL PAUSED: life_node stopped or recording off');
  stale('astale',f.audio.age_s,5,'SOUND PAUSED: life_node stopped or recording off');
  $('trange').textContent=f.thermal.min_c!=null?`${f.thermal.min_c} to ${f.thermal.max_c} °C`:'';
  const lv=f.audio.level_db;$('alevel').textContent=lv!=null?lv.toFixed(0)+' dBFS':'';
  $('meterbar').style.width=lv==null?'0%':Math.max(0,Math.min(100,(lv+70)/60*100))+'%';
  const hc=s.chips.heat,tc=$('tcap');tc.className='cap'+(hc.on?' on':'');
  const corro=hc.on&&(s.chips.sound.on||s.chips.vision.on);
  tc.textContent=!hc.on?'No warm body':corro?('HUMAN DETECTED (heat + '+[s.chips.sound.on?'voice/sound':'',s.chips.vision.on?'camera':''].filter(Boolean).join(' + ')+')'):'WARM BODY DETECTED';
  const h=$('heard');h.textContent=s.chips.sound.on?'Heard: '+s.chips.sound.text:'no human sounds';h.className=s.chips.sound.on?'on':'';
  cam=(a.cam_standard_s!=null&&a.cam_standard_s<5)||a.cam_noir_s==null||a.cam_noir_s>5?'standard':'noir';
  $('camname').textContent=cam==='standard'?'standard':'NoIR wide';
  stale('cstale',cam==='standard'?a.cam_standard_s:a.cam_noir_s,4,'CAMERA PAUSED: vision service not running (Hailo or camera)');
  $('clock').textContent=s.time;
  $('net').innerHTML=s.network==='offline'?'<span class="ok">● OFFLINE: no internet</span>':'<span class="warn">● Network: '+s.network+'</span>';
  $('enrolled').innerHTML=s.target_enrolled?'<span class="ok">Target enrolled</span>':'<span class="warn">No target enrolled</span>';
  const r=s.screenrec||{};const rb=$('rec');rb.className=r.on?'on':'';
  rb.textContent=r.on?`■ STOP ${Math.floor(r.elapsed_s/60)}:${String(r.elapsed_s%60).padStart(2,'0')}`:'● REC';
  $('nest').textContent=s.nest.length?'Rescuer link: '+s.nest[s.nest.length-1].slice(0,80):'';
}
function loadImg(id,url,ms){const el=$(id);let busy=false;setInterval(()=>{if(busy)return;busy=true;const im=new Image();
  im.onload=()=>{el.src=im.src;busy=false};im.onerror=()=>{busy=false};im.src=(typeof url==='function'?url():url)+'?'+Date.now()},ms)}
loadImg('cam',()=>'/cam/'+cam+'.jpg',120);loadImg('thermal','/thermal.png',400);loadImg('audio','/audio.png',250);
function toast(t){const e=$('toast');e.textContent=t;e.style.display='block';clearTimeout(e._t);e._t=setTimeout(()=>e.style.display='none',6000)}
async function toggleRec(){const on=$('rec').classList.contains('on');$('rec').disabled=true;
  try{const j=await (await fetch(on?'/rec/stop':'/rec/start',{method:'POST'})).json();toast(j.message)}catch(e){toast('no answer from the Pi')}
  $('rec').disabled=false;poll()}
$('rec').addEventListener('click',e=>{e.stopPropagation();toggleRec()});
const zoomCards=[...document.querySelectorAll('#views .card, #sound')];
zoomCards.forEach(c=>{c.classList.add('zoomable');c.addEventListener('click',()=>toggleZoom(c))});
function toggleZoom(c){const was=!!c&&c.classList.contains('big');zoomCards.forEach(x=>x.classList.remove('big'));if(!was&&c)c.classList.add('big')}
document.addEventListener('keydown',e=>{if(e.key==='r'||e.key==='R'){toggleRec();return}if(e.key==='Escape')toggleZoom(null);const i={'1':0,'2':1,'3':2}[e.key];if(i!==undefined)toggleZoom(zoomCards[i])});
poll();setInterval(poll,500);
</script></body></html>"""


class ScreenRecorder:
    """Records the Pi's own screen to ~/Videos/eagle_demo_<time>.mp4, started and stopped from the page.
    Wayland desktop (Raspberry Pi OS default): wf-recorder. X11 desktop: ffmpeg x11grab. Both stop
    cleanly on SIGINT so the MP4 is finalized. The service runs as the desktop user, so it can reach the
    session's display socket. libx264 ultrafast at 15 fps keeps the CPU cost modest on a Pi 5."""

    def __init__(self, out_dir: Path, fps: int = 15):
        self.out_dir, self.fps = out_dir, fps
        self.lock = threading.Lock()
        self.proc, self.path, self.t0, self.error = None, None, None, None

    def _command(self, out: Path):
        rt = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        env = dict(os.environ, XDG_RUNTIME_DIR=rt)
        socks = sorted(Path(rt).glob("wayland-[0-9]"))
        if socks:
            if not shutil.which("wf-recorder"):
                return None, None, "wf-recorder is not installed: run  sudo apt install -y wf-recorder  once with internet"
            env["WAYLAND_DISPLAY"] = socks[0].name
            return (["wf-recorder", "-c", "libx264", "-p", "preset=ultrafast", "-p", "crf=26",
                     "-r", str(self.fps), "-f", str(out)], env, None)
        if Path("/tmp/.X11-unix/X0").exists() and shutil.which("ffmpeg"):
            env["DISPLAY"] = ":0"
            return (["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-framerate", str(self.fps), "-i", ":0",
                     "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", "-pix_fmt", "yuv420p", str(out)],
                    env, None)
        return None, None, "no desktop display found (is the Pi logged in to its desktop?)"

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        with self.lock:
            if self.running():
                return True, f"already recording to {self.path.name}"
            self.out_dir.mkdir(parents=True, exist_ok=True)
            out = self.out_dir / time.strftime("eagle_demo_%Y%m%d_%H%M%S.mp4")
            cmd, env, err = self._command(out)
            if err:
                self.error = err
                return False, err
            log = open(self.out_dir / "eagle_demo_recorder.log", "a")
            self.proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                         start_new_session=True)
            time.sleep(0.8)
            if self.proc.poll() is not None:
                self.error = f"recorder exited at once (see {log.name} in {self.out_dir})"
                self.proc = None
                return False, self.error
            self.path, self.t0, self.error = out, time.time(), None
            return True, f"recording to {out}"

    def stop(self):
        with self.lock:
            if not self.running():
                return False, "not recording"
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
            path, dur = self.path, time.time() - (self.t0 or time.time())
            self.proc = None
            size = path.stat().st_size / 1e6 if path and path.exists() else 0
            return True, f"saved {path} ({dur:.0f} s, {size:.1f} MB)"

    def status(self) -> dict:
        on = self.running()
        return {"on": on, "file": self.path.name if on and self.path else None,
                "elapsed_s": round(time.time() - self.t0) if on and self.t0 else 0, "error": self.error}


def make_handler(logs: Path, eagle_dir: Path, verdict: Verdict, rec: "ScreenRecorder"):
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
                st = state(logs, eagle_dir, verdict)
                st["screenrec"] = rec.status()
                self._send(200, json.dumps(st, default=str).encode(), "application/json")
            elif path == "/thermal.png":
                r = thermal_view(logs / "live_thermal.npy")
                self._send(200, r[0], "image/png") if r else self.send_error(404)
            elif path == "/audio.png":
                r = audio_view(logs / "live_audio.npz")
                self._send(200, r[0], "image/png") if r else self.send_error(404)
            elif path in ("/videos", "/videos/"):
                files = sorted(rec.out_dir.glob("eagle_demo_*.mp4"), key=lambda q: q.stat().st_mtime, reverse=True)
                rows = "".join(f"<li><a href='/videos/{f.name}' download>{f.name}</a> "
                               f"<span>{f.stat().st_size / 1e6:.1f} MB</span> "
                               f"<a href='/videos/{f.name}'>play</a></li>" for f in files) or "<li>No recordings yet</li>"
                page = ("<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>"
                        "<title>Recordings</title><style>body{font:17px system-ui;margin:16px;background:#0b0d10;color:#eee}"
                        "a{color:#5b9cf5}li{margin:12px 0}span{color:#999;margin:0 8px}</style>"
                        f"<h2>Recordings</h2><p><a href='/'>Back to the search view</a></p><ul>{rows}</ul>")
                self._send(200, page.encode(), "text/html; charset=utf-8")
            elif path.startswith("/videos/"):
                name = os.path.basename(path[len("/videos/"):])
                f = rec.out_dir / name
                if not (name.startswith("eagle_demo_") and name.endswith(".mp4") and f.is_file()):
                    self.send_error(404)
                    return
                size = f.stat().st_size
                start, end = 0, size - 1
                rng = self.headers.get("Range", "")
                if rng.startswith("bytes="):
                    a, _, b = rng[6:].split(",")[0].partition("-")
                    if a:
                        start, end = int(a), (int(b) if b else size - 1)
                    elif b:
                        start = max(0, size - int(b))
                    end = min(end, size - 1)
                    if start > end:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.end_headers()
                        return
                self.send_response(206 if rng else 200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                if rng:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                with open(f, "rb") as fh:
                    fh.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = fh.read(min(1 << 20, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            elif path.startswith("/cam/") and path.endswith(".jpg") and path[5:-4] in CAMERAS:
                try:
                    self._send(200, (eagle_dir / f"cam_{path[5:-4]}.jpg").read_bytes(), "image/jpeg")
                except OSError:
                    self.send_error(404)
            else:
                self.send_error(404)

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if path == "/rec/start":
                ok, msg = rec.start()
            elif path == "/rec/stop":
                ok, msg = rec.stop()
            else:
                self.send_error(404)
                return
            self._send(200, json.dumps({"ok": ok, "message": msg}).encode(), "application/json")

        def log_message(self, *a):
            pass
    return H


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--logs", default="~/field_logs")
    ap.add_argument("--eagle", default="~/eagle")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--videos", default="~/Videos", help="where screen recordings are saved")
    a = ap.parse_args(argv)
    logs, eagle_dir = Path(os.path.expanduser(a.logs)), Path(os.path.expanduser(a.eagle))
    rec = ScreenRecorder(Path(os.path.expanduser(a.videos)))
    try:
        ThreadingHTTPServer((a.host, a.port), make_handler(logs, eagle_dir, Verdict(), rec)).serve_forever()
    finally:
        rec.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
