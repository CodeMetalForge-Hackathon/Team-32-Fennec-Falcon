"""The firmware and Python must agree byte for byte. The canonical layout lives in the
docstring of eagle/protocol.py; firmware/*/eagle_proto.h mirror it."""
import re
import struct

import pytest

from eagle import protocol as P

HEADERS = ["firmware/eagle_radio/eagle_proto.h", "firmware/tag/eagle_proto.h"]


def parse_defines(text: str) -> dict:
    out = {}
    for m in re.finditer(r"^#define\s+(\w+)\s+(.+?)\s*(?://.*)?$", text, re.M):
        name, val = m.group(1), m.group(2).strip()
        if val.startswith('"'):
            out[name] = val.strip('"')
        else:
            try:
                out[name] = eval(val.replace("(", " ( ").replace(")", " ) "), {}, {})  # ints, hex, floats
            except Exception:
                out[name] = val
    return out


@pytest.fixture(scope="module")
def defs(root):
    texts = [(root / h).read_text() for h in HEADERS]
    assert texts[0] == texts[1], "the two copies of eagle_proto.h differ"
    return parse_defines(texts[0])


def test_message_types_and_sizes(defs):
    assert defs["MSG_CHALLENGE"] == P.MSG_CHALLENGE and defs["MSG_REPLY"] == P.MSG_REPLY
    assert defs["MSG_FIX"] == P.MSG_FIX and defs["MSG_BEACON"] == P.MSG_BEACON
    assert defs["MAC_LEN"] == P.MAC_LEN and defs["NONCE_LEN"] == P.NONCE_LEN
    assert defs["CHAL_LEN"] == P.CHAL_LEN and defs["REPLY_LEN"] == P.REPLY_LEN
    assert defs["FIX_LEN"] == P.FIX_LEN and defs["BEACON_LEN"] == P.BEACON_LEN
    assert defs["COVERT_LABEL"].encode() == P.COVERT_LABEL and defs["COVERT_LABEL_LEN"] == len(P.COVERT_LABEL)


def _offsets(fmt: str) -> list:
    """Byte offset of each field of a big-endian struct format such as '>BB8sBb'."""
    offs, pos = [], 0
    for tok in re.findall(r"\d*[a-zA-Z]", fmt[1:]):
        offs.append(pos)
        pos += struct.calcsize(">" + tok)
    return offs


def test_offsets_match_struct_formats(defs):
    c = _offsets(P.CHAL_FMT)          # type, eagle, nonce, window, tx
    assert [defs[k] for k in ("CHAL_OFF_TYPE", "CHAL_OFF_EAGLE", "CHAL_OFF_NONCE", "CHAL_OFF_WINDOW",
                              "CHAL_OFF_TXDBM")] == c
    assert defs["CHAL_OFF_MAC"] == P.CHAL_BODY_LEN
    r = _offsets(P.REPLY_FMT)         # type, tag, nonce, status, batt, rssi
    assert [defs[k] for k in ("REPLY_OFF_TYPE", "REPLY_OFF_TAG", "REPLY_OFF_NONCE", "REPLY_OFF_STATUS",
                              "REPLY_OFF_BATT", "REPLY_OFF_RSSI")] == r
    assert defs["REPLY_OFF_MAC"] == P.REPLY_BODY_LEN


def test_status_bits(defs):
    for name in ("INJURED", "MOBILE", "WATER", "FOOD", "THREAT", "DURESS", "BUTTON", "LOWBATT"):
        assert defs[f"ST_{name}"] == getattr(P, f"ST_{name}")


def test_radio_defaults_match_config(defs, cfg):
    r = cfg["radio"]
    assert defs["RADIO_FREQ_MHZ"] == r["freq_mhz"] and defs["RADIO_SF"] == r["sf"]
    assert defs["RADIO_BW_KHZ"] == r["bw_khz"] and defs["RADIO_CR"] == r["cr"]
    assert defs["RADIO_MIN_DBM"] == r["min_dbm"] and defs["RADIO_MAX_DBM"] == r["max_dbm"]
    assert defs["LINK_MARGIN_DB"] == r["margin_db"]
    assert defs["EAGLE_SENS_DBM"] == pytest.approx(P.lora_sensitivity(r["sf"], r["bw_khz"]), abs=0.05)


def test_test_vectors_match_python(defs):
    kt, kc = bytes(range(32)), bytes(range(32, 64))
    nonce = P.make_nonce(1, bytes.fromhex("a1a2a3a4"))
    ch = P.encode_challenge(kt, 1, nonce, 500, -9)
    assert ch.hex() == defs["TV_CHALLENGE_HEX"]
    mask = P.covert_mask(kc, 7, 1, 1)
    assert mask == defs["TV_COVERT_MASK"]
    rep = P.encode_reply(kt, 7, nonce, P.ST_INJURED ^ mask, 255, -60)
    assert rep.hex() == defs["TV_REPLY_HEX"]


def test_firmware_offsets_decode_python_frames(defs):
    """Read Python-built frames with the C offsets, as the tag firmware does."""
    kt, kc = bytes(range(32)), bytes(range(32, 64))
    nonce = P.make_nonce(0x01020304, b"\x00\x11\x22\x33")
    ch = P.encode_challenge(kt, 9, nonce, 300, -5)
    assert ch[defs["CHAL_OFF_TYPE"]] == defs["MSG_CHALLENGE"] and ch[defs["CHAL_OFF_EAGLE"]] == 9
    assert ch[defs["CHAL_OFF_NONCE"]:defs["CHAL_OFF_NONCE"] + defs["NONCE_LEN"]] == nonce
    assert int.from_bytes(ch[defs["CHAL_OFF_NONCE"]:defs["CHAL_OFF_NONCE"] + 4], "big") == 0x01020304
    assert ch[defs["CHAL_OFF_WINDOW"]] == 30
    assert struct.unpack("b", ch[defs["CHAL_OFF_TXDBM"]:defs["CHAL_OFF_TXDBM"] + 1])[0] == -5
    assert ch[defs["CHAL_OFF_MAC"]:] == P.hmac8(kt, ch[:defs["CHAL_OFF_MAC"]])
    t = P.TagLogic(4, kt, kc)
    rep = t.on_frame(ch, -77.4).frame
    assert rep[defs["REPLY_OFF_TAG"]] == 4
    assert struct.unpack("b", rep[defs["REPLY_OFF_RSSI"]:defs["REPLY_OFF_RSSI"] + 1])[0] == -77
    assert rep[defs["REPLY_OFF_MAC"]:] == P.hmac8(kt, rep[:defs["REPLY_OFF_MAC"]])


def test_tag_keys_header_matches_example_config(root, keys):
    text = (root / "firmware/tag/tag_keys.h").read_text()

    def arr(name):
        body = re.search(name + r"\[32\]\s*=\s*\{(.*?)\};", text, re.S).group(1)
        return bytes(int(x, 16) for x in re.findall(r"0x([0-9a-fA-F]{2})", body))
    assert arr("K_TAG") == keys["tag_hmac"]
    assert arr("K_COVERT") == keys["covert"]


def test_keygen_outputs_are_consistent(tmp_path, root):
    import importlib.util
    import yaml
    spec = importlib.util.spec_from_file_location("keygen", root / "apps/keygen.py")
    kg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kg)
    kg.main(["--tag-id", "12", "--out-dir", str(tmp_path)])
    eagle = yaml.safe_load((tmp_path / "eagle_keys.yaml").read_text())["keys"]
    nest = yaml.safe_load((tmp_path / "nest_keys.yaml").read_text())["keys"]
    h = (tmp_path / "tag_keys.h").read_text()
    assert eagle["covert"] is None and eagle["tag_hmac"] == nest["tag_hmac"] and eagle["fix_aes"] == nest["fix_aes"]
    assert "#define TAG_ID 12" in h
    first = re.search(r"K_TAG\[32\] = \{\s*(0x[0-9a-f]{2})", h).group(1)
    assert first[2:] == nest["tag_hmac"][:2]
