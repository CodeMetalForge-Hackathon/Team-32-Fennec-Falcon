"""End-to-end checks against the two managed local SITL demo servers."""
import json
import time
from urllib.request import urlopen, Request
from pathlib import Path


def get(port,path='/api/state'):
    with urlopen(f'http://127.0.0.1:{port}{path}',timeout=4) as r:
        return json.load(r)


def command(port,action):
    req=Request(f'http://127.0.0.1:{port}/api/command',data=json.dumps({'action':action}).encode(),
                headers={'Content-Type':'application/json'})
    with urlopen(req,timeout=4) as r:
        return json.load(r)


if __name__=='__main__':
    pending={8766:False,8767:False}; results={}; began=time.monotonic()
    while pending and time.monotonic()-began<180:
        for port in list(pending):
            s=get(port)
            if not pending[port] and s['phase']=='READY':
                command(port,'start');pending[port]=True
                print(port,'started',flush=True)
            if pending[port] and s['phase']=='COMPLETE':
                assert not s['armed'],s
                assert s['outcome']=='SURVEY_COMPLETED',s
                assert s['waypoint']==6,s
                if port==8767:assert s['gps_fix']==0,s
                record=get(port,'/api/export')
                assert max(-p['position'][2] for p in record['telemetry'])>5.5
                assert any(max(p['motors'])>1200 for p in record['telemetry'])
                out=Path(__file__).parent/'artifacts';out.mkdir(exist_ok=True)
                (out/f'verified-flight-{port}.json').write_text(json.dumps(record,indent=2))
                results[port]={k:s[k] for k in ('firmware','phase','outcome','armed','waypoint','elapsed','gps_fix','external_nav')}
                print(json.dumps(results[port]),flush=True)
                del pending[port]
            elif s['phase'] in ('ERROR','ARMING_FAILED','AUTOPILOT_OVERRIDE','LINK_LOST'):
                raise RuntimeError(json.dumps(s))
        time.sleep(.5)
    assert not pending,'Mission deadline exceeded: '+str(pending)
    (Path(__file__).parent/'artifacts'/'flight-verification.json').write_text(json.dumps(results,indent=2))
    print('Both actual flight-controller missions passed.',flush=True)
