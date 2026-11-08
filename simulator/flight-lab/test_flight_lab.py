import unittest
import threading
from collections import deque
from types import SimpleNamespace
from flight_lab import FlightLab, mavutil


class HeartbeatTests(unittest.TestCase):
    def test_peripheral_cannot_change_aircraft_state(self):
        lab=object.__new__(FlightLab)
        lab.state={'armed':True,'mode':'GUIDED'}
        lab.last_heartbeat=0
        peripheral=SimpleNamespace(get_type=lambda:'HEARTBEAT',autopilot=mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                                   base_mode=0)
        lab.receive(peripheral)
        self.assertEqual(lab.state,{'armed':True,'mode':'GUIDED'})
        self.assertEqual(lab.last_heartbeat,0)

    def test_telemetry_ned_preserved(self):
        lab=object.__new__(FlightLab);lab.state={}
        msg=SimpleNamespace(get_type=lambda:'LOCAL_POSITION_NED',x=12,y=-4,z=-6,vx=1,vy=2,vz=-.5)
        lab.receive(msg)
        self.assertEqual(lab.state['position'],[12,-4,-6])
        self.assertEqual(lab.state['velocity'],[1,2,-.5])


if __name__=='__main__':unittest.main()
