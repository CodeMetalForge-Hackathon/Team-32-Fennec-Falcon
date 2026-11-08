"""Offline tests for thermal_probe.py: a fake HTI-like camera behind a fake cv2.VideoCapture."""
import os
import subprocess
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import thermal_probe as tp  # noqa: E402

cv2 = tp.cv2
W, H = 256, 192


def hti_split_frame():
    """256x384 YUYV buffer: top = picture luma, bottom = 16-bit raw with a warm blob."""
    luma = np.tile(np.linspace(30, 220, W, dtype=np.uint8), (H, 1))
    yuyv_top = np.stack([luma, np.full_like(luma, 128)], axis=2).reshape(-1)
    raw = np.full((H, W), 18900, np.uint16) + np.random.default_rng(0).integers(0, 20, (H, W)).astype(np.uint16)
    raw[90:110, 120:132] = 19750                      # person-sized warm patch
    return np.concatenate([yuyv_top, raw.reshape(-1).view(np.uint8)]).reshape(1, -1), raw


class FakeCap:
    """index 0: webcam (any backend). index 1: thermal, DirectShow only; default mode won't stream,
    256x384 YUY2 with CONVERT_RGB off gives the split frame, 256x192 gives YUYV picture."""

    def __init__(self, index, api=None, params=None):
        self.index, self.api, self.props, self.open = index, api, {}, False
        if index == 0:
            self.open = True
        elif index == 1 and api == cv2.CAP_DSHOW:
            self.open = True
        if params:
            for k, v in zip(params[::2], params[1::2]):
                self.props[k] = v

    def isOpened(self):
        return self.open

    def set(self, k, v):
        self.props[k] = v
        return True

    def get(self, k):
        if k == cv2.CAP_PROP_FRAME_WIDTH:
            return self.props.get(k, 640 if self.index == 0 else W)
        if k == cv2.CAP_PROP_FRAME_HEIGHT:
            return self.props.get(k, 480 if self.index == 0 else H)
        return self.props.get(k, 0)

    def read(self):
        if not self.open:
            return False, None
        if self.index == 0:
            return True, np.random.default_rng(1).integers(0, 255, (480, 640, 3), dtype=np.uint8)
        conv = self.props.get(cv2.CAP_PROP_CONVERT_RGB, 1)
        w, h = int(self.get(cv2.CAP_PROP_FRAME_WIDTH)), int(self.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if conv:                                      # the "OpenCV can't read the stream" symptom
            return False, None
        if (w, h) == (W, 2 * H):
            return True, hti_split_frame()[0]
        if (w, h) == (W, H):
            luma = np.tile(np.linspace(30, 220, W, dtype=np.uint8), (H, 1))
            return True, np.stack([luma, np.full_like(luma, 128)], axis=2).reshape(1, -1)
        return False, None

    def release(self):
        self.open = False


def test_decode_split():
    frame, raw = hti_split_frame()
    pic, got, kind = tp.decode(frame, W, 2 * H)
    assert kind == "split" and pic.shape == (H, W) and np.array_equal(got, raw)


def test_decode_variants():
    y16 = np.full((H, W), 30000, np.uint16)
    assert tp.decode(y16, W, H)[2] == "y16"
    assert tp.decode(y16.reshape(1, -1), W, H)[2] == "y16"
    assert tp.decode(np.zeros((H, W, 3), np.uint8), W, H)[2] == "bgr"
    assert tp.decode(np.zeros((H, W), np.uint8), W, H)[2] == "gray8"
    yuyv = np.zeros((H, W, 2), np.uint8)
    assert tp.decode(yuyv, W, H)[2] == "yuyv"


def test_scan_finds_split_mode():
    orig = cv2.VideoCapture
    cv2.VideoCapture = FakeCap
    try:
        with tempfile.TemporaryDirectory() as d:
            results, best = tp.scan([0, 1, 2], ["dshow", "msmf"], quick=True, outdir=d)
            assert best["index"] == 1 and best["backend"] == "dshow" and best["kind"] == "split"
            assert best["size"] == [256, 384] and best["convert_rgb"] is False
            assert any(f.endswith("_raw.npy") for f in os.listdir(d))
            snippet = tp.kit_snippet(best)
            assert "device: 1" in snippet and "height: 192" in snippet
    finally:
        cv2.VideoCapture = orig


def test_hot_spot():
    _, raw = hti_split_frame()
    boxes = tp.hot_spots(raw)
    assert len(boxes) == 1 and boxes[0][0] == 120 and boxes[0][1] == 90


def test_ffmpeg_reader_and_rtsp_file():
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "eagle_test.mp4")
        rc = tp.main(["--view", "--headless", "--frames", "30", "--ffmpeg", "x", "--size", "256x192",
                      "--ffmpeg-input=-f lavfi -i testsrc=size=256x192:rate=25",
                      "--rtsp", out, "--out", d])
        assert rc == 0 and os.path.getsize(out) > 1000
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height",
                                "-of", "csv=p=0", out], capture_output=True, text=True).stdout.strip()
        assert probe.startswith("h264,768,576"), probe


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
