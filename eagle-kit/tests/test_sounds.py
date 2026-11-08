"""Life sounds: heuristic classifier, YAMNet mapping, events with bearings."""
import importlib.util
import os

import numpy as np
import pytest

from eagle.config import array_geometry, deep_merge
from eagle.locate import heading_to_yaw
from eagle.pipeline import Locator
from eagle.sim import add_noise, mic_positions_world, render
from eagle.sounds import (CATEGORIES, DEFAULT_CLASS_MAP, YAMNET_MAP, HeuristicClassifier, YamnetClassifier,
                          detect_life_sounds, load_class_names, locate_sounds, resample, synth_example,
                          synth_speech)


def test_heuristic_gets_80_percent_of_clean_examples():
    clf, rng = HeuristicClassifier(), np.random.default_rng(7)
    right, total, confusion = 0, 0, []
    for cat in CATEGORIES:
        for _ in range(5):
            x = synth_example(cat, 16000, rng)
            x = x + 0.003 * rng.standard_normal(x.size)
            got = clf.classify(x, 16000).category
            right += got == cat
            total += 1
            if got != cat:
                confusion.append((cat, got))
    assert right / total >= 0.8, confusion


def test_yamnet_mapping_uses_exact_class_names():
    names = load_class_names(DEFAULT_CLASS_MAP)
    assert len(names) == 521 and names[0] == "Speech" and names[42] == "Cough" and names[36] == "Breathing"
    for cat, classes in YAMNET_MAP.items():
        assert cat in CATEGORIES
        for n in classes:
            assert n in names, n


@pytest.mark.skipif(not os.environ.get("EAGLE_YAMNET_MODEL"), reason="set EAGLE_YAMNET_MODEL=/path/yamnet.tflite")
def test_yamnet_model_if_present():
    if not any(importlib.util.find_spec(m) for m in ("ai_edge_litert", "tflite_runtime", "tensorflow")):
        pytest.skip("no TFLite runtime installed")
    clf = YamnetClassifier(os.environ["EAGLE_YAMNET_MODEL"])
    lab = clf.classify(synth_speech(16000, 2.0), 16000)
    assert lab.method == "yamnet" and set(lab.detail["categories"]) == set(CATEGORIES)


def test_resample_length_and_tone():
    x = np.sin(2 * np.pi * 440 * np.arange(48000) / 48000)
    y = resample(x, 48000, 16000)
    assert y.size == 16000 and np.argmax(np.abs(np.fft.rfft(y))) == 440


def test_events_get_categories_and_bearings(cfg):
    geom = array_geometry(cfg, "respeaker_usb_v2")
    c = deep_merge(cfg, {"perch": {"enu_m": [0, 0, 3.0], "heading_deg": 0.0, "az_offset_deg": 0.0},
                         "audio": {"temperature_c": 20.0}})
    loc = Locator(c, geometry=geom)
    fs, src = geom.fs, np.array([2.0, 6.0, 0.25])
    mono = np.zeros(int(5 * fs))
    sp = synth_speech(fs, 2.5, 140, np.random.default_rng(3))
    mono[int(1 * fs):int(1 * fs) + sp.size] += sp
    mics = mic_positions_world(geom.positions, loc.perch, heading_to_yaw(0.0))
    audio = add_noise(render(mono, fs, mics, [(src, 1.0)], c=loc.c), fs, 20, [(1.0, 3.5)])
    snd = locate_sounds(audio, detect_life_sounds(audio, fs), loc)
    voice = [s for s in snd if s.category == "VOICE"]
    assert len(voice) == 1
    true_bearing = (90 - np.degrees(np.arctan2(src[1], src[0]))) % 360
    assert abs((voice[0].bearing_deg - true_bearing + 180) % 360 - 180) < 4
    assert voice[0].xy is not None and np.linalg.norm(voice[0].xy - src[:2]) < 1.5
