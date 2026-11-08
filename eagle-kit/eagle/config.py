"""Configuration: YAML loading (user file over the shipped example) and array presets."""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

KIT_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_CONFIG = KIT_ROOT / "config" / "example.yaml"


def _read_yaml(path) -> dict:
    import yaml
    with open(os.path.expanduser(str(path)), "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(path=None) -> dict:
    """The shipped example.yaml provides every default; ``path`` overrides any subset.

    ``path`` may list ``include: [file, ...]``: each file that exists is merged over it in order
    (missing files are skipped). The field page writes the site setup to such a file
    (``~/eagle/site.yaml``) so it never has to rewrite the hand-edited config."""
    cfg = _read_yaml(EXAMPLE_CONFIG)
    if path and Path(os.path.expanduser(str(path))).resolve() != EXAMPLE_CONFIG.resolve():
        user = _read_yaml(path)
        includes = user.pop("include", None) or []
        cfg = deep_merge(cfg, user)
        for inc in ([includes] if isinstance(includes, str) else includes):
            p = Path(os.path.expanduser(str(inc)))
            if p.is_file():
                cfg = deep_merge(cfg, _read_yaml(p))
    return cfg


@dataclass
class ArrayGeometry:
    name: str
    positions: np.ndarray            # (M, 3) metres, body frame
    channels: list                   # device input channel of each mic (0-based)
    device_channels: int             # channels to open on the audio device
    fs: int
    verified: object = False
    description: str = ""
    notes: str = ""

    @property
    def n_mics(self) -> int:
        return int(self.positions.shape[0])

    def select(self, block: np.ndarray) -> np.ndarray:
        """(n_samples, device_channels) audio block -> (n_mics, n_samples) in preset order."""
        return np.ascontiguousarray(np.asarray(block)[:, self.channels].T, dtype=float)


def array_geometry(cfg: dict, name: str | None = None, fs: int | None = None,
                   mirror_y: bool | None = None) -> ArrayGeometry:
    audio = cfg.get("audio", {})
    name = name or audio.get("preset")
    arrays = cfg.get("arrays", {})
    if name not in arrays:
        raise KeyError(f"unknown array preset {name!r}; choose from {sorted(arrays)}")
    p = arrays[name]
    pos = np.asarray(p["positions_m"], dtype=float)
    chans = list(p["channels"])
    if pos.shape != (len(chans), 3):
        raise ValueError(f"preset {name}: positions_m must be {len(chans)} x 3")
    if (audio.get("mirror_y") if mirror_y is None else mirror_y):
        pos = pos * np.array([1.0, -1.0, 1.0])
    return ArrayGeometry(name, pos, chans, int(p.get("device_channels", max(chans) + 1)),
                         int(fs or audio.get("fs") or p.get("fs", 16000)), p.get("verified", False),
                         str(p.get("description", "")), str(p.get("notes", "")))


def load_keys(cfg: dict) -> dict:
    """Hex keys -> bytes (None where absent). From ``keys_file`` (a YAML file written by apps/keygen.py,
    kept outside the repo) when the config sets one, else from cfg['keys']. A ``keys_file`` that is set
    but missing is an error: falling back to the shipped test keys would be silent and insecure."""
    keys = cfg.get("keys") or {}
    if cfg.get("keys_file"):
        p = Path(os.path.expanduser(str(cfg["keys_file"])))
        if not p.is_file():
            raise FileNotFoundError(f"keys_file {p} not found: run apps/keygen.py --out-dir {p.parent}")
        keys = _read_yaml(p).get("keys") or {}
    out = {}
    for k in ("tag_hmac", "fix_aes", "covert"):
        v = keys.get(k)
        out[k] = bytes.fromhex(v) if v else None
        if out[k] is not None and len(out[k]) != 32:
            raise ValueError(f"keys.{k} must be 64 hex characters")
    return out
