"""Eagle: perched acoustic listening node for cooperative search and rescue.

Modules
-------
doa       GCC-PHAT / SRP-PHAT direction of arrival for any mic geometry
detect    whistle / tap / energy detectors and briefed-code pattern matching
locate    bearing -> position (flat floor, DEM ray-march, multi-perch fusion)
geo       local ENU metres <-> WGS84 lat/lon
cot       Cursor-on-Target XML + UDP/multicast/TCP senders for ATAK
protocol  CHALLENGE / REPLY / FIX / BEACON byte layouts, HMAC, AES-GCM, power control
airtime   LoRa time-on-air (Semtech formula)
link      radio transports: serial (our firmware), Meshtastic, UDP bench, in-memory mock
sim       synthetic multichannel audio for tests and the demo
pipeline  detect -> DOA -> fix glue shared by the node app and the simulation
config    YAML loading and microphone-array presets

Signs of life (multi-sensor, multi-person)
camgeo    pinhole camera + mount pose: pixel <-> ray <-> floor
thermal   thermal frame sources (synthetic, MLX90640, UVC Y16, 8-bit) and the warm-body detector
vitals    breathing / heart rate from radar signals, serial bridge reader, simulator
sounds    life-sound categories (YAMNet TFLite or a heuristic) with bearings
rfscan    BLE allowlist scan (strangers only counted), RSSI range rings
vision    person boxes from any detector -> floor positions
fusion    the life list: association, information filter, status, life score
lifesim   simulated rescue scenes driving every detector (demo + tests)
"""

__version__ = "0.1.0"
