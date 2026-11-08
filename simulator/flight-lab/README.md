# Eagle: actual flight-controller simulation

This adds a managed ArduCopter SITL process and pymavlink mission supervisor to the explanatory browser demo. It is included under simulator/flight-lab. The Jungle Simulator can display this local service in its navigation panel. It does not drive the main jungle scene or consume the thermal-cam branch's live sensor data.

## Run on this Windows machine

Python 3.12 was used for validation. Run these commands from `simulator/flight-lab`. The setup downloads dependencies into `vendor/` (ignored by Git).

```powershell
python setup_flight.py
python flight_lab.py
# In another terminal:
python flight_lab.py --external-nav --port 8767
```

- http://127.0.0.1:8766 : simulated GPS + IMU flight.
- http://127.0.0.1:8767 : GPS disabled; ArduPilot virtual Vicon external-position input.
- Open the Jungle Simulator and choose **Open local flight controller** to view the GPS-disabled service inside the page.

Wait for calibration, then Start autonomous flight. A flight takes roughly a minute on this machine. The supervisor requests GUIDED, normal arming, 6 m takeoff, five survey stops, return, LAND, then verifies a disarmed autopilot heartbeat. Fly again repeats it. Abort & land stops the mission. Failure controls remove GPS or virtual external position, respectively, and the supervisor requests landing. No direct motor commands or arbitrary vehicle endpoint are exposed.

The rendering uses live position/attitude telemetry, not a precomputed animated trajectory. The oblique canvas is a lightweight viewer, not a Gazebo/Unreal physics engine. ArduPilot's built-in SITL model supplies dynamics and simulated sensor data.

## Incorporated open-source software

| Component | Used here | Upstream |
|---|---|---|
| ArduPilot / ArduCopter | Actual flight controller, EKF3, control loops, motor outputs and multicopter physics | https://github.com/ArduPilot/ardupilot |
| pymavlink 2.4.50 | Python MAVLink command, telemetry, mode and parameter interface | https://github.com/ArduPilot/pymavlink |
| ArduPilot virtual Vicon | GPS-disabled simulated external-navigation input over virtual serial5 | https://ardupilot.org/dev/docs/adding_simulated_devices.html#testing-vicon-aka-vision-positioning |

ArduCopter reports version 4.7.3. Binaries came from the official `Tools/MissionPlanner/sitl/CopterStable/` directory on November 7, 2026. Its `git.txt` reports commit `bf693f316b0144fb4fac3893956f80f4c277fa3f`; the commit subject references Plane, so the actual Copter version is taken from MAVLink AUTOPILOT_VERSION, not inferred from the directory name. `sitl-manifest.json` pins downloaded file hashes. The bootstrap refuses silently changed upstream binaries. ArduPilot is GPL-3.0; pymavlink is LGPL-3.0. Retain upstream licenses and corresponding-source access when redistributing binaries; no external project is relicensed as Apache-2.0 by this demo.

## Coordinate and command contract

The flight stack uses local **NED**: x north, y east, z down, metres; takeoff target z=-6. The earlier navigation prototype uses ENU. Do not connect them without converting `[E,N,U]` to `[N,E,-U]`, handling attitude separately, and aligning origin and timestamps. The current fixed survey does not use the earlier A* planner.

The supervisor sends position-only SET_POSITION_TARGET_LOCAL_NED (mask 3576), reads LOCAL_POSITION_NED and ATTITUDE, requests flight modes and takeoff using MAVLink, and uses telemetry for waypoint completion. Commands are logged with acknowledgements; a sent command alone does not establish success. Peripheral heartbeats are excluded from aircraft arming/mode state. Virtual RC remains neutral with low throttle; arming checks are not globally disabled.

Official interface reference: https://ardupilot.org/dev/docs/copter-commands-in-guided-mode.html

## GPS-disabled configuration: what it actually demonstrates

`--external-nav` uses `--serial5 sim:vicon:`, MAVLink protocol on serial5, VISO_TYPE=2 and EK3_SRC1 position, velocity and yaw sources set to ExternalNav (6). Both GPS receiver types are disabled. A known simulated test-site origin is supplied so EKF3 can initialize; that is not a GPS fix. The Vicon sensor generates pose from simulator ground truth. Its default noise model is idealized. It is neither actual motion-capture hardware nor a visual-inertial estimator running on camera images.

This validates that the real autopilot can accept external navigation and execute a mission without GPS in this model. It does **not** validate Camera Module 3 tracking, monocular scale, rolling-shutter effects, drift under foliage, canopy navigation, or real-world performance. See the official external-navigation contract: https://ardupilot.org/dev/docs/mavlink-nongps-position-estimation.html

## How the demonstrated stack becomes Eagle

1. **Perception on the Pi:** integrate Alex's thermal-cam fusion events with mission logic. A heat detection needs bearing/depth and pose uncertainty before becoming a map location. Thermal, RGB and microphone readings are not connected here.
2. **Localization:** evaluate [OpenVINS](https://github.com/rpng/open_vins) with calibrated camera intrinsics, synchronized camera/IMU timestamps, camera–IMU extrinsics and metric scale. Replace virtual Vicon with validated odometry. ArduPilot prefers MAVLink ODOMETRY for this integration. Do not transmit unscaled monocular position as metres.
3. **Obstacle sensing and planning:** add depth/range hardware appropriate for branches and terrain, then evaluate [EGO-Planner-Swarm](https://github.com/ZJU-FAST-Lab/ego-planner-swarm) in single-drone mode with its required odometry and environment inputs. The provided RGB/thermal hardware alone does not establish reliable instantaneous obstacle depth. This demo flies an obstacle-free test field.
4. **Environment testing:** [ardupilot_gazebo](https://github.com/ArduPilot/ardupilot_gazebo) supplies the next simulation integration for camera/depth streams, collision geometry and richer scenes. It is not installed or running here. Existing Unreal/Three.js scenes can visualize telemetry, but rendering a forest does not create a validated sensor or collision model.
5. **Flight hardware:** keep stabilization, motor mixing and flight failsafes on a supported flight controller. The Pi handles perception and mission planning. Hailo acceleration is model-dependent; it does not replace the flight controller. Perching, battery-aware search, communications, wind testing and real obstacle avoidance remain separate work.

## Verification and artifacts

`python verify_flight.py` tests both running servers: automatic arm/takeoff/survey/return/land, final disarmed state, all six target arrivals, real motor telemetry above idle, and zero GPS fix in external-nav mode. Successful recordings are written to `artifacts/verified-flight-8766.json`, `artifacts/verified-flight-8767.json` and `artifacts/flight-verification.json`. Runtime console logs and latest mission data are in `flight-runtime/` and `flight-runtime-vicon/`.

Failure controls intentionally command a landing; they are not evidence of an independently triggered firmware EKF failsafe. Unknown forest obstacles, human detection, perching, range safety and autonomous recovery from every failure are outside this demonstrator.
