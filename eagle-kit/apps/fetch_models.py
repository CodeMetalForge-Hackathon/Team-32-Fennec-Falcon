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
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

MIB = 1024 * 1024
MZ_BASE = "https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled"
# HEFs are compiled against one HailoRT minor version and do not load on another. Same mapping as
# hailo-apps' install.sh (get_model_zoo_version): HailoRT 4.24.x -> v2.19.0, 4.23.x (default) -> v2.18.0.
ZOO_BY_HAILORT = {"4.23": "v2.18.0", "4.24": "v2.19.0"}
DEFAULT_ZOO = "v2.18.0"
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
    zoo: str | None = None        # Hailo Model Zoo version (HEFs only; must match the installed HailoRT)


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
    # (zoo, arch, group, file, size, S3 ETag): sizes and ETags as served by Hailo's S3 bucket
]
_HEFS = [
    ("v2.18.0", "hailo8", "clip", "clip_vit_b_32_image_encoder", 96913864, "2d7850d5e0afddfd6c972977f336aa56-6"),
    ("v2.18.0", "hailo8", "clip", "clip_vit_b_32_text_encoder", 93849976, "0e137ed0ca43f77b5c9d5623cfc8347b-6"),
    ("v2.18.0", "hailo8", "detection", "yolov5m_wo_spp", 18024878, "50b3d82acad2cf2227d3fecef1be89ec-2"),
    ("v2.18.0", "hailo8l", "clip", "clip_vit_b_32_image_encoder", 191499803, "d6bc4162642f0214b11b0fa799410081-12"),
    ("v2.18.0", "hailo8l", "clip", "clip_vit_b_32_text_encoder", 167525195, "8469e9637d253f2fc9c1d6753c2f00f1-10"),
    ("v2.18.0", "hailo8l", "detection", "yolov5m_wo_spp", 37763963, "ce3f55a0b84caa9ced5151c90f366a83-3"),
    ("v2.19.0", "hailo8", "clip", "clip_vit_b_32_image_encoder", 96900770, "9214a5606c1f158378959b776cd69af5-6"),
    ("v2.19.0", "hailo8", "clip", "clip_vit_b_32_text_encoder", 93856389, "8eb2f56f41ba2f4ac6c6f419f3b8e4b8-6"),
    ("v2.19.0", "hailo8", "detection", "yolov5m_wo_spp", 18024868, "cf013b86c4216070a2dfdab3a6750f47-2"),
    ("v2.19.0", "hailo8l", "clip", "clip_vit_b_32_image_encoder", 191502011, "aca98707021c2e61f1c8340f188c6995-12"),
    ("v2.19.0", "hailo8l", "clip", "clip_vit_b_32_text_encoder", 167524982, "fa849c696c096328de4dcba2ef90a92a-10"),
    ("v2.19.0", "hailo8l", "detection", "yolov5m_wo_spp", 37764611, "2a38e748f1075b967a4c333d44dcec89-3"),
]
MANIFEST += [
    Entry(f"{'clip_' + f.split('_')[-2] if g == 'clip' else 'detection'}_{arch}_{zoo}", g,
          f"{MZ_BASE}/{zoo}/{arch}/{f}.hef", f"{'clip' if g == 'clip' else 'hailo'}/{arch}/{f}.hef", size,
          etag=etag, arch=arch, license="clip" if g == "clip" else "yolov5", zoo=zoo)
    for zoo, arch, g, f, size, etag in _HEFS
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


# Part size Hailo's S3 uploads use (S3 reports it: HEAD <url>?partNumber=1 -> Content-Length). It is not a
# whole number of MiB, so it is tried before the MiB guesses below.
KNOWN_PART_SIZES = (17179870,)


def etag_matches(path, etag: str, part_sizes=KNOWN_PART_SIZES) -> bool:
    etag = etag.strip('"')
    size = os.path.getsize(path)
    if "-" not in etag:
        h = hashlib.md5()
        with open(path, "rb") as f:
            while b := f.read(MIB):
                h.update(b)
        return h.hexdigest() == etag
    parts = int(etag.rsplit("-", 1)[1])
    for p in part_sizes:
        if math.ceil(size / p) == parts and s3_etag(path, p) == etag:
            return True
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


def detect_zoo() -> tuple:
    """(Model Zoo version, HailoRT version or None) for the HailoRT installed here."""
    try:
        out = subprocess.run(["hailortcli", "--version"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return DEFAULT_ZOO, None
    m = re.search(r"(\d+)\.(\d+)\.\d+", out)
    if not m:
        return DEFAULT_ZOO, None
    return ZOO_BY_HAILORT.get(f"{m[1]}.{m[2]}", DEFAULT_ZOO), m[0]


def select(arch: str, groups=None, zoo: str = DEFAULT_ZOO) -> list:
    arches = {"all": ("hailo8", "hailo8l")}.get(arch, (arch,))
    return [e for e in MANIFEST if (e.arch is None or e.arch in arches) and (not groups or e.group in groups)
            and e.zoo in (None, zoo)]


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
    ap.add_argument("--zoo", default="auto", choices=["auto", *sorted(set(ZOO_BY_HAILORT.values()))],
                    help="Hailo Model Zoo version of the HEFs (auto: match the installed HailoRT)")
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
    zoo = a.zoo
    if zoo == "auto":
        zoo, rt = detect_zoo()
        print(f"HailoRT {rt or 'not found'} -> Hailo Model Zoo {zoo}")
    entries = select(arch, a.only, zoo)
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
