import json
import logging
import os
from pathlib import Path
import re
import subprocess
import time

STATE_PATH = Path('/run/netpulse-watchdog.json')
INTERVAL_FAILURES = 3
COOLDOWN = 300


def run(arguments, timeout=15):
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 1, ''


def decide(present, connected, routed, failures, last_attempt, now):
    if not present:
        return 0, 'missing'
    if connected and routed:
        return 0, 'healthy'
    failures += 1
    if failures < INTERVAL_FAILURES or now - last_attempt < COOLDOWN:
        return failures, 'wait'
    return failures, 'reapply' if connected else 'reconnect'


def resolve_uplink(mac, fallback, root=Path('/sys/class/net')):
    if mac:
        for device in root.iterdir():
            try:
                if (device / 'address').read_text().strip().lower() == mac.lower():
                    return device.name
            except OSError:
                continue
    return fallback


def check():
    uplink = os.environ.get('NETPULSE_UPLINK', '')
    uplink = resolve_uplink(os.environ.get('NETPULSE_UPLINK_MAC', ''), uplink)
    if not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,15}', uplink) or uplink in ('eth0', 'wlan0', 'lo'):
        raise ValueError('NETPULSE_UPLINK 必须指定实际 USB 上游接口，不能是下游 eth0/wlan0')
    try:
        previous = json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        previous = {}
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if previous.get('boot_id') != boot_id:
        previous = {}
    previous['boot_id'] = boot_id
    now = time.monotonic()
    count_path = Path('/proc/sys/net/netfilter/nf_conntrack_count')
    limit_path = Path('/proc/sys/net/netfilter/nf_conntrack_max')
    if count_path.exists() and limit_path.exists():
        count = int(count_path.read_text())
        limit = int(limit_path.read_text())
        previous['conntrack_count'] = count
        previous['conntrack_max'] = limit
        if count >= limit * 0.85 and limit < 32768:
            code, _ = run(['sysctl', '-w', 'net.netfilter.nf_conntrack_max=32768'])
            logging.warning('Connection table near capacity: %s/%s, expansion success=%s', count, limit, code == 0)
        elif count >= limit * 0.9:
            logging.warning('Connection table pressure: %s/%s; no unbounded expansion', count, limit)
    for service in ('netpulse', 'netpulse-ap', 'dnsmasq'):
        if run(['systemctl', 'is-enabled', service])[0] == 0 and run(['systemctl', 'is-active', service])[0] != 0:
            if now - previous.get('service_attempts', {}).get(service, -COOLDOWN) >= COOLDOWN:
                previous.setdefault('service_attempts', {})[service] = now
                logging.warning('Starting enabled but inactive service: %s', service)
                run(['systemctl', 'start', service], timeout=25)
    present = Path('/sys/class/net', uplink).exists()
    state_code, state = run(['nmcli', '-g', 'GENERAL.STATE', 'device', 'show', uplink])
    connected = state_code == 0 and state.split(' ', 1)[0] == '100'
    route_code, route_output = run(['ip', '-j', '-4', 'route', 'show', 'default', 'dev', uplink])
    routes = json.loads(route_output or '[]') if route_code == 0 else []
    gateway_failed = False
    if connected and routes and routes[0].get('gateway'):
        gateway = routes[0]['gateway']
        run(['ping', '-I', uplink, '-c', '2', '-W', '1', gateway], timeout=5)
        code, neighbors = run(['ip', '-j', '-4', 'neigh', 'show', 'to', gateway, 'dev', uplink])
        if code == 0:
            gateway_failed = any(set(item.get('state', [])) & {'FAILED', 'INCOMPLETE'} for item in json.loads(neighbors or '[]'))
    previous['gateway_neighbor_failed'] = gateway_failed
    failures, action = decide(present, connected and not gateway_failed, bool(routes), previous.get('failures', 0), previous.get('last_attempt', -COOLDOWN), now)
    previous.update(failures=failures, action=action, interface=uplink, checked_at=time.time())
    if action in ('reconnect', 'reapply'):
        previous['last_attempt'] = now
        if action == 'reapply':
            command = ['nmcli', 'device', 'reapply', uplink]
        else:
            profile = os.environ.get('NETPULSE_UPLINK_UUID', '')
            command = ['nmcli', '--wait', '20', 'connection', 'up', 'uuid', profile, 'ifname', uplink] if profile else ['nmcli', '--wait', '20', 'device', 'connect', uplink]
        code, _ = run(command, timeout=25)
        previous['recovery_success'] = code == 0
        logging.warning('Upstream recovery %s: success=%s', action, code == 0)
    if present or routes:
        code, _ = run(['/usr/local/sbin/netpulse-router'])
        previous['router_rules_ok'] = code == 0
    if action == 'healthy':
        reachable = any(run(['curl', '-4', '--interface', uplink, '-I', '-sS', '--connect-timeout', '3', '--max-time', '5', url], timeout=7)[0] == 0
                        for url in ('https://www.baidu.com', 'https://www.qq.com'))
        previous['internet_probe_ok'] = reachable
        if not reachable:
            logging.warning('Upstream associated and routed, but Internet/DNS probes failed; preserving local access')
    else:
        previous['internet_probe_ok'] = None
    temporary = STATE_PATH.with_suffix('.tmp')
    temporary.write_text(json.dumps(previous, ensure_ascii=False))
    temporary.replace(STATE_PATH)
    logging.info('Network health: %s, consecutive failures=%s', action, failures)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    check()
