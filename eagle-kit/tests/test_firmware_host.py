"""Compile the two sketches for the HOST (g++ + stub Arduino/RadioLib/NVS, OpenSSL in place of
mbedTLS) and drive them: catches C++ errors and proves the tag firmware emits exactly the bytes
the Python reference produces. Skipped when g++ or OpenSSL headers are missing. This does not
replace an ESP32 build (Arduino IDE / arduino-cli), which this environment cannot run."""
import random
import shutil
import subprocess

import pytest

from eagle import protocol as P
from eagle.link import parse_radio_line


def build(root, tmp_path, sketch):
    if shutil.which("g++") is None:
        pytest.skip("g++ not available")
    exe = tmp_path / sketch.replace("/", "_")
    cmd = ["g++", "-std=c++17", "-Wall", "-Wextra", "-x", "c++",
           f'-DSKETCH="{root / "firmware" / sketch}"', f"-I{root / 'tests/firmware_host/stubs'}",
           str(root / "tests/firmware_host/harness.cpp"), "-o", str(exe), "-lcrypto"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 and "openssl" in r.stderr:
        pytest.skip("OpenSSL development headers not available")
    assert r.returncode == 0, r.stderr
    sketch_warnings = [l for l in r.stderr.splitlines() if "firmware/" in l and "warning" in l]
    assert not sketch_warnings, sketch_warnings
    return exe


def run(exe, lines):
    out = subprocess.run([str(exe)], input="\n".join(lines) + "\n", capture_output=True, text=True,
                         timeout=30).stdout
    blocks, cur = [], []
    for l in out.splitlines():
        if l == "END":
            blocks.append(cur)
            cur = []
        else:
            cur.append(l)
    return blocks          # blocks[0] = setup(), then one block per input line


def test_eagle_radio_line_protocol(root, tmp_path):
    exe = build(root, tmp_path, "eagle_radio/eagle_radio.ino")
    b = run(exe, ["SER PING", "SER CFG 915.000 7 125 5", "SER TX -3 0102ab", "SER TX 30 01",
                  "SER TX 5 zz", "AIR -97 -4.5 0a0b0c", "SER CFG 915.0 13 125 5", "SER FOO", "LOOP"])
    assert b[0] == ["READY eagle_radio 1"]                       # silent at boot
    assert b[1] == ["PONG"] and b[2] == ["OK CFG"]
    assert b[3][0].startswith("OK TX ") and b[3][1] == "TXFRAME -3 0102ab"
    assert b[4][1] == "TXFRAME 22 01"                            # clamped to the SX1262 maximum
    assert b[5][0].startswith("ERR") and not any(l.startswith("TXFRAME") for l in b[5])
    kind, frame = parse_radio_line(b[6][0])
    assert kind == "rx" and frame.payload == b"\x0a\x0b\x0c" and frame.rssi == -97 and frame.snr == -4.5
    assert b[7][0].startswith("ERR") and b[8][0].startswith("ERR")
    assert b[9] == []                                            # idle: never transmits on its own


def test_tag_firmware_matches_python(root, tmp_path, keys):
    exe = build(root, tmp_path, "tag/tag.ino")
    sess = P.EagleSession(1, keys["tag_hmac"], keys["fix_aes"], counter=P.CounterStore(None, start=41))
    ch1, n1 = sess.make_challenge(500, -9)          # counter 42
    ch2, _ = sess.make_challenge(300, 10)           # counter 43, weak link
    forged = bytearray(sess.make_challenge(500, -9)[0])
    forged[-1] ^= 1
    old = P.encode_challenge(keys["tag_hmac"], 1, P.make_nonce(5, b"abcd"), 500, -9)
    b = run(exe, [f"AIR -80 7 {ch1.hex()}", f"AIR -80 7 {ch1.hex()}", f"AIR -122.5 -9 {ch2.hex()}",
                  f"AIR -60 9 {bytes(forged).hex()}", f"AIR -60 9 {old.hex()}", "AIR -60 9 0102"])
    assert "SELFTEST challenge OK, covert mask OK, reply OK" in b[0]
    ref = P.TagLogic(7, keys["tag_hmac"], keys["covert"], sens_dbm=-121.5, margin_db=10, rng=random.Random(0))

    def tx(block):
        return [l.split() for l in block if l.startswith("TXFRAME")]

    def delays(block):
        return [int(l.split()[1]) for l in block if l.startswith("DELAY")]

    r1 = ref.on_frame(ch1, -80)
    assert tx(b[1]) == [["TXFRAME", str(r1.tx_dbm), r1.frame.hex()]]       # byte-identical reply
    assert all(50 <= d <= 500 for d in delays(b[1])) and delays(b[1])
    assert P.decode_reply(keys["tag_hmac"], bytes.fromhex(tx(b[1])[0][2]), n1).tag_id == 7
    assert tx(b[2]) == []                                                  # replay: silent
    r2 = ref.on_frame(ch2, -122.5)                                         # PL 132.5 -> +21 dBm
    assert tx(b[3]) == [["TXFRAME", str(r2.tx_dbm), r2.frame.hex()]] and r2.tx_dbm == 21
    assert all(50 <= d <= 300 for d in delays(b[3]))
    assert tx(b[4]) == [] and tx(b[5]) == [] and tx(b[6]) == []            # forged / old / junk
