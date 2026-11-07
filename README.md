# thermal-cam

OpenCV live radiometric stream for the HTI "Smart Mobile Phone Thermal Imager"
(Hikvision module, USB `VID_2BDF&PID_0102`, shows up as "UVC Camera").

- `python stream.py` opens the live view with min/max/center temperatures
  (q quit, c colormap, s snapshot PNG + `_celsius.npy` temperature array to `captures/`).
- `python find_cameras.py` lists DirectShow devices/formats and finds the thermal camera's MSMF index.
- F5 in VS Code runs either script; Ctrl+Shift+B starts the stream.
- Environment is a standard Python venv in `.venv`, created by setup.bat.

## How the camera data works

Sensor is 256x192. In the camera's 256x392 YUY2 mode each frame holds:

| rows    | content                                               |
|---------|-------------------------------------------------------|
| 0-191   | raw 16-bit little-endian counts, `kelvin = raw / 16`  |
| 192-195 | metadata (magic `DD CC BB AA`)                        |
| 196-387 | camera's own colorized YUYV preview                   |
| 388-391 | metadata (magic `FF FF EE EE`)                        |

The raw bytes only survive with OpenCV's Media Foundation backend (`CAP_MSMF`) and
`CAP_PROP_CONVERT_RGB = 0`. DirectShow always converts YUY2 to BGR, which destroys the raw data.
