"""Read-only diagnostics for the deployed NetPulse dashboard."""
import json
import time
from pathlib import Path
import subprocess
from flask import Blueprint, jsonify

network_health = Blueprint('network_health', __name__)

def read(path, default=''):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return default

def run(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=4).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ''

@network_health.get('/api/network/health')
def health():
    routes = json.loads(run(['ip', '-j', '-4', 'route', 'show', 'default']) or '[]')
    route = min(routes, key=lambda r: r.get('metric', 0), default={})
    uplink = route.get('dev', '')
    link = run(['iw', 'dev', uplink, 'link']) if uplink else ''
    details = {}
    for line in link.splitlines():
        if ':' in line:
            k, v = line.strip().split(':', 1)
            if k in ('SSID', 'freq', 'signal', 'rx bitrate', 'tx bitrate'):
                details[k] = v.strip()
    try:
        recovery = json.loads(read('/run/netpulse-watchdog.json', '{}'))
    except ValueError:
        recovery = {}
    age = max(0, time.time() - recovery.get('checked_at', 0))
    return jsonify(recovery_age_seconds=age, uplink=uplink, gateway=route.get('gateway', ''), wifi=details,
                   ethernet={'speed': read('/sys/class/net/eth0/speed'),
                             'duplex': read('/sys/class/net/eth0/duplex'),
                             'carrier': read('/sys/class/net/eth0/carrier') == '1'},
                   recovery={k: recovery.get(k) for k in
                             ('action', 'checked_at', 'internet_probe_ok', 'recovery_success')})
