"""apps/fetch_models.py: checksums, verified downloads (file:// URLs, no network), lock file, no runtime downloads."""
import hashlib
import importlib.util
import json
import re
import sys
import zipfile

import pytest


@pytest.fixture(scope="module")
def fm(root):
    spec = importlib.util.spec_from_file_location("fetch_models", root / "apps/fetch_models.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fetch_models"] = mod                             # dataclasses look the module up
    spec.loader.exec_module(mod)
    return mod


def entry(fm, path, data, **kw):
    d = dict(name="x", group="clip", url=path.as_uri(), dest="sub/" + path.name, size=len(data),
             sha256=hashlib.sha256(data).hexdigest())
    d.update(kw)
    return fm.Entry(**d)


def test_s3_etag_single_and_multipart(fm, tmp_path):
    data = bytes(range(256)) * 20000                              # ~4.9 MiB
    p = tmp_path / "blob"
    p.write_bytes(data)
    assert fm.etag_matches(p, hashlib.md5(data).hexdigest())
    part = 2 * fm.MIB
    md5s = b"".join(hashlib.md5(data[i:i + part]).digest() for i in range(0, len(data), part))
    etag = f"{hashlib.md5(md5s).hexdigest()}-3"
    assert fm.s3_etag(p, part) == etag and fm.etag_matches(p, etag)
    assert not fm.etag_matches(p, etag.replace(etag[0], "0" if etag[0] != "0" else "1", 1))
    assert not fm.etag_matches(p, "0" * 32)


def test_fetch_verifies_and_rejects(fm, tmp_path):
    src = tmp_path / "src" / "w.bin"
    src.parent.mkdir()
    data = b"weights" * 1000
    src.write_bytes(data)
    models = tmp_path / "models"
    e = entry(fm, src, data)
    logs = []
    lock = fm.fetch([e], models, log=logs.append)
    assert (models / "sub/w.bin").read_bytes() == data and lock == {"sub/w.bin": e.sha256}
    lock2 = fm.fetch([e], models, log=logs.append)                     # second run: no download
    assert lock2 == lock and any("already present and verified" in s for s in logs)
    bad = entry(fm, src, data, dest="sub/bad.bin", sha256="0" * 64)
    with pytest.raises(RuntimeError, match="sha256 mismatch"):
        fm.fetch([bad], models, log=logs.append)
    assert not (models / "sub/bad.bin").exists() and (models / "sub/bad.bin.rejected").exists()
    assert not list(models.rglob("*.part"))
    wrong_size = entry(fm, src, data, dest="sub/s.bin", size=len(data) + 1)
    with pytest.raises(RuntimeError, match="size"):
        fm.fetch([wrong_size], models, log=logs.append)


def test_zip_extract_and_lock_verify(fm, tmp_path):
    z = tmp_path / "m.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("det_10g.onnx", b"det")
        zf.writestr("w600k_r50.onnx", b"rec")
    data = z.read_bytes()
    models = tmp_path / "models"
    e = entry(fm, z, data, dest="insightface/m.zip", extract="insightface/models/buffalo_l")
    lock = fm.fetch([e], models, log=lambda s: None)
    assert (models / "insightface/models/buffalo_l/det_10g.onnx").read_bytes() == b"det"
    assert "insightface/models/buffalo_l/w600k_r50.onnx" in lock
    (models / fm.LOCK).write_text(json.dumps(lock))
    assert fm.verify_lock(models, log=lambda s: None)
    (models / "insightface/models/buffalo_l/det_10g.onnx").write_bytes(b"tampered")
    out = []
    assert not fm.verify_lock(models, log=out.append) and any(s.startswith("CHANGED") for s in out)


def test_zip_path_traversal_refused(fm, tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../../outside.txt", b"x")
    with pytest.raises(ValueError, match="unsafe path"):
        fm.safe_extract(z, tmp_path / "out")
    assert not (tmp_path / "outside.txt").exists()


def test_manifest_is_pinned_and_licensed(fm, root):
    notice = (root / "eagle/data/NOTICE.md").read_text(encoding="utf-8")
    for e in fm.MANIFEST:
        assert e.size > 0 and (e.sha256 or e.etag), e.name           # every file has a checksum
        assert e.url.startswith("https://") and not e.dest.startswith(("/", ".."))
        assert f"[{e.license}]" in notice, f"licence {e.license!r} of {e.name} not in NOTICE.md"
    assert "non-commercial" in notice.lower() and "insightface" in notice.lower()
    names = {e.dest for e in fm.select("hailo8l")}
    assert "clip/hailo8l/clip_vit_b_32_image_encoder.hef" in names and not any("/hailo8/" in n for n in names)
    assert len(fm.select("all")) == len(fm.MANIFEST)


def test_runtime_code_never_downloads(root):
    """Downloads live only in apps/fetch_models.py."""
    pat = re.compile(r"\burllib\b|\brequests\.|\burlopen\b|http\.client|\bwget\b|\bcurl\b|\bgdown\b|hf_hub|"
                     r"snapshot_download|download_resources")
    for rel in ("eagle/identity.py", "eagle/fusion.py", "eagle/vision.py", "eagle/thermal.py",
                "apps/hailo_vision.py", "apps/enroll_target.py", "apps/life_node.py"):
        code = "\n".join(ln for ln in (root / rel).read_text(encoding="utf-8").splitlines()
                         if not ln.lstrip().startswith("#"))
        code = re.sub(r'"""[\s\S]*?"""', "", code)                    # docstrings may mention the fetch script
        assert not pat.search(code), f"{rel} contains download code"


def test_hailo_vision_uses_fetched_detection_hef(root, tmp_path):
    spec = importlib.util.spec_from_file_location("hv2", root / "apps/hailo_vision.py")
    hv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hv)
    (tmp_path / "hailo" / "hailo8l").mkdir(parents=True)
    hef = tmp_path / "hailo" / "hailo8l" / "yolov5m_wo_spp.hef"
    hef.write_bytes(b"x")
    _, rest = hv.split_args(["--models-dir", str(tmp_path)])
    assert rest == ["--input", "rpi", "--hef-path", str(hef)]
    _, rest = hv.split_args(["--models-dir", str(tmp_path), "--hef-path", "/my.hef"])
    assert rest.count("--hef-path") == 1 and "/my.hef" in rest
