"""Managed ArduCopter SITL only; no arbitrary vehicle connection is exposed."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'vendor' / 'python'))
from pymavlink import mavutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import deque
import subprocess
import threading
import time
import json
import math
import queue
import argparse

ROUTE = [[0, 0, -6], [0, 18, -6], [10, 18, -6], [10, -18, -6],
         [20, -18, -6], [20, 18, -6], [0, 0, -6]]


class FlightLab:
    def __init__(self, external_nav=False):
        self.external_nav = external_nav
        self.serial_port = 5790 if external_nav else 5780
        self.lock = threading.RLock()
        self.requests = queue.Queue()
        self.stop = threading.Event()
        self.events = deque(maxlen=60)
        self.trail = deque(maxlen=4000)
        self.samples = []
        self.process = None
        self.link = None
        self.state = dict(phase='BOOTING', mode='UNKNOWN', armed=False, connected=False,
                          position=[0, 0, 0], velocity=[0, 0, 0], attitude=[0, 0, 0],
                          motors=[0]*4, gps_fix=0, satellites=0, ekf_flags=0,
                          battery=None, waypoint=0, route=ROUTE, elapsed=0,
                          firmware='ArduCopter SITL', gps_enabled=True, error=None,
                          engine='Actual ArduPilot firmware + built-in multicopter physics',
                          localization='Simulated GPS + inertial sensors; no visual odometry',
                          observations=[])
        self.state.update(outcome=None, vicon_enabled=True)
        self.state['external_nav'] = external_nav
        if external_nav:
            self.state.update(gps_enabled=False, localization='GPS disabled; virtual Vicon from simulator truth, not image-based VIO')
        self.phase_started = time.monotonic()
        self.started = time.monotonic()
        self.last_heartbeat = 0
        self.last_position = 0
        self.last_target = 0
        self.last_arm = 0
        self.last_sample = 0
        self.mission_start = None
        threading.Thread(target=self.worker, daemon=True).start()

    def event(self, message):
        self.events.appendleft({'t': round(time.monotonic()-self.started, 1), 'message': message})

    def phase(self, phase):
        self.state['phase'] = phase
        self.phase_started = time.monotonic()
        self.event(phase.replace('_', ' ').capitalize())

    def snapshot(self):
        with self.lock:
            result = dict(self.state)
            result.update(events=list(self.events), trail=list(self.trail),
                          heartbeat_age=round(time.monotonic()-self.last_heartbeat, 2) if self.last_heartbeat else None)
            return result

    def command_long(self, command, *params):
        self.link.mav.command_long_send(self.link.target_system, self.link.target_component,
                                       command, 0, *(list(params)+[0]*(7-len(params))))

    def mode(self, name):
        self.link.set_mode(self.link.mode_mapping()[name])
        self.event('Requested flight mode: '+name)

    def target(self, point):
        self.link.mav.set_position_target_local_ned_send(0, self.link.target_system,
            self.link.target_component, mavutil.mavlink.MAV_FRAME_LOCAL_NED,
            0b110111111000, *point, 0, 0, 0, 0, 0, 0, 0, 0)

    def worker(self):
        runtime = ROOT / ('flight-runtime-vicon' if self.external_nav else 'flight-runtime')
        self.runtime = runtime
        runtime.mkdir(exist_ok=True)
        log = (runtime / 'sitl-console.log').open('w')
        try:
            binary = ROOT / 'vendor' / 'ardupilot-sitl' / 'arducopter.exe'
            defaults = ROOT / 'vendor' / 'ardupilot-sitl' / 'copter.parm'
            settings = defaults.read_text()
            if self.external_nav:
                settings += '\nEK3_SRC1_POSXY 6\nEK3_SRC1_POSZ 6\nEK3_SRC1_VELXY 6\nEK3_SRC1_VELZ 6\nEK3_SRC1_YAW 6\nVISO_TYPE 2\nSERIAL5_PROTOCOL 2\nGPS1_TYPE 0\nGPS2_TYPE 0\n'
            local_defaults = runtime/'flight.parm'
            local_defaults.write_text(settings)
            extra = ['--serial5', 'sim:vicon:'] if self.external_nav else []
            self.process = subprocess.Popen([str(binary), '--model', 'quad', '--speedup', '1',
                '--instance', '12' if self.external_nav else '11', '--serial0', f'tcp:{self.serial_port}',
                '--defaults', str(local_defaults), '--wipe']+extra,
                cwd=runtime, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            for attempt in range(30):
                if self.process.poll() is not None:
                    raise RuntimeError('SITL exited. See flight-runtime/sitl-console.log')
                try:
                    self.link = mavutil.mavlink_connection(f'tcp:127.0.0.1:{self.serial_port}', source_system=255)
                    break
                except OSError:
                    self.stop.wait(.5)
            if self.link is None or not self.link.wait_heartbeat(timeout=15):
                raise RuntimeError('No heartbeat from managed SITL')
            self.link.mav.request_data_stream_send(self.link.target_system, self.link.target_component, 0, 10, 1)
            self.command_long(mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE, 148)
            self.phase('CALIBRATING')
            self.event('Connected to the locally launched ArduCopter process. No physical vehicle endpoint.')
            last_gcs = 0
            while not self.stop.is_set():
                if self.process.poll() is not None:
                    raise RuntimeError('Managed SITL process stopped')
                with self.lock:
                    for _ in range(300):
                        message = self.link.recv_match(blocking=False)
                        if message is None:
                            break
                        self.receive(message)
                    now = time.monotonic()
                    if now-last_gcs > 1:
                        self.link.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_GCS,
                            mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)
                        # A neutral simulated RC transmitter keeps ordinary arming checks meaningful.
                        self.link.mav.rc_channels_override_send(self.link.target_system,
                            self.link.target_component, 1500, 1500, 1000, 1500, 1800, 1500, 1500, 1500)
                        last_gcs = now
                        if self.external_nav and self.state['phase']=='CALIBRATING':
                            # Known test-site origin only; no GPS measurement is supplied.
                            self.link.mav.set_gps_global_origin_send(self.link.target_system,
                                int(-35.363261*1e7),int(149.165230*1e7),584000)
                    while not self.requests.empty():
                        self.act(self.requests.get_nowait())
                    self.advance(now)
                self.stop.wait(.02)
        except Exception as exc:
            with self.lock:
                self.state['error'] = str(exc)
                self.phase('ERROR')
        finally:
            if self.link:
                self.link.close()
            if self.process and self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=10)
            log.close()

    def receive(self, m):
        kind = m.get_type()
        if kind == 'HEARTBEAT':
            if m.autopilot != mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA:
                return  # The virtual Vicon peripheral also emits a heartbeat.
            self.last_heartbeat = time.monotonic()
            self.state.update(connected=True, mode=mavutil.mode_string_v10(m),
                              armed=bool(m.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED))
        elif kind == 'LOCAL_POSITION_NED':
            self.last_position = time.monotonic()
            self.state.update(position=[m.x,m.y,m.z], velocity=[m.vx,m.vy,m.vz])
        elif kind == 'ATTITUDE':
            self.state['attitude'] = [m.roll,m.pitch,m.yaw]
        elif kind == 'SERVO_OUTPUT_RAW':
            self.state['motors'] = [m.servo1_raw,m.servo2_raw,m.servo3_raw,m.servo4_raw]
        elif kind == 'GPS_RAW_INT':
            self.state.update(gps_fix=m.fix_type, satellites=m.satellites_visible)
        elif kind == 'EKF_STATUS_REPORT':
            self.state['ekf_flags'] = m.flags
        elif kind == 'SYS_STATUS':
            self.state['battery'] = m.voltage_battery/1000
        elif kind == 'AUTOPILOT_VERSION':
            v=m.flight_sw_version
            self.state['firmware'] = f'ArduCopter {v>>24}.{(v>>16)&255}.{(v>>8)&255} (SITL)'
        elif kind == 'STATUSTEXT':
            self.event(str(m.text))
        elif kind == 'COMMAND_ACK':
            self.event(f'Autopilot command {m.command}: '+{0:'accepted',1:'temporarily rejected',2:'denied',3:'unsupported',4:'failed',5:'in progress'}.get(m.result,str(m.result)))
        elif kind == 'PARAM_VALUE' and m.param_id == 'SIM_GPS1_ENABLE':
            self.state['gps_enabled'] = bool(m.param_value)
            self.event('SITL GPS parameter confirmed: '+str(m.param_value))
        elif kind == 'PARAM_VALUE' and m.param_id == 'SIM_VICON_FAIL':
            self.state['vicon_enabled'] = not bool(m.param_value)
            self.event('SITL virtual-position failure parameter confirmed: '+str(m.param_value))

    def act(self, action):
        phase = self.state['phase']
        if action == 'start' and phase in ('READY','COMPLETE') and not self.state['armed']:
            if not self.external_nav and self.state['gps_fix']<3:
                self.event('Wait for a GPS fix before restarting');return
            self.state['waypoint']=0
            self.state['outcome']=None
            self.state['observations']=[]
            self.trail.clear()
            self.samples=[]
            self.mode('GUIDED')
            self.mission_start = time.monotonic()
            self.phase('ARMING')
        elif action == 'land' and self.state['armed']:
            self.state['outcome']='ABORTED'
            self.mode('LAND')
            self.phase('LANDING')
        elif action in ('vision_off','vision_on') and self.external_nav:
            self.link.mav.param_set_send(self.link.target_system,self.link.target_component,
                b'SIM_VICON_FAIL',1 if action=='vision_off' else 0,mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
            if action=='vision_off' and self.state['armed']:
                self.state['outcome']='ABORTED'
                self.mode('LAND');self.phase('LANDING')
                self.event('Required external position removed; supervisor aborts and requests LAND')
        elif action == 'gps_off':
            if self.external_nav:
                self.event('GPS is already disabled in external-navigation mode');return
            self.link.mav.param_set_send(self.link.target_system,self.link.target_component,
                                        b'SIM_GPS1_ENABLE',0,mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
            self.event('Requested simulated GPS failure; autonomous survey will abort and land.')
            if self.state['armed']:
                self.state['outcome']='ABORTED'
                self.mode('LAND')
                self.phase('LANDING')
        elif action == 'gps_on':
            if self.external_nav:
                self.event('GPS stays disabled in this test configuration');return
            self.link.mav.param_set_send(self.link.target_system,self.link.target_component,
                                        b'SIM_GPS1_ENABLE',1,mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
        else:
            self.event('Action ignored in current mission state: '+action)

    def advance(self, now):
        s=self.state
        if now-self.last_heartbeat>3:
            s['connected']=False
            if s['phase'] not in ('CALIBRATING','ERROR'):
                self.phase('LINK_LOST')
            return
        if self.mission_start and s['phase']!='COMPLETE':
            s['elapsed']=round(now-self.mission_start,1)
        if now-self.last_sample>.2:
            self.last_sample=now
            if self.last_position:
                self.trail.append(list(s['position']))
                self.samples.append(dict(t=round(now-self.started,2),phase=s['phase'],position=list(s['position']),
                                         velocity=list(s['velocity']),attitude=list(s['attitude']),motors=list(s['motors'])))
                self.samples=self.samples[-10000:]
        phase=s['phase']
        if phase=='CALIBRATING' and (self.external_nav or s['gps_fix']>=3) and s['ekf_flags']&16 and now-self.phase_started>12 and self.last_position:
            self.phase('READY')
        elif phase=='ARMING':
            if s['armed'] and s['mode']=='GUIDED':
                self.command_long(mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,0,0,0,0,0,0,6)
                self.phase('TAKEOFF')
            elif now-self.last_arm>3:
                self.command_long(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,1)
                self.last_arm=now
            if now-self.phase_started>45:
                self.phase('ARMING_FAILED')
        elif phase in ('TAKEOFF','SURVEY','RETURN'):
            if now-self.last_position>2:
                s['outcome']='ABORTED'
                self.mode('LAND');self.phase('LANDING');self.event('Aborted: local position telemetry stale');return
            if s['mode']!='GUIDED':
                self.phase('AUTOPILOT_OVERRIDE');self.event('Flight controller changed mode; survey setpoints stopped');return
            if now-self.phase_started>90:
                s['outcome']='ABORTED'
                self.mode('LAND');self.phase('LANDING');self.event('Aborted: waypoint timeout');return
            if phase=='TAKEOFF':
                if -s['position'][2]>5.5:
                    s['waypoint']=1;self.phase('SURVEY')
                return
            goal=ROUTE[s['waypoint']]
            if now-self.last_target>.2:
                self.target(goal);self.last_target=now
            distance=math.dist(s['position'],goal)
            if distance<1 and math.hypot(*s['velocity'])<.8:
                self.event(f'Waypoint {s["waypoint"]} reached using autopilot telemetry')
                if s['waypoint']==len(ROUTE)-1:
                    self.mode('LAND');self.phase('LANDING')
                else:
                    s['observations'].append(dict(waypoint=s['waypoint'],position=list(s['position']),
                        label='Simulated sampling stop; no thermal data connected'))
                    s['waypoint']+=1
                    self.phase('RETURN' if s['waypoint']==len(ROUTE)-1 else 'SURVEY')
        elif phase=='LANDING' and not s['armed']:
            if s['outcome'] is None:
                s['outcome']='SURVEY_COMPLETED' if s['waypoint']==len(ROUTE)-1 else 'ABORTED'
            self.phase('COMPLETE')
            (self.runtime/'last-flight.json').write_text(json.dumps(self.recording(),indent=2))

    def recording(self):
        return {'schema':'eagle-ardupilot-sitl/v1','state':self.snapshot(),'telemetry':self.samples,
                'limits':[self.state['localization'],'No image-based VIO or obstacle avoidance','No physical hardware connected']}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--external-nav',action='store_true');args=parser.parse_args()
    lab=FlightLab(args.external_nav)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def send(self,data,mime='application/json',code=200):
            body=data if isinstance(data,bytes) else json.dumps(data,allow_nan=False).encode()
            self.send_response(code);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
        def do_GET(self):
            if self.path=='/api/state': return self.send(lab.snapshot())
            if self.path=='/api/export': return self.send(lab.recording())
            paths={'/':('flight.html','text/html'),'/flight.js':('flight.js','text/javascript'),'/tokens.css':('tokens.css','text/css')}
            if self.path not in paths:return self.send({'error':'Not found'},code=404)
            file,mime=paths[self.path];self.send((ROOT/'eagle_nav'/'web'/file).read_bytes(),mime+'; charset=utf-8')
        def do_POST(self):
            if self.headers.get('Origin') not in (None,f'http://127.0.0.1:{args.port}',f'http://localhost:{args.port}'):
                return self.send({'error':'Local origin required'},code=403)
            try:
                size=int(self.headers.get('Content-Length',0))
                if not 0<size<1024:raise ValueError('Invalid request size')
                data=json.loads(self.rfile.read(size));action=data.get('action')
                if self.path!='/api/command' or action not in ('start','land','gps_off','gps_on','vision_off','vision_on'):raise ValueError('Unknown command')
                lab.requests.put(action);self.send({'queued':action})
            except (ValueError,TypeError,AttributeError) as exc:self.send({'error':str(exc)},code=400)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'Eagle actual flight-controller simulation: http://127.0.0.1:{args.port}',flush=True)
    try:server.serve_forever()
    finally:lab.stop.set();server.server_close()

if __name__=='__main__':main()
