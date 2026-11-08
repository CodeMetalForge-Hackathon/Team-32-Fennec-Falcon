"""Download official Windows SITL dependencies; verify the recorded binary hashes."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parent


def download(url,path,expected=None):
    if path.exists() and expected and hashlib.sha256(path.read_bytes()).hexdigest()==expected:
        return
    with urlopen(url,timeout=60) as response:
        data=response.read()
    if expected and hashlib.sha256(data).hexdigest()!=expected:
        raise RuntimeError(f'{path.name}: upstream binary has changed. Review and update sitl-manifest.json explicitly.')
    path.write_bytes(data)


if __name__=='__main__':
    if sys.platform!='win32':raise SystemExit('This bootstrap is for Windows x64. Use upstream ArduPilot SITL setup on Linux.')
    manifest=json.loads((ROOT/'sitl-manifest.json').read_text(encoding='utf-8-sig'))
    vendor=ROOT/'vendor'/'ardupilot-sitl';vendor.mkdir(parents=True,exist_ok=True)
    for item in manifest['files']:
        download(manifest['base_url']+item['name'],vendor/item['name'],item['sha256'])
    shutil.copyfile(vendor/'ArduCopter.elf',vendor/'arducopter.exe')
    base='https://raw.githubusercontent.com/ArduPilot/ardupilot/'+manifest['reported_commit']+'/'
    download(base+'Tools/autotest/default_params/copter.parm',vendor/'copter.parm')
    download(base+'COPYING.txt',vendor/'COPYING.txt')
    subprocess.run([sys.executable,'-m','pip','install','--target',str(ROOT/'vendor'/'python'),
                    'pymavlink==2.4.50','lxml==6.1.3','fastcrc==0.5.0'],check=True)
    print('Ready: python flight_lab.py --external-nav --port 8767')
