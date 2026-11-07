"""List cameras: DirectShow names + formats (via pygrabber), and which MSMF index is the thermal camera.

stream.py uses the Media Foundation (MSMF) backend because only it delivers the raw
YUY2 bytes. MSMF indices normally follow the same order as the DirectShow list.
"""
import cv2

from stream import FRAME_BYTES, HEIGHT, WIDTH

try:
    from pygrabber.dshow_graph import FilterGraph
except ImportError:
    FilterGraph = None

if FilterGraph:
    names = FilterGraph().get_input_devices()
    print("DirectShow devices:")
    for i, name in enumerate(names):
        g = FilterGraph()
        g.add_video_input_device(i)
        fmts = sorted({(f["media_type_str"], f["width"], f["height"], round(f["max_framerate"]))
                       for f in g.get_input_device().get_formats()})
        print(f"  [{i}] {name}")
        for fmt in fmts:
            print("       %s %dx%d @ %d fps" % fmt)
else:
    print("pygrabber not installed; skipping DirectShow device names (pip install pygrabber)")
    names = [None] * 4

print("\nMSMF probe for raw %dx%d YUY2 (takes a few seconds per device):" % (WIDTH, HEIGHT))
for i in range(len(names)):
    cap = cv2.VideoCapture(i, cv2.CAP_MSMF, [cv2.CAP_PROP_FRAME_WIDTH, WIDTH, cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT])
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    thermal = False
    if cap.isOpened() and (w, h) == (WIDTH, HEIGHT):
        cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
        ok, frame = cap.read()
        thermal = ok and frame.size == FRAME_BYTES
    print(f"  MSMF [{i}] opened={cap.isOpened()} {w}x{h}" + ("  <-- thermal camera (raw data OK)" if thermal else ""))
    cap.release()
