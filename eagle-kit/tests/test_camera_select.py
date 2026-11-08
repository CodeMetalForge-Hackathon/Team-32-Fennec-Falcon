"""eagle/camera.py (pick a Pi camera by sensor name) and config keys_file."""
import pytest

from eagle.camera import camera_num, configured_sensor, tuning_for
from eagle.config import load_keys

INFOS = [{"Num": 0, "Model": "imx708"}, {"Num": 1, "Model": "imx708_wide_noir"}]


def test_camera_num_matches_sensor_exactly():
    assert camera_num("imx708", INFOS) == 0
    assert camera_num("imx708_wide_noir", INFOS) == 1
    assert camera_num("imx708", INFOS[::-1]) == 0                 # by name, not by list position
    with pytest.raises(LookupError, match="imx708_wide_noir"):
        camera_num("imx708_wide", INFOS)                           # no substring matches


def test_tuning_follows_sensor_unless_overridden():
    assert tuning_for("imx708") is None                           # libcamera's default imx708.json
    assert tuning_for("imx708_wide_noir") == "imx708_wide_noir.json"
    assert tuning_for(None) == "imx708_wide_noir.json"            # legacy behaviour without a sensor
    assert tuning_for("imx708", "none") is None
    assert tuning_for("imx708", "custom.json") == "custom.json"


def test_configured_sensor():
    assert configured_sensor({"camera": {"sensor": "imx708"}}) == "imx708"
    assert configured_sensor({}) is None and configured_sensor(None) is None


def test_keys_file_overrides_test_keys_and_must_exist(tmp_path):
    test = {"keys": {"tag_hmac": "aa" * 32, "fix_aes": "bb" * 32, "covert": None}}
    f = tmp_path / "nest_keys.yaml"
    f.write_text(f"keys:\n  tag_hmac: '{'11' * 32}'\n  fix_aes: '{'22' * 32}'\n  covert: '{'33' * 32}'\n")
    k = load_keys(dict(test, keys_file=str(f)))
    assert k == {"tag_hmac": b"\x11" * 32, "fix_aes": b"\x22" * 32, "covert": b"\x33" * 32}
    assert load_keys(test)["fix_aes"] == b"\xbb" * 32
    with pytest.raises(FileNotFoundError):
        load_keys(dict(test, keys_file=str(tmp_path / "missing.yaml")))
