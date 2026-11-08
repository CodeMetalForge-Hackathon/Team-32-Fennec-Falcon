# Third-party data in this folder

`yamnet_class_map.csv` is copied unchanged from the TensorFlow Model Garden:
https://github.com/tensorflow/models/blob/master/research/audioset/yamnet/yamnet_class_map.csv
(downloaded 2026-11-07, SHA-256 cdf24d193e196d9e95912a2667051ae203e92a2ba09449218ccb40ef787c6df2).
Copyright 2016 The TensorFlow Authors / Google LLC. Licensed under the Apache License 2.0;
the repository licence text is in `LICENSE-tensorflow-models.txt` (it covers /research).
Changes: none. It lists the 521 AudioSet classes in YAMNet's output order (index, mid, display_name).

# Model weights fetched by apps/fetch_models.py (not in this repository)

These files are downloaded once into `identity.models_dir` (default `~/eagle-models`) and checked
against the SHA-256 / S3 ETag pinned in `apps/fetch_models.py`. None is committed here (several are
over 50 MB), and nothing downloads them at runtime. Licences were read on 2026-11-07; check them
again before any use beyond this hackathon.

## [insightface] InsightFace buffalo_l (SCRFD-10G face detector + ArcFace R50 recognition)

https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip
(SHA-256 80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f).
**The pretrained weights are restricted to NON-COMMERCIAL RESEARCH use only.** The InsightFace
README: "The training data containing the annotation (and the models trained with these data) are
available for non-commercial research purposes only", and both manual and automatic downloads
"follow the above license policy". The InsightFace Python package (code) is MIT-licensed; the
licence restriction is on the weights. Any commercial or operational deployment needs a licence
from InsightFace or a differently-licensed face model.

## [osnet] OSNet re-ID weights (torchreid / deep-person-reid)

`osnet_x1_0` trained on MSMT17, from the torchreid model zoo (docs/MODEL_ZOO.md, same-domain table),
Google Drive id 112EMUfBPYeYg70w-syK6V6Mx8-Qb9Q1M (SHA-256
b7d73dc67c016fd044e4027ff856019496392a7aca8fa0ed56d862a1632c1cf2). torchreid
(https://github.com/KaiyangZhou/deep-person-reid) is MIT-licensed; the ONNX file is produced locally
with its `tools/export.py` at commit f8cd150fdf77e8d9e1ed143b7f308c2c609ded50. The weights were
trained on the MSMT17 dataset, which is distributed for non-commercial research under its own
release agreement: treat these weights as research-only too unless MSMT17's terms say otherwise.
(The multi-source torchreid weights were avoided because they include DukeMTMC-reID, a dataset its
authors withdrew.)

## [clip] CLIP ViT-B/32 on the Hailo (as used by the hailo-apps clip app)

Compiled HEFs `clip_vit_b_32_image_encoder.hef` / `clip_vit_b_32_text_encoder.hef` from the Hailo
Model Zoo (https://github.com/hailo-ai/hailo_model_zoo, MIT) v2.19.0 for hailo8 / hailo8l, and
`clip_tokenizer.json`, `token_embedding_lut.npy`, `text_projection.npy` from hailo-apps' resource
bucket (https://github.com/hailo-ai/hailo-apps, MIT). The underlying CLIP model and weights are
OpenAI's (https://github.com/openai/CLIP, MIT). Checked against Hailo's S3 ETags (and SHA-256 for the
two small files).

## [yolov5] Person detector HEF for apps/hailo_vision.py

`yolov5m_wo_spp.hef` (the hailo-apps detection default) from the Hailo Model Zoo v2.19.0, compiled
from Ultralytics YOLOv5, which is AGPL-3.0. Fine for this open hackathon project; a closed-source
product would need an Ultralytics licence.

## Already in the repository (for reference)

* `fusion/models/coco_yolov8n_320.onnx` (also used by apps/enroll_target.py for person crops):
  Ultralytics YOLOv8n, AGPL-3.0.
* `fusion/models/yamnet.tflite`: Google YAMNet, Apache-2.0.
* `fusion/models/thermal_person.onnx`: trained on HIT-UAV (CC BY 4.0, Suo et al., Scientific Data 2023).
