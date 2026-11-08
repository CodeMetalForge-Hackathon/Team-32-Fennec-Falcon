"""Settings for the Eagle Unreal demo.

Edit these, then run Eagle Sim > 1. Build Taiwan scene again.
Distances are metres in a local frame: x = east, y = north, z = up,
measured from SITE_LON / SITE_LAT. The Unreal adapter converts to centimetres.
"""

# ---------------------------------------------------------------- site
# A forested slope in Taiwan's Central Mountain Range (Nantou County).
# Any lon/lat works: everything is snapped to the real terrain after the tiles load.
SITE_LON = 121.10
SITE_LAT = 23.80

# Cesium ion asset for the ground:
#   2275207 = Google Photorealistic 3D Tiles (real imagery draped on real terrain)
#   1       = Cesium World Terrain (then add Bing imagery from the Cesium panel)
TILESET_ION_ASSET_ID = 2275207
# Leave empty if you signed in from the Cesium panel (it stores a default token).
CESIUM_ION_TOKEN = ""

LOCAL_UTC_OFFSET_H = 8.0
SOLAR_TIME_H = 10.0           # mid-morning, when real raptors start soaring
DATE_DMY = (7, 11, 2026)

# ---------------------------------------------------------------- scenario layout
OPERATOR_EN = (35.0, 215.0)   # the hidden isolated person, inside search cell B
OPERATOR_YAW_DEG = 35.0
ANIMAL_EN = (-150.0, -15.0)   # a muntjac-sized warm animal inside cell A (false-positive check)
SEARCH_CELLS_EN = [            # evasion-plan areas from the person's EPA
    (-160.0, -40.0),           # cell A
    (20.0, 190.0),             # cell B (the person is here)
    (210.0, -150.0),           # cell C
]
INGRESS_START_EN = (1200.0, -900.0)   # birds arrive from the east (the Nest boat side)
TREES = 300
TREE_RADIUS_M = 260.0         # trees scattered around the middle of the cells
COVER_TREES = 4               # trees placed close around the person
SEED = 32

# ---------------------------------------------------------------- eagle flight
# Design point from the Hawk-Eagle doc: 1.65 m span, 2.4 kg, cruise about 12-13 m/s.
SPEED_MS = 12.0
INGRESS_AGL_M = 150.0
SEARCH_AGL_M = 100.0
SEARCH_RADIUS_M = 100.0
SEARCH_LAPS = 1.25            # laps per cell before moving on
CONFIRM_AGL_M = 60.0
CONFIRM_RADIUS_M = 40.0
CONFIRM_MAX_LAPS = 2.5
HOLD_AGL_M = 120.0
HOLD_RADIUS_M = 90.0
TIME_SCALE = 3.0              # simulated seconds per real second (whole run is about 1.5 min real)

# ---------------------------------------------------------------- thermal camera
# FLIR Boson+ 640 class with a 32 degree lens. Detection needs MIN_PIXELS across a 0.45 m torso.
CAM_PIXELS_H = 640
CAM_PIXELS_V = 512
CAM_HFOV_DEG = 32.0
MIN_PIXELS = 3.0
TARGET_M = 0.45
CUE_GLIMPSES = 3              # separate glimpses (1.5 s apart) within CUE_WINDOW_S start a confirm orbit
CUE_WINDOW_S = 12.0
CONFIRM_VIEWS = 8             # visible views needed from ...
CONFIRM_BINS = 4              # ... at least this many 30-degree azimuth bins (airborne optical sectioning)
AZ_BINS = 12

# The person carries no technology; all observations originate on the bird.

# ---------------------------------------------------------------- ATAK / Cursor-on-Target
# ATAK listens to SA multicast 239.2.3.1:6969 by default. For unicast, add (<phone ip>, port)
# using the UDP input port shown in ATAK's Network Preferences > Manage Inputs.
COT_TARGETS = [("239.2.3.1", 6969)]
COT_MCAST_IFACE = ""          # local IP of the Wi-Fi adapter for multicast, if the OS picks the wrong one
COT_PERIOD_S = 2.0            # real seconds between eagle position updates
EAGLE_CALLSIGN = "EAGLE-1"
PERSON_CALLSIGN = "IP-1"

# ---------------------------------------------------------------- Unreal views (ignored by the tests)
CINEMATIC_SLOWMO = True       # play the perch observation at real time
CHASE_BACK_M = 14.0           # chase camera distance behind and above the bird
CHASE_UP_M = 4.0
OVERVIEW_BACK_M = 650.0       # overview camera: this far south-east of the search cells ...
OVERVIEW_UP_M = 520.0         # ... and this high above them
LABEL_SCALE = 0.022           # world labels keep a constant screen size: height = distance x this
TEXT_YAW_FIX_DEG = 0.0        # set to 180 if on-screen text or labels are missing or mirrored, then
                              # run Eagle Sim > Re-place forest and props
GROUND_TTL_S = 4.0            # re-check terrain height this often (tiles sharpen as the camera moves)
TILE_WAIT_S = 150.0           # stop waiting for Cesium tiles after this many seconds
