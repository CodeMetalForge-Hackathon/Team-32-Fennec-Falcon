#!/usr/bin/env python3
"""Read-only field status page: http://<pi>:8080 (on the THERMAL-PI hotspot: http://10.42.0.1:8080).

Opens no sensors (life_node and hailo_vision own them); it only reads ~/field_logs. Standard library
only, so it runs with the system python3. Shows: which eagle services are running, the current
recording (size, free disk, hours left), the latest life list, recent detections and what the Nest
received. /status returns the same as JSON.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import subprocess
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SERVICES = ("eagle-nest", "eagle-life", "eagle-vision", "eagle-dashboard")
GB_PER_HOUR = 0.97          # measured: 256x192 thermal at 2/s + 6-channel 16 kHz audio (eagle/record.py)


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
        lines = lines[1:]                       # first line is probably cut
    return lines[-n:]


def service_states() -> dict:
    out = {}
    for s in SERVICES:
        try:
            r = subprocess.run(["systemctl", "is-active", s], capture_output=True, text=True, timeout=3)
            out[s] = r.stdout.strip() or "unknown"
        except (OSError, subprocess.SubprocessError):
            out[s] = "unknown"
    return out


def status(logs: Path) -> dict:
    run = logs / "latest"
    run_dir = run.resolve() if run.exists() else None
    events, last_list, dets = [], None, deque(maxlen=15)
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
            elif e.get("event") in ("run_start", "record_stopped", "required_missing", "required_failed", "audio_gap"):
                events.append(e)
    size = sum(p.stat().st_size for p in run_dir.iterdir() if p.is_file()) if run_dir else 0
    free = shutil.disk_usage(logs if logs.exists() else Path.home()).free
    nest = [l for l in tail_lines(logs / "nest.log", 300) if "RX" in l or "COT" in l][-12:]
    return {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "services": service_states(),
        "recording": {"dir": str(run_dir) if run_dir else None, "size_gb": size / 1e9, "free_gb": free / 1e9,
                      "hours_left": max(0.0, (free / 1e9 - 2.0) / GB_PER_HOUR)},
        "life_list": last_list.get("summary") if last_list else None,
        "life_list_age_s": time.time() - last_list["t"] if last_list else None,
        "detections": [{"age_s": round(time.time() - d["t"], 1), "kind": d.get("kind"), "category": d.get("category"),
                        "source": d.get("source"), "xy": d.get("xy"), "match_level": d.get("match_level")}
                       for d in reversed(dets)],
        "events": events[-5:],
        "nest": nest,
        "vision_log": tail_lines(logs / "vision.log", 3),
    }


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="3"><title>Eagle field</title>
<style>body{{font:14px/1.4 system-ui,sans-serif;margin:16px;background:#111;color:#eee}}h1{{font-size:18px}}
h2{{font-size:15px;margin:18px 0 6px}}pre{{white-space:pre-wrap;background:#1c1c1c;padding:8px;border-radius:6px;overflow-x:auto}}
.ok{{color:#5d5}}.bad{{color:#f66}}table{{border-collapse:collapse}}td{{padding:2px 10px 2px 0}}</style></head><body>
<h1>Eagle field status <small>{time}</small></h1>
<h2>Services</h2><table>{services}</table>
<h2>Recording</h2><pre>{recording}</pre>
<h2>Life list {list_age}</h2><pre>{life_list}</pre>
<h2>Recent detections</h2><pre>{detections}</pre>
<h2>Nest received</h2><pre>{nest}</pre>
<h2>Vision</h2><pre>{vision}</pre>
</body></html>"""


def render(s: dict) -> str:
    e = html.escape
    svc = "".join(f"<tr><td>{e(k)}</td><td class='{'ok' if v == 'active' else 'bad'}'>{e(v)}</td></tr>"
                  for k, v in s["services"].items())
    r = s["recording"]
    rec = (f"{r['dir'] or 'not recording'}\n{r['size_gb']:.2f} GB this run, {r['free_gb']:.1f} GB free, "
           f"about {r['hours_left']:.0f} h of recording left")
    dets = "\n".join(f"{d['age_s']:6.1f} s ago  {d['kind'] or '':7s} {d['category'] or '':9s} "
                     f"{('E %.1f N %.1f' % tuple(d['xy'][:2])) if d['xy'] else '':16s} "
                     f"{('match ' + str(d['match_level'])) if d['match_level'] else ''} {d['source'] or ''}"
                     for d in s["detections"]) or "none yet"
    age = f"({s['life_list_age_s']:.0f} s ago)" if s["life_list_age_s"] is not None else ""
    return PAGE.format(time=e(s["time"]), services=svc, recording=e(rec), list_age=e(age),
                       life_list=e(s["life_list"] or "no report yet"), detections=e(dets),
                       nest=e("\n".join(s["nest"]) or "nothing yet"), vision=e("\n".join(s["vision_log"]) or "-"))


def make_handler(logs: Path):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            s = status(logs)
            if self.path.startswith("/status"):
                body, ctype = json.dumps(s, default=str).encode(), "application/json"
            elif self.path in ("/", "/index.html"):
                body, ctype = render(s).encode(), "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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
    ThreadingHTTPServer((a.host, a.port), make_handler(logs)).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
