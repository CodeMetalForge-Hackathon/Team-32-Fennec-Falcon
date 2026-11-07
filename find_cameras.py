"""List every camera index OpenCV can open, with its resolution."""
import cv2

for i in range(6):
    cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
    if cap.isOpened():
        ok, frame = cap.read()
        shape = frame.shape if ok else "no frame"
        print(f"index {i}: {shape}")
        cap.release()
