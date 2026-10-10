#!/usr/bin/env python3
"""Spread a single receive queue on a USB uplink across online CPU cores."""
import json
import re
from pathlib import Path

EXCLUDED = {'eth0', 'wlan0', 'lo', 'tailscale0'}


def online_mask(spec):
    cores = set()
    for entry in spec.strip().split(','):
        bounds = entry.split('-')
        first, last = int(bounds[0]), int(bounds[-1])
        if not 0 <= first <= last < 4096:
            raise ValueError('Invalid online CPU range')
        cores.update(range(first, last + 1))
    mask = sum(1 << core for core in cores)
    digits = format(mask, 'x')
    chunks = []
    while digits:
        chunks.insert(0, digits[-8:])
        digits = digits[:-8]
    return ','.join(chunks)


def default_interface(text):
    routes = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 8:
            continue
        iface = fields[0]
        if (fields[1] == '00000000' and int(fields[3], 16) & 1
                and iface not in EXCLUDED and re.fullmatch(r'[a-zA-Z0-9_.:-]{1,15}', iface)):
            routes.append((int(fields[6]), iface))
    return min(routes)[1] if routes else ''


def apply(net_root=Path('/sys/class/net'), route_file=Path('/proc/net/route'),
          cpu_file=Path('/sys/devices/system/cpu/online')):
    iface = default_interface(route_file.read_text())
    if not iface:
        return {'applied': False, 'reason': 'no_upstream'}
    device = net_root / iface
    # Leave Ethernet, tunnel devices, and hardware multi-queue RSS unchanged.
    if not any(re.fullmatch(r'usb\d+', part) for part in (device / 'device').resolve().parts):
        return {'applied': False, 'interface': iface, 'reason': 'not_usb'}
    queues = list((device / 'queues').glob('rx-*'))
    if len(queues) != 1:
        return {'applied': False, 'interface': iface, 'reason': 'multiple_queues'}
    mask = online_mask(cpu_file.read_text())
    if int(mask.replace(',', ''), 16).bit_count() < 2:
        return {'applied': False, 'interface': iface, 'reason': 'single_core'}
    target = queues[0] / 'rps_cpus'
    if int(target.read_text().strip().replace(',', ''), 16) != int(mask.replace(',', ''), 16):
        target.write_text(mask + '\n')
    return {'applied': True, 'interface': iface, 'rps_cpus': mask}


if __name__ == '__main__':
    print(json.dumps(apply()))
