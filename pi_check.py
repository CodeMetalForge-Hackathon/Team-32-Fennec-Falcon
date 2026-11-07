"""Find the Raspberry Pi from this laptop without scanning the network. Writes pi_check_log.txt."""
import socket
import subprocess
from pathlib import Path

LOG = Path(__file__).with_name("pi_check_log.txt")
out = []
PI_MACS = ("b8-27-eb", "dc-a6-32", "e4-5f-01", "28-cd-c1", "d8-3a-dd", "2c-cf-67")


def log(s):
    print(s, flush=True)
    out.append(s)


def port22(ip):
    try:
        with socket.create_connection((ip, 22), timeout=2):
            return True
    except OSError:
        return False


log("== hostname lookup")
for name in ("raspberrypi.local", "raspberrypi", "pi.local", "thermal.local", "drone.local"):
    try:
        ips = sorted({a[4][0] for a in socket.getaddrinfo(name, 22, socket.AF_INET)})
        for ip in ips:
            log(f"  {name} -> {ip}  ssh port open: {port22(ip)}")
    except OSError:
        log(f"  {name}: not found")

log("== Raspberry Pi devices in this laptop's ARP cache (no scan)")
arp = subprocess.run(["arp", "-a"], capture_output=True, text=True).stdout
hits = [l.strip() for l in arp.splitlines() if any(m in l.lower() for m in PI_MACS)]
for h in hits:
    ip = h.split()[0]
    log(f"  {h}  ssh port open: {port22(ip)}")
if not hits:
    log("  none")

log("== laptop network adapters")
r = subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' } | "
                    "ForEach-Object { \"$($_.InterfaceAlias): $($_.IPAddress)/$($_.PrefixLength)\" }"],
                   capture_output=True, text=True)
for line in r.stdout.splitlines():
    log("  " + line)

log("== USB devices from Raspberry Pi (gadget mode)")
r = subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_2E8A|VID_0525|VID_1D6B' -or "
                    "$_.FriendlyName -match 'RNDIS|Raspberry|Gadget' } | ForEach-Object { \"$($_.FriendlyName) | $($_.InstanceId)\" }"],
                   capture_output=True, text=True)
log("  " + (r.stdout.strip() or "none"))
log("done")
LOG.write_text("\n".join(out), encoding="utf-8")
