"""Audio I/O: multichannel WAV read/write (stdlib + numpy) and live capture (sounddevice)."""
from __future__ import annotations

import struct
import threading
import time

import numpy as np


def read_wav(path) -> tuple:
    """-> (fs, data) with data shaped (channels, samples), float64 in [-1, 1].
    Supports PCM 16/24/32-bit, IEEE float 32/64 and WAVE_FORMAT_EXTENSIBLE."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(raw):
        cid, size = raw[pos:pos + 4], struct.unpack("<I", raw[pos + 4:pos + 8])[0]
        body = raw[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            tag, ch, fs, _, _, bits = struct.unpack("<HHIIHH", body[:16])
            if tag == 0xFFFE and len(body) >= 26:
                tag = struct.unpack("<H", body[24:26])[0]
            fmt = (tag, ch, fs, bits)
        elif cid == b"data":
            data = body
        pos += 8 + size + (size & 1)
    if fmt is None or data is None:
        raise ValueError("WAV without fmt/data chunk")
    tag, ch, fs, bits = fmt
    if tag == 3:
        x = np.frombuffer(data, dtype="<f4" if bits == 32 else "<f8").astype(float)
    elif tag == 1 and bits == 16:
        x = np.frombuffer(data, dtype="<i2") / 32768.0
    elif tag == 1 and bits == 32:
        x = np.frombuffer(data, dtype="<i4") / 2147483648.0
    elif tag == 1 and bits == 24:
        b = np.frombuffer(data[: len(data) // 3 * 3], dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = np.where(v >= 1 << 23, v - (1 << 24), v) / 8388608.0
    else:
        raise ValueError(f"unsupported WAV format tag={tag} bits={bits}")
    n = x.size // ch
    return int(fs), x[: n * ch].reshape(n, ch).T.copy()


def write_wav(path, fs: int, data, *, float32: bool = False) -> None:
    """Write (channels, samples) audio. Default 16-bit PCM (clipped); float32 if asked."""
    x = np.atleast_2d(np.asarray(data, dtype=float))
    ch, n = x.shape
    if float32:
        payload, tag, bits = x.T.astype("<f4").tobytes(), 3, 32
    else:
        payload, tag, bits = (np.clip(x.T, -1.0, 32767 / 32768) * 32768).astype("<i2").tobytes(), 1, 16
    hdr = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(payload), b"WAVE", b"fmt ", 16, tag, ch, int(fs),
                      int(fs) * ch * bits // 8, ch * bits // 8, bits, b"data", len(payload))
    with open(path, "wb") as f:
        f.write(hdr + payload)


def list_devices() -> str:
    import sounddevice as sd  # needs PortAudio (apt install libportaudio2)
    return str(sd.query_devices())


def find_device(name_or_index):
    """sounddevice index from an int or a case-insensitive name substring (input devices)."""
    if name_or_index is None or isinstance(name_or_index, int):
        return name_or_index
    if str(name_or_index).isdigit():
        return int(name_or_index)
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and str(name_or_index).lower() in d["name"].lower():
            return i
    raise ValueError(f"no input device matching {name_or_index!r}; try --list-devices")


class LiveCapture:
    """Ring buffer fed by a sounddevice InputStream (all device channels, float32)."""

    def __init__(self, device, fs: int, channels: int, seconds: float = 10.0, blocksize: int = 0):
        import sounddevice as sd
        self.fs, self.channels = int(fs), int(channels)
        self.buf = np.zeros((int(seconds * fs), channels), dtype=np.float32)
        self.total = 0                       # samples captured since start
        self.lock = threading.Lock()
        self.overflows = 0
        self.stream = sd.InputStream(device=find_device(device), samplerate=fs, channels=channels,
                                     dtype="float32", blocksize=blocksize, callback=self._cb)

    def _cb(self, indata, frames, time_info, status):  # pragma: no cover - audio thread
        if status:
            self.overflows += 1
        with self.lock:
            n = len(indata)
            i = self.total % len(self.buf)
            first = min(n, len(self.buf) - i)
            self.buf[i:i + first] = indata[:first]
            self.buf[: n - first] = indata[first:]
            self.total += n

    def start(self):
        self.stream.start()
        return self

    def stop(self):
        self.stream.stop()
        self.stream.close()

    def window(self, seconds: float) -> tuple:
        """-> (abs_start_sample, block (n, device_channels)) for the most recent ``seconds``."""
        with self.lock:
            n = min(int(seconds * self.fs), self.total, len(self.buf))
            end = self.total % len(self.buf)
            idx = (np.arange(end - n, end)) % len(self.buf)
            return self.total - n, self.buf[idx].copy()

    def record(self, seconds: float) -> np.ndarray:
        start = self.total
        while self.total - start < seconds * self.fs:
            time.sleep(0.05)
        _, blk = self.window(seconds)
        return blk
