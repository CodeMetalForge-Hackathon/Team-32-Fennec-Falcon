#!/usr/bin/env python3
"""Field control page: http://<pi>:8080 (on the THERMAL-PI hotspot: http://10.42.0.1:8080).

Everything a field team needs from a phone, with no internet: sensor status, the fused verdict and
what the Nest decrypted, a live thermal view and camera snapshots, start/stop recording, enroll or
clear the missing person's face, site setup, the clap test, and restart / reboot / shut down.

It never opens a sensor (life_node and the vision services own them). It reads what they write:
~/field_logs/live_thermal.npy, ~/eagle/cam_<name>.jpg, the run folder, nest.log. Buttons run the
scripts next to this file, which use sudo only for `systemctl start|stop|restart eagle-*`, `reboot`
and `poweroff` (/etc/sudoers.d/eagle-field from install.sh). Standard library only (numpy optional,
for the thermal picture), so it runs with the system python3. /status returns the state as JSON.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import struct
import subprocess
import threading
import time
import zlib
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

HERE = Path(__file__).resolve().parent
GB_PER_HOUR = 0.97          # measured: 256x192 thermal at 2/s + 6-channel 16 kHz audio (eagle/record.py)
CAMERAS = ("standard", "noir")
TARGET_NAME = os.environ.get("EAGLE_TARGET_NAME", "Mitch")   # shown on the page; the profile itself has no name
SITE_FIELDS = (             # (form name, label, default, unit)
    ("lat", "Latitude", "", "deg"),
    ("lon", "Longitude", "", "deg"),
    ("height_m", "Mount height above ground", "1.5", "m"),
    ("heading_deg", "Direction the module faces (true north = 0)", "0", "deg"),
    ("tilt_down_deg", "Camera tilt below horizontal", "10", "deg"),
    ("az_offset_deg", "Mic az offset (from clap test)", "0", "deg"),
)
ACTIONS = {                 # name -> (script, args, label)
    "enroll": ("enroll.sh", [], "Enroll face"),
    "clear_target": ("clear_target.sh", [], "Clear target"),
    "clap": ("clap.sh", ["20"], "Clap test"),
}


# ------------------------------------------------------------------ helpers
def tail_lines(path: Path, n: int, max_bytes: int = 256_000) -> list:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = data.splitlines()
    if size > max_bytes:
        lines = lines[1:]
    return lines[-n:]


def age_s(path: Path):
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def systemctl(*args, timeout=20) -> tuple:
    try:
        r = subprocess.run(["sudo", "-n", "/usr/bin/systemctl", *args], capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


def node_unit() -> str:
    """The detection service in use: eagle-runtime (decision gate, pi/field/use_runtime.sh) when it is
    enabled, else eagle-life. Buttons restart this one, so they never swap one for the other."""
    if Path("/etc/systemd/system/eagle-runtime.service").exists():
        try:
            if subprocess.run(["systemctl", "is-enabled", "--quiet", "eagle-runtime"], timeout=3).returncode == 0:
                return "eagle-runtime"
        except (OSError, subprocess.SubprocessError):
            pass
    return "eagle-life"


def unit_names() -> list:
    vision = ([f"eagle-vision@{c}" for c in CAMERAS] if Path("/etc/systemd/system/eagle-vision@.service").exists()
              else ["eagle-vision"])
    return ["eagle-nest", node_unit(), *vision, "eagle-dashboard", "eagle-hotspot"]


def service_states() -> dict:
    out = {}
    for s in unit_names():
        try:
            r = subprocess.run(["systemctl", "is-active", s], capture_output=True, text=True, timeout=3)
            out[s] = r.stdout.strip() or "unknown"
        except (OSError, subprocess.SubprocessError):
            out[s] = "unknown"
    return out


# ------------------------------------------------------------------ thermal PNG (numpy optional)
def _palette() -> bytes:
    """256-colour black -> purple -> red -> orange -> yellow -> white ramp, roughly 'inferno'."""
    stops = [(0, (0, 0, 4)), (60, (60, 10, 100)), (120, (180, 40, 70)), (180, (240, 110, 20)),
             (230, (250, 210, 60)), (255, (255, 255, 230))]
    out = bytearray()
    for i in range(256):
        for (a, ca), (b, cb) in zip(stops, stops[1:]):
            if a <= i <= b:
                t = (i - a) / max(b - a, 1)
                out += bytes(int(ca[k] + (cb[k] - ca[k]) * t) for k in range(3))
                break
    return bytes(out)


PALETTE = _palette()


def _png(width: int, height: int, rows: bytes) -> bytes:
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0))
            + chunk(b"PLTE", PALETTE) + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def thermal_png(path: Path, scale: int = 2):
    """-> (png bytes, min C, max C) of the latest frame, or None."""
    try:
        import numpy as np
        c = np.load(path).astype(np.float32) / 100.0
    except Exception:
        return None
    lo, hi = float(np.percentile(c, 1)), float(c.max())
    top = min(max(hi, lo + 1.0), 40.0)                     # cap just above body temperature
    idx = (np.clip((c - lo) / max(top - lo, 0.5), 0, 1) * 255).astype(np.uint8)
    if scale > 1:
        idx = idx.repeat(scale, 0).repeat(scale, 1)
    h, w = idx.shape
    rows = b"".join(b"\x00" + idx[r].tobytes() for r in range(h))
    return _png(w, h, rows), lo, hi


# ------------------------------------------------------------------ actions (one at a time)
class Runner:
    def __init__(self, logs: Path):
        self.logs, self.lock = logs, threading.Lock()
        self.name, self.proc, self.started = None, None, None

    def busy(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, name: str) -> tuple:
        with self.lock:
            if self.busy():
                return False, f"{self.name} is still running"
            script, args, _ = ACTIONS[name]
            log = open(self.logs / f"{name}.log", "w")
            self.proc = subprocess.Popen(["bash", str(HERE / script), *args], stdout=log, stderr=subprocess.STDOUT,
                                         cwd=str(HERE), start_new_session=True)
            self.name, self.started = name, time.time()
            return True, f"{ACTIONS[name][2]} started"

    def state(self) -> dict:
        if self.name is None:
            return {"name": None}
        rc = self.proc.poll() if self.proc else None
        return {"name": self.name, "label": ACTIONS[self.name][2], "running": rc is None, "exit_code": rc,
                "elapsed_s": round(time.time() - self.started, 1),
                "log": tail_lines(self.logs / f"{self.name}.log", 14)}


def set_recording(on: bool) -> tuple:
    flag = Path.home() / "eagle" / "norecord"
    flag.parent.mkdir(parents=True, exist_ok=True)
    if on:
        flag.unlink(missing_ok=True)
    else:
        flag.touch()
    rc, out = systemctl("restart", f"{node_unit()}.service")
    return rc == 0, ("Recording started (new run folder)" if on else "Recording stopped") if rc == 0 else out


# ------------------------------------------------------------------ site setup (~/eagle/site.yaml)
def site_path() -> Path:
    return Path.home() / "eagle" / "site.yaml"


def read_site() -> dict:
    try:
        d = json.loads(site_path().read_text())
        return d.get("_form", {})
    except (OSError, ValueError):
        return {}


def write_site(form: dict) -> tuple:
    vals = {}
    for key, label, default, _ in SITE_FIELDS:
        raw = (form.get(key) or [default])[0].strip()
        if raw == "":
            if key in ("lat", "lon"):
                return False, f"{label} is required (type it from your phone's maps app)"
            raw = default
        try:
            vals[key] = float(raw)
        except ValueError:
            return False, f"{label}: '{raw}' is not a number"
    if not (-90 <= vals["lat"] <= 90 and -180 <= vals["lon"] <= 180):
        return False, "latitude/longitude out of range"
    if not 0.1 <= vals["height_m"] <= 50:
        return False, "mount height should be between 0.1 and 50 m"
    mirror = bool(form.get("mirror_y"))
    mount = {"heading_deg": vals["heading_deg"], "tilt_down_deg": vals["tilt_down_deg"]}
    site = {  # JSON is valid YAML; merged over field.yaml by eagle.config `include:`
        "_form": {**{k: v for k, v in vals.items()}, "mirror_y": mirror},
        "site": {"origin": {"lat": vals["lat"], "lon": vals["lon"], "hae_m": 0.0}},
        "perch": {"enu_m": [0.0, 0.0, vals["height_m"]], "heading_deg": vals["heading_deg"],
                  "az_offset_deg": vals["az_offset_deg"]},
        "audio": {"mirror_y": mirror},
        "life": {"thermal": {"camera": {"mount": dict(mount)}}, "vision": {"camera": {"mount": dict(mount)}}},
    }
    p = site_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(site, indent=1))
    os.replace(tmp, p)
    errs = [out for u in (f"{node_unit()}.service", "eagle-nest.service") for rc, out in [systemctl("restart", u)] if rc]
    return (not errs), ("Site saved; services restarted" if not errs else "Saved, but restart failed: " + "; ".join(errs))


# ------------------------------------------------------------------ status
def gate_text(gate: dict, sends) -> str | None:
    """The decision gate's view (eagle_runtime's decision events), most probable first."""
    if not gate and not sends:
        return None
    rows = sorted(gate.values(), key=lambda d: -(d.get("p") or 0))[:4]
    out = [f"{d.get('action', ''):8s} P {d.get('p') or 0:.3f}  {d.get('subject', '')}: {d.get('reason', '')}" for d in rows]
    out += [f"SENT     {d.get('subject')} burst {d.get('burst') or '-'}, {time.time() - d['t']:.0f} s ago"
            + (" (muted)" if d.get("muted") else "") for d in sends]
    return "\n".join(out)


def status(logs: Path, runner: Runner) -> dict:
    run = logs / "latest"
    run_dir = run.resolve() if run.exists() else None
    last_list, dets = None, deque(maxlen=12)
    gate, sends = {}, deque(maxlen=3)              # eagle_runtime: latest decision per subject, sent reports
    if run_dir:
        for line in tail_lines(run_dir / "events.jsonl", 400):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("event") == "life_list":
                last_list = e
            elif e.get("event") == "detection":
                dets.append(e)
            elif e.get("event") == "decision":
                if e.get("kind") == "SEND":
                    sends.append(e)
                elif e.get("subject"):
                    gate[e["subject"]] = e
    recording = not (Path.home() / "eagle" / "norecord").exists()
    size = sum(p.stat().st_size for p in run_dir.iterdir() if p.is_file()) if run_dir else 0
    free = shutil.disk_usage(logs if logs.exists() else Path.home()).free
    wavs = sorted(run_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime) if run_dir else []
    sensors = {
        "Thermal": age_s(logs / "live_thermal.npy"),
        "Microphones": age_s(wavs[-1]) if wavs else None,
        **{f"Camera {c}": age_s(Path.home() / "eagle" / f"cam_{c}.jpg") for c in CAMERAS},
    }
    nest = [l for l in tail_lines(logs / "nest.log", 400) if "RX" in l or "COT" in l or "FIX" in l][-10:]
    return {
        "time": time.strftime("%H:%M:%S"),
        "services": service_states(),
        "sensors": {k: (None if v is None else round(v, 1)) for k, v in sensors.items()},
        "recording": {"on": recording, "dir": run_dir.name if run_dir else None, "size_gb": round(size / 1e9, 2),
                      "free_gb": round(free / 1e9, 1), "hours_left": round(max(0.0, (free / 1e9 - 2.0) / GB_PER_HOUR))},
        "target_enrolled": (Path.home() / "eagle" / "target_profile.npz").exists(),
        "life_list": last_list.get("summary") if last_list else None,
        "life_list_age_s": round(time.time() - last_list["t"]) if last_list else None,
        "gate": gate_text(gate, sends),
        "detections": [{"age_s": round(time.time() - d["t"], 1), "kind": d.get("kind"), "category": d.get("category"),
                        "source": d.get("source"), "match_level": d.get("match_level")} for d in reversed(dets)],
        "nest": nest,
        "action": runner.state(),
        "thermal_range": None,
    }


# ------------------------------------------------------------------ page
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Eagle field</title>
<style>
:root{--bg:#101214;--card:#1b1e22;--fg:#e8eaed;--mut:#9aa0a6;--ok:#4cc26a;--bad:#ef5350;--warn:#f2b33d;--acc:#5b9cf5}
*{box-sizing:border-box}body{margin:0;padding:12px 16px 40px;background:var(--bg);color:var(--fg);
font:15px/1.45 system-ui,-apple-system,sans-serif}h1{font-size:19px;margin:4px 0 10px}
h2{font-size:15px;margin:0 0 8px;color:var(--mut);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.card{background:var(--card);border-radius:12px;padding:12px;margin:0 0 12px}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.grow{flex:1}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}
.ok{background:var(--ok)}.bad{background:var(--bad)}.warn{background:var(--warn)}
button{font:inherit;border:0;border-radius:10px;padding:11px 14px;background:#2b3036;color:var(--fg);min-height:44px}
button.pri{background:var(--acc);color:#fff}button.dan{background:#5a2323}button:disabled{opacity:.45}
input{font:inherit;width:100%;padding:10px;border-radius:8px;border:1px solid #333;background:#0c0e10;color:var(--fg)}
label{display:block;font-size:13px;color:var(--mut);margin:8px 0 3px}
pre{white-space:pre-wrap;word-break:break-word;background:#0c0e10;border-radius:8px;padding:8px;margin:6px 0 0;
font:12.5px/1.4 ui-monospace,monospace;max-height:260px;overflow:auto}
img{width:100%;border-radius:8px;background:#000;display:block}.cams{display:grid;gap:8px;grid-template-columns:1fr}
@media(min-width:700px){.cams{grid-template-columns:1fr 1fr 1fr}}
.verdict{font-size:17px;font-weight:600}.mut{color:var(--mut);font-size:13px}#msg{min-height:20px;color:var(--warn)}
</style></head><body>
<h1>Eagle field <span class="mut" id="clock"></span></h1>
<div id="msg"></div>

<div class="card"><h2>Sensors</h2><div id="sensors" class="row"></div>
<div class="mut" id="services" style="margin-top:8px"></div></div>

<div class="card"><h2>Verdict</h2><div class="verdict" id="verdict">waiting...</div>
<pre id="dets"></pre><h2 style="margin-top:10px">Rescuer messages (decrypted at the Nest)</h2><pre id="nest"></pre></div>

<div class="card"><h2>Live view</h2><div class="cams">
<div><img id="thermal" alt="thermal"><div class="mut" id="trange">thermal</div></div>
<div><img id="cam_standard" alt="standard camera"><div class="mut">standard camera</div></div>
<div><img id="cam_noir" alt="NoIR wide camera"><div class="mut">NoIR wide camera</div></div></div></div>

<div class="card"><h2>Recording</h2><div id="rec" class="mut"></div>
<div class="row" style="margin-top:8px"><button class="pri" onclick="post('/record/start')">Start recording</button>
<button onclick="post('/record/stop')">Stop recording</button></div></div>

<div class="card"><h2>Missing person: {TARGET}</h2><div id="target" class="mut"></div>
<div class="row" style="margin-top:8px"><button class="pri act" onclick="act('enroll','Stop the cameras and scan {TARGET} now? {TARGET} faces the standard camera and turn your head slowly for about 10 s.')">Enroll face</button>
<button class="act" onclick="act('clear_target','Remove the enrolled face?')">Clear target</button></div></div>

<div class="card"><h2>Site setup</h2><form id="site" onsubmit="saveSite(event)">{site_fields}
<label><input type="checkbox" name="mirror_y" style="width:auto" {mirror}> Mic left/right swapped (from clap test)</label>
<div class="row" style="margin-top:10px"><button class="pri" type="submit">Save site</button>
<button type="button" class="act" onclick="act('clap','Run a 20 s clap test? Detection pauses. Clap about 5 m away, first ON the 0 deg mark.')">Clap test</button></div>
</form><div class="mut" style="margin-top:6px">Lat/lon: copy from your phone's maps app (browsers block location on local pages).</div></div>

<div class="card"><h2>Task</h2><div id="act" class="mut">none</div><pre id="actlog" style="display:none"></pre></div>

<div class="card"><h2>Power</h2><div class="row">
<button onclick="post('/power/restart','Restart all eagle services?')">Restart services</button>
<button onclick="post('/power/reboot','Reboot the Pi?')">Reboot</button>
<button class="dan" onclick="post('/power/poweroff','Shut down the Pi? Wait for the green light to stop before unplugging.')">Shut down</button></div>
<div class="mut" style="margin-top:6px">The Pi's power button also shuts down cleanly (one short press).</div></div>

<script>
const $=id=>document.getElementById(id);
function msg(t){$('msg').textContent=t||'';if(t)setTimeout(()=>{if($('msg').textContent===t)$('msg').textContent=''},8000)}
async function post(url,confirmText,body){
  if(confirmText&&!confirm(confirmText))return;
  try{const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body||''});
      const j=await r.json();msg(j.message);}catch(e){msg('No answer (normal during reboot/shutdown)');}
  setTimeout(poll,500);}
function act(name,c){post('/action/'+name,c);}
function saveSite(e){e.preventDefault();post('/site','Save site setup and restart detection?',new URLSearchParams(new FormData($('site'))).toString());}
function sensor(name,age){const s=age==null?'bad':age<5?'ok':age<90?'warn':'bad';
  const t=age==null?'no data':age<5?'live':Math.round(age)+' s old';
  return `<span><span class="dot ${s}"></span>${name} <span class="mut">${t}</span></span>`;}
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
async function poll(){
  let s;try{s=await (await fetch('/status')).json();}catch(e){$('clock').textContent='(no connection)';return;}
  $('clock').textContent=s.time;
  $('sensors').innerHTML=Object.entries(s.sensors).map(([k,v])=>sensor(k,v)).join(' &nbsp; ');
  $('services').innerHTML=Object.entries(s.services).map(([k,v])=>`<span class="dot ${v==='active'?'ok':'bad'}"></span>${esc(k)}`).join(' &nbsp; ');
  $('verdict').textContent=(s.gate?'Decision gate:\n'+s.gate+'\n\n':'')+(s.life_list?s.life_list:'No people tracked yet');
  $('dets').textContent=s.detections.map(d=>`${d.age_s.toFixed(0).padStart(4)} s  ${d.kind||''} ${d.category||''}${d.match_level?'  MATCH '+d.match_level:''}  ${d.source||''}`).join('\\n')||'no detections yet';
  $('nest').textContent=s.nest.join('\\n')||'nothing received yet';
  const r=s.recording;$('rec').innerHTML=(r.on?'<span class="dot ok"></span>Recording ':'<span class="dot bad"></span>Not recording ')+
    `${r.dir?'run '+esc(r.dir)+', ':''}${r.size_gb} GB this run, ${r.free_gb} GB free, about ${r.hours_left} h left`;
  $('target').innerHTML=s.target_enrolled?'<span class="dot ok"></span>{TARGET} enrolled: the cameras are matching against his face':'<span class="dot warn"></span>{TARGET} not enrolled yet: people are detected but not identified';
  const a=s.action;document.querySelectorAll('.act').forEach(b=>b.disabled=!!(a.name&&a.running));
  if(a.name){$('act').textContent=`${a.label}: ${a.running?'running '+a.elapsed_s+' s':(a.exit_code===0?'finished OK':'failed (exit '+a.exit_code+')')}`;
    $('actlog').style.display='block';$('actlog').textContent=a.log.join('\\n');}
  const t=Date.now();$('thermal').src='/thermal.png?'+t;['standard','noir'].forEach(c=>$('cam_'+c).src='/cam/'+c+'.jpg?'+t);
}
$('thermal').onload=async()=>{try{const h=await fetch('/thermal.json');const j=await h.json();
  if(j.min!=null)$('trange').textContent=`thermal ${j.min.toFixed(1)} to ${j.max.toFixed(1)} C`;}catch(e){}};
poll();setInterval(poll,2500);
</script></body></html>"""


def render_page() -> str:
    site = read_site()
    fields = "".join(
        f"<label>{html.escape(label)} ({unit})</label><input name='{key}' inputmode='decimal' "
        f"value='{html.escape(str(site.get(key, default)))}'>" for key, label, default, unit in SITE_FIELDS)
    return PAGE.replace("{TARGET}", html.escape(TARGET_NAME)).replace("{site_fields}", fields).replace("{mirror}", "checked" if site.get("mirror_y") else "")


def make_handler(logs: Path, runner: Runner):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, default=str).encode(), "application/json")

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._send(200, render_page().encode(), "text/html; charset=utf-8")
            elif path == "/status":
                self._json(status(logs, runner))
            elif path == "/thermal.png":
                r = thermal_png(logs / "live_thermal.npy")
                self._send(200, r[0], "image/png") if r else self.send_error(404)
            elif path == "/thermal.json":
                r = thermal_png(logs / "live_thermal.npy", scale=1)
                self._json({"min": r[1], "max": r[2]} if r else {"min": None, "max": None})
            elif path.startswith("/cam/") and path.endswith(".jpg") and path[5:-4] in CAMERAS:
                try:
                    self._send(200, (Path.home() / "eagle" / f"cam_{path[5:-4]}.jpg").read_bytes(), "image/jpeg")
                except OSError:
                    self.send_error(404)
            else:
                self.send_error(404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            form = parse_qs(self.rfile.read(n).decode("utf-8", "replace")) if n else {}
            path = self.path.split("?", 1)[0]
            if path.startswith("/action/") and path[8:] in ACTIONS:
                ok, m = runner.start(path[8:])
            elif path in ("/record/start", "/record/stop"):
                ok, m = set_recording(path.endswith("start"))
            elif path == "/site":
                ok, m = write_site(form)
            elif path == "/power/restart":
                if runner.busy():
                    ok, m = False, "wait for the running task to finish"
                else:
                    units = [u + ".service" for u in unit_names() if u not in ("eagle-dashboard", "eagle-hotspot")]
                    rc, out = systemctl("restart", *units, timeout=60)
                    ok, m = rc == 0, "Services restarted" if rc == 0 else out
            elif path in ("/power/reboot", "/power/poweroff"):
                verb = path.rsplit("/", 1)[1]
                threading.Timer(1.0, systemctl, args=(verb,)).start()
                ok, m = True, "Rebooting..." if verb == "reboot" else "Shutting down. Unplug when the green light stops."
            else:
                self._json({"ok": False, "message": "unknown action"}, 404)
                return
            self._json({"ok": ok, "message": m})

        def log_message(self, *a):
            pass
    return H


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--logs", default="~/field_logs")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    a = ap.parse_args(argv)
    logs = Path(os.path.expanduser(a.logs))
    logs.mkdir(parents=True, exist_ok=True)
    ThreadingHTTPServer((a.host, a.port), make_handler(logs, Runner(logs))).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
