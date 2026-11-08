#!/usr/bin/env python3
"""Download every model weight ONCE, while internet is available, and verify it. Nothing downloads at runtime.

    python apps/fetch_models.py --arch hailo8l               # AI HAT+ 13 TOPS (Hailo-8L)
    python apps/fetch_models.py --arch hailo8                # AI HAT+ 26 TOPS (Hailo-8)
    python apps/fetch_models.py --arch auto --export-osnet   # detect the Hailo, also build the OSNet ONNX
    python apps/fetch_models.py --verify                     # offline: check every file against models.lock.json

Files land in ``--models-dir`` (default ``identity.models_dir`` = ~/eagle-models), outside the repo
(several are over 50 MB). Every download is checked before it is kept:

* ``sha256`` pinned in MANIFEST below where we computed it from the upstream file, and/or
* ``etag``: the S3 ETag the publisher (Hailo) serves - MD5 of the file, or for multipart uploads
  MD5 of the part MD5s + "-<parts>" (verified locally by trying the part sizes that give that count),
* ``size`` always.
After a verified download (or the OSNet export) its SHA-256 is written to ``models.lock.json`` in
the models dir; ``--verify`` re-checks everything against it without touching the network. Copy
the whole models dir to other Pis rather than downloading again.

OSNet: torchreid publishes PyTorch weights only. ``--export-osnet`` clones torchreid (pinned
commit) and runs its own ``tools/export.py`` to produce ``osnet/osnet_x1_0_msmt17.onnx``. That needs
``pip install torch torchvision onnx pandas`` on the machine doing it (a laptop is fine; copy the
.onnx over). Licences: eagle/data/NOTICE.md (InsightFace weights: non-commercial research only).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

MIB = 1024 * 1024
MZ = "https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/v2.19.0"   # hailo-apps' model_zoo_versions
S3 = "https://hailo-csdata.s3.eu-west-2.amazonaws.com/resources"                     # hailo-apps' S3_RESOURCES_BASE_URL
TORCHREID_REPO = "https://github.com/KaiyangZhou/deep-person-reid.git"
TORCHREID_COMMIT = "f8cd150fdf77e8d9e1ed143b7f308c2c609ded50"
USER_AGENT = "eagle-fetch-models/1"


@dataclass(frozen=True)
class Entry:
    name: str
    group: str                    # face | osnet | clip | detection
    url: str
    dest: str                     # relative to the models dir
    size: int
    sha256: str | None = None
    etag: str | None = None
    arch: str | None = None       # hailo8 | hailo8l (HEFs are compiled per architecture)
    extract: str | None = None    # zip: extract into this dir (relative to the models dir)
    license: str = ""             # key in eagle/data/NOTICE.md


MANIFEST = [
    Entry("buffalo_l", "face", "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
          "insightface/buffalo_l.zip", 288621354,
          sha256="80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f",
          extract="insightface/models/buffalo_l", license="insightface"),
    Entry("osnet_x1_0_msmt17", "osnet",
          "https://drive.usercontent.google.com/download?id=112EMUfBPYeYg70w-syK6V6Mx8-Qb9Q1M&export=download&confirm=t",
          "osnet/osnet_x1_0_msmt17.pt", 10994685,
          sha256="b7d73dc67c016fd044e4027ff856019496392a7aca8fa0ed56d862a1632c1cf2", license="osnet"),
    Entry("clip_tokenizer", "clip", f"{S3}/configs/clip_tokenizer.json", "clip/clip_tokenizer.json", 3642073,
          sha256="6d9109cc838977f3ca94a379eec36aecc7c807e1785cd729660ca2fc0171fb35",
          etag="0ac11a8753bc771a78b6f6a65ca8f22a", license="clip"),
    Entry("clip_text_projection", "clip", f"{S3}/npy/text_projection.npy", "clip/text_projection.npy", 1048704,
          sha256="c17206334739eff3138ddeee884b0478fd70b7f744a0f9395d3d5bdb140400d9",
          etag="255fc9f07752162e9681494527818c71", license="clip"),
    Entry("clip_token_embedding_lut", "clip", f"{S3}/npy/token_embedding_lut.npy", "clip/token_embedding_lut.npy",
          101187712, etag="7aa4f6f2194ab57e503e400c5b0d775a-6", license="clip"),
    Entry("clip_image_hailo8", "clip", f"{MZ}/hailo8/clip_vit_b_32_image_encoder.hef",
          "clip/hailo8/clip_vit_b_32_image_encoder.hef", 96900770, etag="9214a5606c1f158378959b776cd69af5-6",
          arch="hailo8", license="clip"),
    Entry("clip_text_hailo8", "clip", f"{MZ}/hailo8/clip_vit_b_32_text_encoder.hef",
          "clip/hailo8/clip_vit_b_32_text_encoder.hef", 93856389, etag="8eb2f56f41ba2f4ac6c6f419f3b8e4b8-6",
          arch="hailo8", license="clip"),
    Entry("clip_image_hailo8l", "clip", f"{MZ}/hailo8l/clip_vit_b_32_image_encoder.hef",
          "clip/hailo8l/clip_vit_b_32_image_encoder.hef", 191502011, etag="aca98707021c2e61f1c8340f188c6995-12",
          arch="hailo8l", license="clip"),
    Entry("clip_text_hailo8l", "clip", f"{MZ}/hailo8l/clip_vit_b_32_text_encoder.hef",
          "clip/hailo8l/clip_vit_b_32_text_encoder.hef", 167524982, etag="fa849c696c096328de4dcba2ef90a92a-10",
          arch="hailo8l", license="clip"),
    Entry("detection_hailo8", "detection", f"{MZ}/hailo8/yolov5m_wo_spp.hef", "hailo/hailo8/yolov5m_wo_spp.hef",
          18024868, etag="cf013b86c4216070a2dfdab3a6750f47-2", arch="hailo8", license="yolov5"),
    Entry("detection_hailo8l", "detection", f"{MZ}/hailo8l/yolov5m_wo_spp.hef", "hailo/hailo8l/yolov5m_wo_spp.hef",
          37764611, etag="2a38e748f1075b967a4c333d44dcec89-3", arch="hailo8l", license="yolov5"),
]
OSNET_ONNX = "osnet/osnet_x1_0_msmt17.onnx"
LOCK = "models.lock.json"


# ---- checksums ----------------------------------------------------------------------------------
def sha256_file(path, chunk: int = MIB) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


def s3_etag(path, part_size: int) -> str:
    """S3 ETag of ``path`` uploaded in ``part_size`` parts (plain MD5 when it fits one part)."""
    md5s = []
    with open(path, "rb") as f:
        while b := f.read(part_size):
            md5s.append(hashlib.md5(b).digest())
    if len(md5s) <= 1:
        return (md5s[0] if md5s else hashlib.md5(b"").digest()).hex()
    return f"{hashlib.md5(b''.join(md5s)).hexdigest()}-{len(md5s)}"


def etag_matches(path, etag: str) -> bool:
    etag = etag.strip('"')
    size = os.path.getsize(path)
    if "-" not in etag:
        h = hashlib.md5()
        with open(path, "rb") as f:
            while b := f.read(MIB):
                h.update(b)
        return h.hexdigest() == etag
    parts = int(etag.rsplit("-", 1)[1])
    lo = max(1, math.ceil(size / parts / MIB))                    # part sizes (whole MiB) giving `parts` parts
    for p_mib in range(lo, lo + 64):
        p = p_mib * MIB
        if math.ceil(size / p) != parts:
            if math.ceil(size / p) < parts:
                break
            continue
        if s3_etag(path, p) == etag:
            return True
    return False


def verify_entry(path: Path, e: Entry) -> str | None:
    """None if ``path`` is the file ``e`` describes, else the reason it is not."""
    if not path.is_file():
        return "missing"
    if path.stat().st_size != e.size:
        return f"size {path.stat().st_size} != {e.size}"
    if e.sha256 and sha256_file(path) != e.sha256:
        return "sha256 mismatch"
    if e.etag and not etag_matches(path, e.etag):
        return "S3 ETag mismatch"
    return None


# ---- download / extract ---------------------------------------------------------------------------------
def download(url: str, dest: Path, log=print) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    t0, n = time.time(), 0
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            while b := r.read(MIB):
                f.write(b)
                n += len(b)
                if total and n % (32 * MIB) < MIB:
                    log(f"    {n / MIB:7.1f} / {total / MIB:.1f} MiB")
        part.replace(dest)
    finally:
        if part.exists():
            part.unlink()
    log(f"    {n / MIB:.1f} MiB in {time.time() - t0:.0f} s")


def safe_extract(zip_path: Path, out_dir: Path) -> list:
    """Extract a zip, refusing any member that would land outside ``out_dir``. Returns the files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    root = out_dir.resolve()
    files = []
    with zipfile.ZipFile(zip_path) as z:
        for m in z.infolist():
            target = (out_dir / m.filename).resolve()
            if root != target and root not in target.parents:
                raise ValueError(f"unsafe path in {zip_path.name}: {m.filename}")
            if m.is_dir():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(m) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            files.append(target)
    return files


def fetch(entries, models_dir: Path, log=print) -> dict:
    """Download (if needed) and verify each entry -> {relative path: sha256} for the lock file."""
    locked = {}
    for e in entries:
        dest = models_dir / e.dest
        why = verify_entry(dest, e)
        if why is None:
            log(f"ok     {e.dest} (already present and verified)")
        else:
            log(f"get    {e.dest}  {e.size / MIB:.1f} MiB  <- {e.url}")
            download(e.url, dest, log)
            why = verify_entry(dest, e)
            if why is not None:
                bad = dest.with_name(dest.name + ".rejected")
                dest.replace(bad)
                raise RuntimeError(f"{e.dest}: {why} - kept as {bad.name} for inspection, NOT used")
            log(f"ok     {e.dest} verified")
        locked[e.dest] = sha256_file(dest)
        if e.extract:
            for f in safe_extract(dest, models_dir / e.extract):
                locked[f.relative_to(models_dir).as_posix()] = sha256_file(f)
            log(f"ok     extracted to {e.extract}")
    return locked


def export_osnet(models_dir: Path, log=print, python=sys.executable) -> Path:
    """torchreid's own tools/export.py (pinned commit) -> osnet_x1_0_msmt17.onnx."""
    weights = models_dir / "osnet" / "osnet_x1_0_msmt17.pt"
    if verify_entry(weights, next(e for e in MANIFEST if e.group == "osnet")) is not None:
        raise RuntimeError("fetch the OSNet weights first (they are verified before export)")
    src = models_dir / "_build" / "deep-person-reid"
    if not (src / "tools" / "export.py").is_file():
        src.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--quiet", TORCHREID_REPO, str(src)], check=True)
    subprocess.run(["git", "-C", str(src), "checkout", "--quiet", TORCHREID_COMMIT], check=True)
    env = dict(os.environ, PYTHONPATH=str(src) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    log("export OSNet -> ONNX with torchreid tools/export.py (needs torch, torchvision, onnx, pandas)")
    subprocess.run([python, "tools/export.py", "-p", str(weights), "--include", "onnx"], cwd=src, env=env, check=True)
    out = weights.with_suffix(".onnx")
    if not out.is_file():
        raise RuntimeError(f"torchreid export did not produce {out}")
    return out


def detect_arch() -> str | None:
    try:
        out = subprocess.run(["hailortcli", "fw-control", "identify"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if "Architecture" in line:
            a = line.split(":", 1)[1].strip().lower()
            return "hailo8l" if "8l" in a else "hailo8" if "8" in a else None
    return None


def select(arch: str, groups=None) -> list:
    arches = {"all": ("hailo8", "hailo8l")}.get(arch, (arch,))
    return [e for e in MANIFEST if (e.arch is None or e.arch in arches) and (not groups or e.group in groups)]


def read_lock(models_dir: Path) -> dict:
    p = models_dir / LOCK
    return json.loads(p.read_text()) if p.is_file() else {}


def verify_lock(models_dir: Path, log=print) -> bool:
    lock = read_lock(models_dir)
    if not lock:
        log(f"no {LOCK} in {models_dir}: run fetch first")
        return False
    ok = True
    for rel, digest in sorted(lock.items()):
        p = models_dir / rel
        state = "missing" if not p.is_file() else ("ok" if sha256_file(p) == digest else "CHANGED")
        ok &= state == "ok"
        log(f"{state:8s} {rel}")
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--models-dir", default=None, help="default: identity.models_dir from the config")
    ap.add_argument("--config", default=None)
    ap.add_argument("--arch", default="auto", choices=["auto", "hailo8", "hailo8l", "all"],
                    help="Hailo architecture for the HEFs (auto: ask hailortcli)")
    ap.add_argument("--only", nargs="+", choices=["face", "osnet", "clip", "detection"], help="subset of groups")
    ap.add_argument("--export-osnet", action="store_true", help="also build osnet_x1_0_msmt17.onnx (needs torch)")
    ap.add_argument("--verify", action="store_true", help="offline check against models.lock.json, no downloads")
    ap.add_argument("--list", action="store_true", help="print what would be downloaded and the total size")
    a = ap.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from eagle.config import load_config
    from eagle.identity import merged_config
    models_dir = Path(os.path.expanduser(a.models_dir or merged_config(load_config(a.config).get("identity"))["models_dir"]))
    if a.verify:
        return 0 if verify_lock(models_dir) else 1
    arch = a.arch
    if arch == "auto":
        arch = detect_arch()
        if arch is None:
            print("Could not detect the Hailo (hailortcli). Pass --arch hailo8 (26 TOPS) or hailo8l (13 TOPS).")
            return 2
        print(f"Hailo architecture: {arch}")
    entries = select(arch, a.only)
    total = sum(e.size for e in entries)
    print(f"{len(entries)} file(s), {total / 1e6:.0f} MB, into {models_dir}")
    if a.list:
        for e in entries:
            print(f"  {e.size / 1e6:8.1f} MB  {e.dest}")
        return 0
    lock = read_lock(models_dir)
    lock.update(fetch(entries, models_dir))
    if a.export_osnet:
        onnx = export_osnet(models_dir)
        lock[onnx.relative_to(models_dir).as_posix()] = sha256_file(onnx)
    (models_dir / LOCK).write_text(json.dumps(lock, indent=1, sort_keys=True))
    print(f"wrote {models_dir / LOCK} ({len(lock)} files). Runtime never downloads; re-check with --verify.")
    if arch in ("hailo8", "hailo8l"):
        print(f"CLIP HEFs for {arch}: set identity.models.clip_image_hef / clip_text_hef to "
              f"{models_dir}/clip/{arch}/... (or copy them to {models_dir}/clip/).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
