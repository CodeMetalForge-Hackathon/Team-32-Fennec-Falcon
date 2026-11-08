#!/usr/bin/env python3
"""Enroll a missing person: folder of photos (+ optional description) -> target_profile.npz.

Runs on the Pi with the SAME models as the runtime matcher (eagle/identity.py), so profile and
live embeddings are comparable:

* faces: InsightFace buffalo_l (SCRFD-10G + ArcFace R50) on the CPU. Photos without a face at
  least ``identity.face.min_width_px`` wide are logged and skipped for the face part;
* person crops: the largest person found by fusion/models/coco_yolov8n_320.onnx (OpenCV DNN, the
  detector fusion/app.py already uses) -> CLIP ViT-B/32 on the Hailo (hailo-apps clip HEF) + OSNet.
  A photo with no person (e.g. the jacket they wore) is embedded whole, CLIP only;
* description: CLIP ViT-B/32 text embeddings of the full text and of each comma/semicolon part;
* ``--live``: ~10 s face scan on the Pi camera (Picamera2; ``--camera SENSOR`` or ``camera.sensor`` in
  ``--config`` picks it by sensor name, tuning by sensor, see eagle/camera.py) while the person
  turns their head slowly left, right, up and down. Only good frames are kept (face >= 80 px, sharp,
  landmarks inside the frame, one face in view), up to 20 ArcFace embeddings spread across head
  angles (eagle/facescan.py). They go into the same ``face`` array of the same target_profile.npz,
  so photo and live enrollment feed the same matcher; ``--live`` and ``--photos`` combine;
* ``--verify``: run the face matcher live on the camera and print the score, right after a scan.
Under IR light at night, low-colour frames are converted to grayscale (3 channels) before SCRFD and
ArcFace, at enrollment and at runtime.

    python apps/enroll_target.py --photos ~/target_photos --description "red rain jacket, blue jeans, \
        grey beanie, about 1.75 m" --out ~/eagle/target_profile.npz
    python apps/enroll_target.py --live --out ~/eagle/target_profile.npz            # face scan only
    python apps/enroll_target.py --live --photos ~/target_photos --out ~/eagle/target_profile.npz
    python apps/enroll_target.py --verify --profile ~/eagle/target_profile.npz      # live score check
    python apps/enroll_target.py --live --camera imx708 --out ~/eagle/target_profile.npz  # standard camera
    python apps/hailo_vision.py --profile ~/eagle/target_profile.npz --jsonl ~/eagle/vision.jsonl

The camera can only be used by one program: stop hailo_vision.py before --live / --verify.

Models must be fetched beforehand (apps/fetch_models.py); nothing is downloaded here.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eagle.identity import TargetProfile, crop_box, make_runtime_embedders, merged_config  # noqa: E402

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff", ".heic", ".heif"}
PERSON_MODEL = ROOT.parent / "fusion" / "models" / "coco_yolov8n_320.onnx"


def description_prompts(text: str) -> list:
    """Full description + one prompt per comma/semicolon part (CLIP's text limit is 77 tokens)."""
    text = " ".join((text or "").split())
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"[;,\n]", text) if len(p.strip()) > 2]
    prompts = [f"a photo of a person: {text}"[:300]]
    prompts += [f"a photo of a person, {p}" for p in parts if p != text]
    return prompts


def load_person_detector(path=PERSON_MODEL):
    """fusion/app.py's YOLOv8 ONNX wrapper (cv2.dnn), reused rather than re-implemented."""
    spec = importlib.util.spec_from_file_location("fusion_app", ROOT.parent / "fusion" / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yolo = mod.Yolo(path, 320)

    def persons(rgb, conf=0.4):
        dets = yolo(np.ascontiguousarray(rgb[..., ::-1]), conf=conf)
        return sorted((d["box"] for d in dets if d["cls"] == 0),
                      key=lambda b: -(b[2] - b[0]) * (b[3] - b[1]))
    return persons


def consistent_faces(face: list, names: list, *, min_cos: float = 0.30, log=print):
    """Keep only faces that look like the same person as the rest. -> (kept faces, [(name, cos)] dropped).

    A photo whose largest face is someone else (a group shot, a person in the background) would
    otherwise teach the matcher a second identity. With 3+ faces: centre = mean of the normalised
    embeddings, refined once on the faces that agree with it; a face whose cosine to that centre is
    below ``min_cos`` is dropped. ArcFace (buffalo_l) same-person pairs usually score well above 0.4 and
    different people below 0.2, so 0.30 leaves margin on both sides. Fewer than 3 faces: kept as is."""
    if len(face) < 3 or min_cos is None:
        return face, []
    e = np.stack(face).astype(np.float32)
    e /= np.linalg.norm(e, axis=1, keepdims=True) + 1e-9
    centre = e.mean(0)
    for _ in range(2):
        centre /= np.linalg.norm(centre) + 1e-9
        agree = e @ centre >= min_cos
        if agree.sum() >= max(2, len(e) // 2):
            centre = e[agree].mean(0)
    centre /= np.linalg.norm(centre) + 1e-9
    cos = e @ centre
    kept, dropped = [], []
    for f, n, c in zip(face, names, cos):
        if c >= min_cos:
            kept.append(f)
        else:
            dropped.append((n, float(c)))
            log(f"OUTLIER {n}: face cos {c:.2f} to the other photos < {min_cos:.2f} - not the same person? dropped")
    log(f"CHECK  {len(kept)}/{len(face)} faces agree with each other (min cos {min_cos:.2f}, "
        f"lowest kept {min((c for c in cos if c >= min_cos), default=float('nan')):.2f})")
    return kept, dropped


def person_box_for_face(boxes, face_box, min_inside: float = 0.8):
    """The person box that contains the face: at least ``min_inside`` of the face box's area inside it;
    the tightest such box if several do (overlapping people). None if no person box contains it."""
    fx0, fy0, fx1, fy1 = (float(v) for v in face_box[:4])
    area = max((fx1 - fx0) * (fy1 - fy0), 1e-9)
    best, best_area = None, None
    for b in boxes:
        x0, y0, x1, y1 = (float(v) for v in b[:4])
        iw, ih = max(0.0, min(fx1, x1) - max(fx0, x0)), max(0.0, min(fy1, y1) - max(fy0, y0))
        if iw * ih / area < min_inside:
            continue
        a = (x1 - x0) * (y1 - y0)
        if best is None or a < best_area:
            best, best_area = b, a
    return best


def enroll(images, description: str, embedders, detect_persons, *, min_face_px: float = 40.0,
           consistency: float = 0.30, log=print) -> TargetProfile:
    """images: iterable of (name, RGB array or None). Returns the profile (not saved).

    Appearance (CLIP image / OSNet) comes only from the person box that contains the photo's kept face, so
    a group photo never enrolls a bystander's clothes; photos without a kept face add no person crop.
    Without a face model the largest person is used. Person-less photos (clothing) are embedded whole."""
    face, face_names, clip_img, osnet, skipped = [], [], [], [], []
    appearance = []                       # (photo name, {"clip": ..., "osnet": ...}) from person crops
    for name, rgb in images:
        if rgb is None:
            log(f"SKIP   {name}: not a readable image")
            skipped.append((name, "unreadable"))
            continue
        face_box = None
        if embedders.face is not None:
            faces = embedders.face.faces(rgb, with_boxes=True)
            usable = [f for f in faces if f[1] >= min_face_px]
            if not usable:
                why = "no face found" if not faces else f"largest face {faces[0][1]:.0f} px < {min_face_px:.0f} px"
                log(f"NOFACE {name}: {why} - skipped for face matching")
                skipped.append((name, why))
            else:
                if len(usable) > 1:
                    log(f"WARN   {name}: {len(usable)} faces, using the largest - check it is the right person")
                face.append(usable[0][0])
                face_names.append(name)
                face_box = usable[0][3]
                log(f"FACE   {name}: {usable[0][1]:.0f} px wide, det {usable[0][2]:.2f}")
        boxes = detect_persons(rgb) if detect_persons else []
        if boxes:
            if embedders.face is None:
                if len(boxes) > 1:
                    log(f"WARN   {name}: {len(boxes)} people, no face model: using the largest - check it")
                box = boxes[0]
            elif face_box is None:
                log(f"NOCROP {name}: no kept face, so no person crop (appearance skipped)")
                continue
            else:
                box = person_box_for_face(boxes, face_box)
                if box is None:
                    log(f"NOCROP {name}: the kept face is in none of the {len(boxes)} person box(es) "
                        "(appearance skipped)")
                    continue
            crop = crop_box(rgb, box)
            e = {}
            if embedders.clip is not None:
                e["clip"] = embedders.clip.image_embedding(crop)
            if embedders.osnet is not None:
                e["osnet"] = embedders.osnet(crop)
            appearance.append((name, e))
            which = f" (the one with the face, of {len(boxes)})" if len(boxes) > 1 else ""
            log(f"PERSON {name}: crop {crop.shape[1]}x{crop.shape[0]}{which} -> "
                + (", ".join(e) or "no appearance model"))
        elif embedders.clip is not None:
            clip_img.append(embedders.clip.image_embedding(rgb))
            log(f"ITEM   {name}: no person found, whole photo embedded with CLIP only (clothing/item photo)")
    face, dropped = consistent_faces(face, face_names, min_cos=consistency, log=log)
    skipped += [(n, f"face does not match the other photos (cos {c:.2f})") for n, c in dropped]
    outliers = {n for n, _ in dropped}
    for n, e in appearance:               # a crop located by a face that turned out not to be the target: drop it
        if n in outliers:
            log(f"NOCROP {n}: face was an outlier, its person crop is dropped too")
            continue
        clip_img += [e["clip"]] if "clip" in e else []
        osnet += [e["osnet"]] if "osnet" in e else []
    prompts = description_prompts(description)
    text = np.zeros((0, 512), np.float32)
    if prompts and embedders.clip is not None:
        text = embedders.clip.text_embeddings(prompts)
        log(f"TEXT   {len(prompts)} prompt(s) from the description")
    elif prompts:
        log("WARN   description given but CLIP is off: it is stored as text only")

    def stack(v):
        return np.stack(v).astype(np.float32) if v else np.zeros((0, 512), np.float32)
    meta = {"created_unix": int(time.time()), "skipped": skipped, "prompts": prompts,
            "models": {"face": "insightface buffalo_l (SCRFD-10G + ArcFace R50)" if embedders.face else None,
                       "clip": "CLIP ViT-B/32 (hailo-apps clip HEFs)" if embedders.clip else None,
                       "osnet": "torchreid OSNet (ONNX)" if embedders.osnet else None}}
    return TargetProfile(stack(face), stack(clip_img), text, stack(osnet), description or "", meta)


def _read_rgb(p: Path):
    """RGB array, upright per EXIF, or None. HEIC/HEIF (iPhone) needs pillow-heif."""
    if p.suffix.lower() in (".heic", ".heif"):
        try:
            from PIL import Image, ImageOps
            from pillow_heif import register_heif_opener
        except ImportError:
            print(f"SKIP   {p.name}: HEIC needs `pip install pillow-heif` (or convert it to JPG)")
            return None
        register_heif_opener()
        try:
            with Image.open(p) as im:
                return np.asarray(ImageOps.exif_transpose(im).convert("RGB")).copy()
        except Exception as e:  # corrupt or unsupported file
            print(f"SKIP   {p.name}: {e}")
            return None
    import cv2
    bgr = cv2.imread(str(p), cv2.IMREAD_COLOR)          # applies EXIF orientation for JPEG
    return None if bgr is None else bgr[..., ::-1].copy()


def read_images(folder: Path):
    import hashlib
    seen = {}
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() in IMAGE_EXT:
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            if digest in seen:                         # the same photo uploaded twice would count double
                print(f"DUP    {p.name}: same file as {seen[digest]} - skipped")
                continue
            seen[digest] = p.name
            yield p.name, _read_rgb(p)
        elif p.is_file():
            print(f"SKIP   {p.name}: not an image type this tool reads ({', '.join(sorted(IMAGE_EXT))})")


def add_live_faces(profile, kept: list, summary: list) -> TargetProfile:
    """Append live-scan ArcFace embeddings to ``profile`` (or a new one): same file format, same matcher."""
    prof = profile if profile is not None else TargetProfile(meta={"created_unix": int(time.time())})
    n_photo = len(prof.face)
    if kept:
        live = np.stack([c.embedding for c in kept]).astype(np.float32)
        prof = TargetProfile(np.concatenate([prof.face, live]) if n_photo else live, prof.clip_image,
                             prof.clip_text, prof.osnet, prof.description, dict(prof.meta))
    meta = dict(prof.meta)
    meta["face_sources"] = {"photos": n_photo, "live": len(kept)}
    meta["live_scan"] = {"kept": len(kept), "yaw_deg": [round(c.yaw, 1) for c in kept],
                         "pitch_deg": [round(c.pitch, 1) for c in kept],
                         "ir_gray": int(sum(bool(c.gray) for c in kept)), "summary": list(summary)}
    meta["models"] = dict(meta.get("models") or {}, face="insightface buffalo_l (SCRFD-10G + ArcFace R50)")
    prof.meta = meta
    return prof


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--photos", default=None, help="folder of photos: face shots, full body, clothing worn")
    ap.add_argument("--live", action="store_true", help="add a ~10 s live face scan from the Pi camera")
    ap.add_argument("--verify", action="store_true", help="live: print the face score vs --profile (default --out)")
    ap.add_argument("--profile", default=None, help="profile for --verify (default: --out)")
    ap.add_argument("--seconds", type=float, default=None, help="live scan / verify duration")
    ap.add_argument("--camera", default=None,
                    help="Pi camera sensor name, e.g. imx708 or imx708_wide_noir (default: camera.sensor in --config)")
    ap.add_argument("--tuning-file", default="auto",
                    help="libcamera tuning file; auto = by sensor; none = libcamera's default")
    ap.add_argument("--description", default="", help="free text, e.g. 'red rain jacket, blue jeans, grey beanie'")
    ap.add_argument("--description-file", default=None, help="read the description from a text file")
    ap.add_argument("--out", default="target_profile.npz")
    ap.add_argument("--config", default=None, help="eagle config (identity: models_dir, face.min_width_px)")
    ap.add_argument("--person-model", default=str(PERSON_MODEL))
    ap.add_argument("--same-person-cos", type=float, default=0.30,
                    help="drop photo faces whose ArcFace cosine to the others is below this (0 = keep all)")
    a = ap.parse_args(argv)
    if not (a.photos or a.live or a.verify):
        ap.error("give --photos, --live (or both), or --verify")
    from eagle.camera import configured_sensor, tuning_for
    from eagle.config import load_config
    full_cfg = load_config(a.config)
    cfg = full_cfg.get("identity", {})
    c = merged_config(cfg)
    a.camera = a.camera or configured_sensor(full_cfg)
    tuning = tuning_for(a.camera, a.tuning_file)
    if a.verify:
        return verify(a, cfg, c, tuning)
    held = {}
    try:
        return enroll_main(a, cfg, c, tuning, held)
    finally:
        if held.get("emb") is not None:
            held["emb"].close()                               # release the Hailo before interpreter exit


def enroll_main(a, cfg, c, tuning, held: dict) -> int:
    prof, emb = None, None
    if a.photos:
        desc = Path(a.description_file).read_text(encoding="utf-8") if a.description_file else a.description
        emb = held["emb"] = make_runtime_embedders(cfg, text=True)
        if emb.face is None and emb.clip is None and emb.osnet is None:
            print("No identity model could be loaded: run apps/fetch_models.py (and install hailo-apps for CLIP).")
            return 2
        folder = Path(a.photos).expanduser()
        if not folder.is_dir():
            print(f"{folder} is not a folder")
            return 2
        prof = enroll(read_images(folder), desc, emb, load_person_detector(a.person_model),
                      min_face_px=float(c["face"]["min_width_px"]),
                      consistency=None if a.same_person_cos <= 0 else a.same_person_cos)
    if a.live:
        face = emb.face if emb is not None and emb.face is not None else face_embedder(cfg, c)
        if face is None:
            return 2
        live_cfg = dict(c["live"], **({"seconds": a.seconds} if a.seconds else {}))
        from eagle.facescan import LiveFaceScan
        cam = open_camera(a.camera, tuning)
        try:
            kept, summary = LiveFaceScan(cam, face, live_cfg).scan()
        finally:
            cam.close()
        prof = add_live_faces(prof, kept, summary)
    n = prof.counts()
    if not (n["face"] or n["clip_image"] or n["osnet"]):
        print("Nothing usable was enrolled (see the log above); profile not written.")
        return 1
    out = Path(a.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    prof.save(out)
    print(f"wrote {out}: {n['face']} face, {n['clip_image']} CLIP image, {n['clip_text']} CLIP text, "
          f"{n['osnet']} OSNet embedding(s)")
    if a.live:
        print(f"Test it now: python apps/enroll_target.py --verify --profile {out}")
    return 0


def open_camera(sensor, tuning):
    """PiCamera on the camera with sensor model ``sensor`` (eagle/camera.py), or camera 0 if None."""
    from eagle.camera import camera_num
    from eagle.facescan import PiCamera
    num = camera_num(sensor) if sensor else 0
    print(f"camera: {sensor or 'first camera'} (libcamera camera {num}), tuning {tuning or 'libcamera default'}")
    return PiCamera(tuning, camera_num=num)


def face_embedder(cfg, c):
    from eagle.identity import FaceEmbedder, model_paths
    try:
        return FaceEmbedder(model_paths(cfg)["insightface_root"], int(c["face"]["det_size"]),
                            float(c["face"]["gray_threshold"]))
    except Exception as e:                                    # noqa: BLE001
        print(f"Face model not available ({e}). Run apps/fetch_models.py --only face; "
              "pip install insightface onnxruntime")
        return None


def verify(a, cfg, c, tuning) -> int:
    path = Path(a.profile or a.out).expanduser()
    if not path.is_file():
        print(f"no profile at {path}")
        return 2
    prof = TargetProfile.load(path)
    face = face_embedder(cfg, c)
    if face is None:
        return 2
    from eagle.facescan import live_verify
    cam = open_camera(a.camera, tuning)
    res = []
    try:
        res = live_verify(cam, face, prof, cfg, seconds=a.seconds or 30.0)
    except KeyboardInterrupt:
        pass
    finally:
        cam.close()
    scores = [s for _, s, _ in res if s is not None]
    if scores:
        print(f"face scores: median {np.median(scores):.2f}, max {max(scores):.2f} over {len(scores)} frame(s); "
              f"verify threshold {c['face']['verify']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
