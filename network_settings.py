import ipaddress
import json
import logging
import os
from pathlib import Path
import subprocess
import threading
from flask import Blueprint, jsonify, request

network_settings = Blueprint('network_settings', __name__)
AP_CONFIG = Path('/etc/hostapd/netpulse.conf')
change_lock = threading.Lock()
apply_state = {'state': 'idle', 'message': ''}


def run(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=15)


def config_values():
    return dict(line.split('=', 1) for line in AP_CONFIG.read_text().splitlines()
                if '=' in line and not line.startswith('#'))


@network_settings.get('/api/network/overview')
def overview():
    config = config_values()
    interfaces = json.loads(run(['ip', '-j', '-4', 'addr', 'show']).stdout or '[]')
    routes = json.loads(run(['ip', '-j', '-4', 'route', 'show', 'default']).stdout or '[]')
    clients = []
    leases = Path('/var/lib/misc/dnsmasq.leases')
    if leases.exists():
        for line in leases.read_text().splitlines():
            parts = line.split()
            if len(parts) >= 4 and ipaddress.ip_address(parts[2]) in ipaddress.ip_network('192.168.112.0/24'):
                clients.append({'ip': parts[2], 'name': parts[3], 'mac': parts[1]})
    return jsonify(interfaces=interfaces, routes=routes, leases=clients,
                   hotspot={'ssid': config.get('ssid', ''), 'channel': config.get('channel', '6'),
                            'active': run(['systemctl', 'is-active', 'netpulse-ap']).returncode == 0,
                            'security': 'WPA2-PSK', 'address': '192.168.112.1'},
                   apply=dict(apply_state))


def write_config(text):
    temporary = AP_CONFIG.with_suffix('.pending')
    temporary.write_text(text)
    temporary.chmod(0o600)
    os.replace(temporary, AP_CONFIG)


def apply_hotspot(updated, original):
    try:
        write_config(updated)
        result = run(['systemctl', 'restart', 'netpulse-ap'])
        status = run(['hostapd_cli', '-i', 'wlan0', 'status'])
        if result.returncode or 'state=ENABLED' not in status.stdout:
            raise RuntimeError('热点启动检查失败')
        apply_state.update(state='success', message='热点设置已生效，请使用新名称和密码重新连接。')
    except Exception:
        logging.exception('Hotspot configuration failed')
        write_config(original)
        run(['systemctl', 'restart', 'netpulse-ap'])
        apply_state.update(state='error', message='应用失败，已恢复原来的热点配置。')
    finally:
        change_lock.release()


@network_settings.post('/api/network/hotspot')
def save_hotspot():
    if request.headers.get('Origin') and request.headers['Origin'] != request.host_url.rstrip('/'):
        return jsonify(error='请求来源不匹配'), 403
    data = request.get_json(silent=True) or {}
    ssid = data.get('ssid', '')
    password = data.get('password', '')
    channel = str(data.get('channel', '6'))
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode('utf-8')) <= 32 or any(ord(char) < 32 for char in ssid):
        return jsonify(error='热点名称需为 1–32 字节，不能包含换行或控制字符'), 400
    if not isinstance(password, str) or password and (not 8 <= len(password) <= 63 or any(ord(char) < 32 or ord(char) > 126 for char in password)):
        return jsonify(error='密码需为 8–63 位英文、数字或符号'), 400
    if channel not in ('1', '6', '11'):
        return jsonify(error='请选择信道 1、6 或 11'), 400
    if not change_lock.acquire(blocking=False):
        return jsonify(error='正在应用设置，请稍后再试'), 409
    try:
        original = AP_CONFIG.read_text()
        replacements = {'ssid': ssid, 'channel': channel}
        if password:
            replacements['wpa_passphrase'] = password
        lines = []
        for line in original.splitlines():
            key = line.split('=', 1)[0]
            lines.append(key + '=' + replacements[key] if key in replacements else line)
        updated = '\n'.join(lines) + '\n'
        apply_state.update(state='pending', message='正在应用热点设置…')
        timer = threading.Timer(2, apply_hotspot, args=(updated, original))
        timer.daemon = True
        timer.start()
    except Exception:
        change_lock.release()
        raise
    return jsonify(success=True, message='设置已提交，热点将在 2 秒后重启。')
