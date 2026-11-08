"""Field recording for life_node ``--record``: everything needed to replay or re-score a run later.

One directory per run, ``<root>/<YYYYmmdd-HHMMSS>/`` (``-2``, ``-3``... if it already exists, e.g. a
Pi that booted without a valid clock), and ``<root>/latest`` pointing at it:

* ``thermal_NNNN.npz``  temperature frames at up to ``thermal_hz`` (default 2/s), chunked every
  ``chunk_s`` seconds: ``t`` (unix s, float64), ``centi_c`` (int16, deg C x 100, exact to 0.01 C).
* ``audio_NNNN.wav``    every device channel of the mic array (raw, not just the DOA channels),
  16-bit PCM, a new file every ``chunk_s`` seconds so a power cut loses at most one chunk.
  ``audio_NNNN.json``  holds the unix time of its first sample.
* ``events.jsonl``      one JSON object per line: run start (config), module status, every detection,
  identity match, life list report and transmitted frame, keyed by ``event`` (run_start, detection,
  life_list, tx_life, audio_gap, record_stopped, record_closed). Flushed per line, fsynced every few seconds.

Recording stops (logged, not fatal) when free disk space drops below ``min_free_gb``.
"""
from __future__ import annotations

import dataclasses
import json
import os
import shutil
import time
import wave
from pathlib import Path

import numpy as np


def _jsonable(x):
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, (np.floating, np.integer, np.bool_)):
        return x.item()
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return {k: _jsonable(v) for k, v in dataclasses.asdict(x).items()}
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, Path):
        return str(x)
    return x


def make_run_dir(root, now=None) -> Path:
    root = Path(os.path.expanduser(str(root)))
    root.mkdir(parents=True, exist_ok=True)
    base = time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() if now is None else now))
    d, k = root / base, 1
    while d.exists():
        k += 1
        d = root / f"{base}-{k}"
    d.mkdir()
    latest = root / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(d.name)
    except OSError:
        pass
    return d


class Recorder:
    def __init__(self, root, *, thermal_hz: float = 2.0, chunk_s: float = 60.0, min_free_gb: float = 2.0,
                 fsync_s: float = 5.0, log=print, clock=time.time):
        self.dir = make_run_dir(root, clock())
        self.thermal_dt = 1.0 / float(thermal_hz) if thermal_hz else None
        self.chunk_s, self.min_free = float(chunk_s), float(min_free_gb) * 1e9
        self.fsync_s, self.log, self.clock = float(fsync_s), log, clock
        self.active = True
        self.events = open(self.dir / "events.jsonl", "a", buffering=1, encoding="utf-8")
        self._last_fsync = clock()
        self._th_t, self._th_f, self._th_n, self._th_start, self._th_last = [], [], 0, None, -1e9
        self._wav, self._wav_n, self._wav_start = None, 0, None
        self._audio_total = None                 # LiveCapture.total already written
        self.log(f"RECORD  {self.dir}")

    # ---- disk guard ---------------------------------------------------------------------------
    def free_bytes(self) -> int:
        return shutil.disk_usage(self.dir).free

    def _check_space(self) -> bool:
        if self.active and self.free_bytes() < self.min_free:
            self.event("record_stopped", reason=f"free disk below {self.min_free / 1e9:.1f} GB")
            self.log(f"RECORD  stopped: free disk below {self.min_free / 1e9:.1f} GB")
            self._flush_thermal()
            self._close_wav()
            self.active = False
        return self.active

    # ---- events -------------------------------------------------------------------------------
    def event(self, _event: str, **fields) -> None:
        """One line in events.jsonl: {"t": unix s, "event": _event, ...fields}."""
        if self.events.closed:
            return
        rec = {"t": self.clock(), "event": _event, **_jsonable(fields)}
        self.events.write(json.dumps(rec, separators=(",", ":")) + "\n")
        now = self.clock()
        if now - self._last_fsync >= self.fsync_s:
            self._last_fsync = now
            try:
                os.fsync(self.events.fileno())
            except OSError:
                pass

    def detections(self, dets) -> None:
        for d in dets:
            self.event("detection", **_jsonable(d))

    # ---- thermal ------------------------------------------------------------------------------
    def thermal_due(self, now: float) -> bool:
        return self.active and self.thermal_dt is not None and now - self._th_last >= self.thermal_dt - 1e-3

    def thermal(self, t: float, frame) -> None:
        if not self.thermal_due(t):
            return
        self._th_last = t
        if self._th_start is None:
            self._th_start = t
        self._th_t.append(float(t))
        self._th_f.append(np.clip(np.round(np.asarray(frame, float) * 100.0), -32768, 32767).astype(np.int16))
        self._write_live(self._th_f[-1])
        if t - self._th_start >= self.chunk_s:
            self._flush_thermal()

    def _write_live(self, centi_c) -> None:
        """Latest thermal frame (centi-deg C, int16) at <root>/live_thermal.npy for the field page.
        Written to a temp file then renamed, so a reader never sees half a frame."""
        try:
            tmp = self.dir.parent / "live_thermal.tmp.npy"
            np.save(tmp, centi_c)
            os.replace(tmp, self.dir.parent / "live_thermal.npy")
        except OSError:
            pass

    def _flush_thermal(self) -> None:
        if not self._th_t:
            return
        if self._check_space_quiet():
            p = self.dir / f"thermal_{self._th_n:04d}.npz"
            np.savez_compressed(p, t=np.array(self._th_t), centi_c=np.stack(self._th_f))
            self._th_n += 1
        self._th_t, self._th_f, self._th_start = [], [], None

    def _check_space_quiet(self) -> bool:
        return self.free_bytes() >= self.min_free

    # ---- audio --------------------------------------------------------------------------------
    def audio(self, cap) -> None:
        """Append the samples ``cap`` (eagle.audio_io.LiveCapture) captured since the last call."""
        if not self.active:
            return
        total, block = cap.since(self._audio_total)
        if self._audio_total is not None and total - len(block) > self._audio_total:
            lost = total - len(block) - self._audio_total
            self.event("audio_gap", samples=int(lost), seconds=lost / cap.fs)
        self._audio_total = total
        if not len(block):
            return
        now = self.clock()
        if self._wav is None or now - self._wav_start >= self.chunk_s:
            self._close_wav()
            if not self._check_space():
                return
            p = self.dir / f"audio_{self._wav_n:04d}.wav"
            self._wav = wave.open(str(p), "wb")
            self._wav.setnchannels(block.shape[1])
            self._wav.setsampwidth(2)
            self._wav.setframerate(int(cap.fs))
            t0 = now - len(block) / cap.fs
            (self.dir / f"audio_{self._wav_n:04d}.json").write_text(
                json.dumps({"t_first_sample": t0, "fs": int(cap.fs), "channels": int(block.shape[1])}))
            self._wav_n += 1
            self._wav_start = now
        pcm = np.clip(np.round(np.asarray(block, np.float32) * 32767.0), -32768, 32767).astype("<i2")
        self._wav.writeframes(pcm.tobytes())

    def _close_wav(self) -> None:
        if self._wav is not None:
            try:
                self._wav.close()
            finally:
                self._wav = None

    # ---- lifecycle ----------------------------------------------------------------------------
    def tick(self) -> None:
        """Once per life_node step: disk check."""
        self._check_space()

    def close(self) -> None:
        self._flush_thermal()
        self._close_wav()
        if not self.events.closed:
            self.event("record_closed")
            self.events.close()


def size_per_hour(run_dir) -> dict:
    """Bytes per hour of each stream in a finished run (from its files and timestamps)."""
    d = Path(run_dir)
    out = {}
    th = sorted(d.glob("thermal_*.npz"))
    if th:
        ts = np.concatenate([np.load(p)["t"] for p in th])
        span = max(ts[-1] - ts[0], 1e-9) * len(ts) / max(len(ts) - 1, 1)
        out["thermal"] = sum(p.stat().st_size for p in th) / span * 3600
    wavs = sorted(d.glob("audio_*.wav"))
    if wavs:
        secs = 0.0
        for p in wavs:
            with wave.open(str(p)) as w:
                secs += w.getnframes() / w.getframerate()
        out["audio"] = sum(p.stat().st_size for p in wavs) / max(secs, 1e-9) * 3600
    ev = d / "events.jsonl"
    if ev.is_file():
        lines = ev.read_text().splitlines()
        if len(lines) > 1:
            t0, t1 = json.loads(lines[0])["t"], json.loads(lines[-1])["t"]
            out["events"] = ev.stat().st_size / max(t1 - t0, 1e-9) * 3600
    return out
