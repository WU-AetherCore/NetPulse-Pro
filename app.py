"""
NetPulse - 网络设备管理系统
Flask主程序
"""
import os
import sys
import time
import json
import threading
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, send_from_directory

# 确保能导入本地模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import WEB_HOST, WEB_PORT, ADMIN_PASSWORD
from wifi_manager import status as wifi_status, scan as wifi_scan, connect as wifi_connect
from uplink_manager import status as uplink_status, set_uplink
from database import (
    add_speedtest_result, get_speedtest_history, clear_speedtest_history,
    init_db, get_all_devices, get_device_by_mac,
    get_hourly_traffic, get_daily_traffic, get_connection_events,
    update_device_name, get_summary, cleanup_old_data,
    get_period_summary, get_period_settings, set_period_settings, check_and_reset_period, update_period_traffic,
    set_device_blocked, set_device_limit, get_managed_devices,
    set_device_wifi_band, get_band_stats, get_traffic_ranking, get_db
)
from scanner import Scanner
from traffic import TrafficMonitor
from device_manager import (
    block_device, unblock_device, limit_device, unlimit_device,
    init_block_chain, init_tc, get_blocked_devices, get_limited_devices,
    start_global_spoof, stop_global_spoof, get_global_spoof_status,
    ensure_global_spoof_running, ensure_nat_and_forwarding,
    start_ipv6_spoof, stop_ipv6_spoof, get_ipv6_spoof_status,
    ensure_ipv6_spoof_running, full_health_check,
    set_device_priority, remove_device_priority, init_qos, get_qos_status,
    init_device_manager, cleanup_device_manager
)

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
from network_settings import network_settings
app.register_blueprint(network_settings)
from network_health import network_health
app.register_blueprint(network_health)
from integrations import integrations
app.register_blueprint(integrations)


@app.route('/api/wifi/status')
def api_wifi_status():
    return jsonify(wifi_status())


@app.route('/api/uplink/status')
def api_uplink_status():
    return jsonify(uplink_status())


@app.route('/api/uplink/set', methods=['POST'])
def api_uplink_set():
    data = request.get_json(silent=True) or {}
    return jsonify(set_uplink(data.get('uplink', '')))


@app.route('/api/wifi/scan')
def api_wifi_scan():
    return jsonify(wifi_scan(request.args.get('interface')))


@app.route('/api/wifi/connect', methods=['POST'])
def api_wifi_connect():
    data = request.get_json(silent=True) or {}
    return jsonify(wifi_connect(data.get('ssid', '').strip(), data.get('password', ''), data.get('interface')))

# 全局变量
scanner = None
traffic_monitor = None
# 接口级别的全局速率（直接从eth0读取，更准确）
global_interface_rates = {
    'upload_kbps': 0.0,
    'download_kbps': 0.0,
    'upload_bytes': 0,
    'download_bytes': 0,
    'last_update': 0
}



def update_device_priority(mac, priority):
    """更新设备优先级"""
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE devices SET priority = ? WHERE mac = ?", (priority, mac))
    conn.commit()
    conn.close()

def format_bytes(bytes_val):
    """格式化字节数"""
    if bytes_val is None:
        return "0 B"
    bytes_val = float(bytes_val)
    if bytes_val < 1024:
        return f"{bytes_val:.0f} B"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val/1024:.1f} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val/(1024*1024):.1f} MB"
    else:
        return f"{bytes_val/(1024*1024*1024):.2f} GB"


def format_rate(rate_kbps):
    """格式化速率"""
    if rate_kbps is None:
        return "0 KB/s"
    if rate_kbps < 1024:
        return f"{rate_kbps:.1f} KB/s"
    else:
        return f"{rate_kbps/1024:.2f} MB/s"


@app.route('/')
def index():
    """主页"""
    return render_template('index.html')


@app.route('/api/summary')
def api_summary():
    """系统概览"""
    summary = get_summary()
    # 使用接口级别的真实总速率（直接从eth0读取，比设备速率之和更准确）
    if global_interface_rates['last_update'] > 0:
        summary['avg_upload_rate'] = global_interface_rates['upload_kbps']
        summary['avg_download_rate'] = global_interface_rates['download_kbps']
    summary['formatted'] = {
        'total_upload': format_bytes(summary['total_upload']),
        'total_download': format_bytes(summary['total_download']),
        'avg_upload_rate': format_rate(summary['avg_upload_rate']),
        'avg_download_rate': format_rate(summary['avg_download_rate']),
    }
    summary['timestamp'] = int(time.time())
    return jsonify(summary)


@app.route('/api/period-summary')
def api_period_summary():
    """获取周期流量统计概览"""
    # 检查是否需要进入新周期
    check_and_reset_period()
    # 更新当前周期流量
    update_period_traffic()
    period_data = get_period_summary()

    # 格式化数据
    for p in period_data['history']:
        p['total_upload_str'] = format_bytes(p.get('total_upload', 0))
        p['total_download_str'] = format_bytes(p.get('total_download', 0))
        p['total_str'] = format_bytes(p.get('total_upload', 0) + p.get('total_download', 0))
        from datetime import datetime
        if p.get('start_time'):
            p['start_time_str'] = datetime.fromtimestamp(p['start_time']).strftime('%Y-%m-%d')
        if p.get('end_time'):
            p['end_time_str'] = datetime.fromtimestamp(p['end_time']).strftime('%Y-%m-%d')

    period_data['current']['total_upload_str'] = format_bytes(period_data['current']['total_upload'])
    period_data['current']['total_download_str'] = format_bytes(period_data['current']['total_download'])
    period_data['current']['total_str'] = format_bytes(period_data['current']['total'])
    from datetime import datetime
    period_data['current']['start_time_str'] = datetime.fromtimestamp(period_data['current']['start_time']).strftime('%Y-%m-%d')

    return jsonify(period_data)


@app.route('/api/period-settings', methods=['GET', 'POST'])
def api_period_settings():
    """获取或设置周期统计类型"""
    if request.method == 'POST':
        data = request.get_json() or {}
        period_type = data.get('period_type', 'monthly')
        custom_days = int(data.get('custom_days', 30))
        auto_reset = data.get('auto_reset', True)
        settings = set_period_settings(period_type, custom_days, auto_reset)
        return jsonify({'success': True, 'settings': settings})
    else:
        return jsonify(get_period_settings())


@app.route('/api/period-reset', methods=['POST'])
def api_period_reset():
    """手动重置当前周期（清零）"""
    settings = get_period_settings()
    result = set_period_settings(settings['period_type'], settings['custom_days'])
    return jsonify({'success': True, 'message': '周期已重置，流量已清零', 'current': result})



@app.route('/api/devices')
def api_devices():
    """获取所有设备列表"""
    devices = get_all_devices()
    for dev in devices:
        dev['total_upload_str'] = format_bytes(dev.get('total_upload', 0))
        dev['total_download_str'] = format_bytes(dev.get('total_download', 0))
        dev['upload_rate_str'] = format_rate(dev.get('current_upload_rate', 0))
        dev['download_rate_str'] = format_rate(dev.get('current_download_rate', 0))
        dev['last_seen_str'] = datetime.fromtimestamp(dev['last_seen']).strftime('%Y-%m-%d %H:%M:%S') if dev.get('last_seen') else '未知'
        dev['first_seen_str'] = datetime.fromtimestamp(dev['first_seen']).strftime('%Y-%m-%d %H:%M:%S') if dev.get('first_seen') else '未知'
        # 在线时长
        if dev.get('is_online') and dev.get('last_seen'):
            online_seconds = int(time.time()) - dev['last_seen']
            dev['online_duration'] = f"{online_seconds//3600}小时{(online_seconds%3600)//60}分"
        else:
            dev['online_duration'] = '离线'
    return jsonify(devices)


@app.route('/api/device/<mac>')
def api_device_detail(mac):
    """获取设备详情"""
    device = get_device_by_mac(mac)
    if not device:
        return jsonify({"error": "设备不存在"}), 404

    device['total_upload_str'] = format_bytes(device.get('total_upload', 0))
    device['total_download_str'] = format_bytes(device.get('total_download', 0))
    device['upload_rate_str'] = format_rate(device.get('current_upload_rate', 0))
    device['download_rate_str'] = format_rate(device.get('current_download_rate', 0))

    # 获取流量数据
    hourly = get_hourly_traffic(mac, 24)
    daily = get_daily_traffic(mac, 30)

    # 获取连接事件
    events = get_connection_events(limit=50, mac=mac)
    for evt in events:
        evt['time_str'] = datetime.fromtimestamp(evt['timestamp']).strftime('%Y-%m-%d %H:%M:%S')

    return jsonify({
        "device": device,
        "hourly_traffic": hourly,
        "daily_traffic": daily,
        "events": events
    })


@app.route('/api/device/<mac>/rename', methods=['POST'])
def api_rename_device(mac):
    """重命名设备"""
    data = request.get_json()
    name = data.get('name', '').strip()
    if not name:
        return jsonify({"error": "名称不能为空"}), 400
    update_device_name(mac, name)
    return jsonify({"success": True})


@app.route('/api/traffic/hourly/<mac>')
def api_hourly_traffic(mac):
    """获取小时流量"""
    hours = request.args.get('hours', 24, type=int)
    data = get_hourly_traffic(mac, hours)
    return jsonify(data)


@app.route('/api/traffic/daily/<mac>')
def api_daily_traffic(mac):
    """获取天流量"""
    days = request.args.get('days', 30, type=int)
    data = get_daily_traffic(mac, days)
    return jsonify(data)


@app.route('/api/events')
def api_events():
    """获取连接事件"""
    limit = request.args.get('limit', 100, type=int)
    mac = request.args.get('mac', None)
    events = get_connection_events(limit=limit, mac=mac)
    for evt in events:
        evt['time_str'] = datetime.fromtimestamp(evt['timestamp']).strftime('%Y-%m-%d %H:%M:%S')
    return jsonify(events)


@app.route('/api/scan', methods=['POST'])
def api_scan_now():
    """立即扫描"""
    if scanner:
        from scanner import scan_devices
        devices = scan_devices()
        return jsonify({"success": True, "devices_found": len(devices)})
    return jsonify({"error": "扫描器未启动"}), 500


@app.route('/api/cleanup', methods=['POST'])
def api_cleanup():
    """清理过期数据"""
    cleanup_old_data()
    return jsonify({"success": True})


# ============================================================
# 设备管理 API
# ============================================================

@app.route('/api/device/<mac>/block', methods=['POST'])
def api_block_device(mac):
    """封禁设备（踢出网络）"""
    device = get_device_by_mac(mac)
    if not device:
        return jsonify({"error": "设备不存在"}), 404

    ip = device.get('ip', '')
    if not ip:
        return jsonify({"error": "设备IP未知，无法封禁"}), 400

    try:
        block_device(ip, mac)
        set_device_blocked(mac, True)
        # 记录事件
        from database import record_connection_event
        record_connection_event(mac, ip, 'blocked', '设备被管理员封禁，已踢出网络')
        return jsonify({"success": True, "message": f"设备 {ip} 已被封禁"})
    except Exception as e:
        return jsonify({"error": f"封禁失败: {str(e)}"}), 500


@app.route('/api/device/<mac>/unblock', methods=['POST'])
def api_unblock_device(mac):
    """解除设备封禁"""
    device = get_device_by_mac(mac)
    if not device:
        return jsonify({"error": "设备不存在"}), 404

    ip = device.get('ip', '')
    try:
        unblock_device(ip, mac)
        set_device_blocked(mac, False)
        from database import record_connection_event
        record_connection_event(mac, ip, 'unblocked', '设备封禁已解除')
        return jsonify({"success": True, "message": f"设备 {ip} 已解除封禁"})
    except Exception as e:
        return jsonify({"error": f"解除封禁失败: {str(e)}"}), 500


@app.route('/api/device/<mac>/limit', methods=['POST'])
def api_limit_device(mac):
    """限速设备"""
    device = get_device_by_mac(mac)
    if not device:
        return jsonify({"error": "设备不存在"}), 404

    data = request.get_json()
    upload_kbps = data.get('upload_kbps', 0)
    download_kbps = data.get('download_kbps', 0)

    if upload_kbps <= 0 and download_kbps <= 0:
        return jsonify({"error": "限速值必须大于0"}), 400

    ip = device.get('ip', '')
    if not ip:
        return jsonify({"error": "设备IP未知，无法限速"}), 400

    try:
        limit_device(ip, mac, upload_kbps, download_kbps)
        set_device_limit(mac, upload_kbps, download_kbps)
        from database import record_connection_event
        record_connection_event(mac, ip, 'limited',
                                f'设备已限速: 上传{upload_kbps}kbps / 下载{download_kbps}kbps')
        return jsonify({"success": True, "message": f"设备 {ip} 已限速"})
    except Exception as e:
        return jsonify({"error": f"限速失败: {str(e)}"}), 500


@app.route('/api/device/<mac>/unlimit', methods=['POST'])
def api_unlimit_device(mac):
    """取消设备限速"""
    device = get_device_by_mac(mac)
    if not device:
        return jsonify({"error": "设备不存在"}), 404

    ip = device.get('ip', '')
    try:
        unlimit_device(ip, mac)
        set_device_limit(mac, 0, 0)
        from database import record_connection_event
        record_connection_event(mac, ip, 'unlimited', '设备限速已取消')
        return jsonify({"success": True, "message": f"设备 {ip} 已取消限速"})
    except Exception as e:
        return jsonify({"error": f"取消限速失败: {str(e)}"}), 500


@app.route('/api/managed')
def api_managed_devices():
    """获取被管理的设备列表（封禁/限速）"""
    devices = get_managed_devices()
    blocked = get_blocked_devices()
    limited = get_limited_devices()
    return jsonify({
        "managed_devices": devices,
        "blocked_ips": blocked,
        "limited_classes": limited
    })


# ============================================================
# WiFi频段管理 API
# ============================================================

@app.route('/api/device/<mac>/band', methods=['POST'])
def api_set_device_band(mac):
    """设置设备WiFi频段（2.4g/5g/wired/unknown）"""
    device = get_device_by_mac(mac)
    if not device:
        return jsonify({"error": "设备不存在"}), 404

    data = request.get_json()
    band = data.get('band', 'unknown').strip().lower()

    valid_bands = ['2.4g', '5g', 'wired', 'unknown']
    if band not in valid_bands:
        return jsonify({"error": f"无效的频段，可选: {', '.join(valid_bands)}"}), 400

    set_device_wifi_band(mac, band)
    band_names = {'2.4g': '2.4GHz', '5g': '5GHz', 'wired': '有线连接', 'unknown': '未知'}
    return jsonify({"success": True, "message": f"设备频段已设置为 {band_names.get(band, band)}"})


@app.route('/api/band-stats')
def api_band_stats():
    """获取各频段设备统计"""
    stats = get_band_stats()
    # 确保所有频段都有数据
    result = {
        '2.4g': stats.get('2.4g', {'total': 0, 'online': 0}),
        '5g': stats.get('5g', {'total': 0, 'online': 0}),
        'wired': stats.get('wired', {'total': 0, 'online': 0}),
        'unknown': stats.get('unknown', {'total': 0, 'online': 0}),
    }
    return jsonify(result)


@app.route('/api/traffic-ranking')
def api_traffic_ranking():
    """获取设备流量排行榜
    参数: days=1/7/30, sort=total/download/upload
    """
    days = request.args.get('days', 7, type=int)
    sort = request.args.get('sort', 'total')
    
    if days not in [1, 7, 30]:
        days = 7
    
    ranking = get_traffic_ranking(days=days, limit=50)
    
    # 格式化数据
    for item in ranking:
        item['total_upload_str'] = format_bytes(item.get('total_upload', 0))
        item['total_download_str'] = format_bytes(item.get('total_download', 0))
        item['total_str'] = format_bytes(item.get('total_upload', 0) + item.get('total_download', 0))
    
    # 按指定方式排序
    if sort == 'download':
        ranking.sort(key=lambda x: x['total_download'], reverse=True)
    elif sort == 'upload':
        ranking.sort(key=lambda x: x['total_upload'], reverse=True)
    else:
        ranking.sort(key=lambda x: x['total_upload'] + x['total_download'], reverse=True)
    
    return jsonify({
        'days': days,
        'sort': sort,
        'ranking': ranking
    })


# ============================================================
# 浏览记录（DNS查询日志）+ AdGuard Home 统计
# ============================================================

# 缓存机制：避免频繁请求 AdGuard Home
_browsing_cache = {'data': None, 'time': 0, 'limit': 0}
_adguard_stats_cache = {'data': None, 'time': 0}
CACHE_TTL = 5  # 缓存5秒

@app.route('/api/browsing-history')
def api_browsing_history():
    """获取设备浏览记录（从AdGuard Home查询日志，包含所有访问域名，带缓存）"""
    import urllib.request
    limit = request.args.get('limit', 3000, type=int)
    device_ip = request.args.get('ip', None)

    # 检查缓存（仅无特定设备筛选时使用缓存）
    now = time.time()
    if not device_ip and _browsing_cache['data'] and _browsing_cache['limit'] == limit and (now - _browsing_cache['time']) < CACHE_TTL:
        return jsonify(_browsing_cache['data'])

    try:
        url = f"http://127.0.0.1:3000/control/querylog?limit={limit}"
        if device_ip:
            url += f"&client={device_ip}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return jsonify({"error": f"获取AdGuard日志失败: {str(e)}"}), 500

    # 按设备分组
    devices = {}
    for item in data.get('data', []):
        client = item.get('client', 'unknown')
        domain = item.get('question', {}).get('name', '')
        qtype = item.get('question', {}).get('type', '')
        status = item.get('status', '')
        reason = item.get('reason', '')
        blocked = reason.startswith('Filtered') or status == 'REFUSED'
        timestamp = item.get('time', '')
        # 提取解析结果IP（仅前2个，减少数据量）
        answer_ips = []
        for ans in item.get('answer', []):
            if ans.get('type') in ('A', 'AAAA'):
                answer_ips.append(ans.get('value', ''))
                if len(answer_ips) >= 2:
                    break
        elapsed = item.get('elapsedMs', 0)

        if client not in devices:
            devices[client] = {
                'ip': client,
                'total_queries': 0,
                'blocked_count': 0,
                'domains': {},
                'recent': []
            }

        devices[client]['total_queries'] += 1
        if blocked:
            devices[client]['blocked_count'] += 1

        # 统计所有域名（不过滤类型）
        clean_domain = domain.rstrip('.').lower()
        if clean_domain:
            if clean_domain not in devices[client]['domains']:
                devices[client]['domains'][clean_domain] = {'count': 0, 'blocked': 0, 'last_time': '', 'types': set(), 'ips': set()}
            devices[client]['domains'][clean_domain]['count'] += 1
            if blocked:
                devices[client]['domains'][clean_domain]['blocked'] += 1
            devices[client]['domains'][clean_domain]['last_time'] = timestamp
            devices[client]['domains'][clean_domain]['types'].add(qtype)
            for ip in answer_ips:
                if ip:
                    devices[client]['domains'][clean_domain]['ips'].add(ip)

        # 最近记录（最多保留30条，减少数据量）
        if len(devices[client]['recent']) < 30:
            devices[client]['recent'].append({
                'domain': clean_domain,
                'type': qtype,
                'blocked': blocked,
                'time': timestamp,
                'status': status,
                'reason': reason,
                'answer_ips': answer_ips,
                'elapsed_ms': elapsed
            })

    # 转换为列表并按查询次数排序
    result = []
    total_q = 0
    for ip, info in devices.items():
        total_q += info['total_queries']
        # 域名按访问次数排序，保留前80个（减少数据量）
        sorted_domains = sorted(info['domains'].items(), key=lambda x: x[1]['count'], reverse=True)
        info['top_domains'] = []
        for d, stats in sorted_domains[:80]:
            info['top_domains'].append({
                'domain': d,
                'count': stats['count'],
                'blocked': stats['blocked'],
                'last_time': stats['last_time'],
                'types': list(stats['types']),
                'ips': list(stats['ips'])[:3]
            })
        del info['domains']
        result.append(info)

    result.sort(key=lambda x: x['total_queries'], reverse=True)

    resp_data = {
        'total_devices': len(result),
        'total_queries': total_q,
        'devices': result
    }

    # 写入缓存（仅无特定设备筛选时）
    if not device_ip:
        _browsing_cache['data'] = resp_data
        _browsing_cache['time'] = now
        _browsing_cache['limit'] = limit

    return jsonify(resp_data)


@app.route('/api/adguard-stats')
def api_adguard_stats():
    """获取 AdGuard Home 统计数据（带缓存）"""
    import urllib.request
    now = time.time()
    if _adguard_stats_cache['data'] and (now - _adguard_stats_cache['time']) < CACHE_TTL:
        return jsonify(_adguard_stats_cache['data'])

    try:
        req = urllib.request.Request("http://127.0.0.1:3000/control/stats")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return jsonify({"error": f"获取AdGuard统计失败: {str(e)}"}), 500

    # 格式化数据
    result = {
        'num_dns_queries': data.get('num_dns_queries', 0),
        'num_blocked_filtering': data.get('num_blocked_filtering', 0),
        'num_replaced_safebrowsing': data.get('num_replaced_safebrowsing', 0),
        'num_replaced_parental': data.get('num_replaced_parental', 0),
        'num_replaced_safesearch': data.get('num_replaced_safesearch', 0),
        'avg_processing_time': data.get('avg_processing_time', 0),
        'top_queried_domains': [],
        'top_clients': [],
        'top_blocked_domains': [],
    }

    # 域名排行
    for item in data.get('top_queried_domains', []):
        for domain, count in item.items():
            result['top_queried_domains'].append({'domain': domain, 'count': count})

    # 客户端排行
    for item in data.get('top_clients', []):
        for client, count in item.items():
            result['top_clients'].append({'client': client, 'count': count})

    # 被拦截域名排行
    for item in data.get('top_blocked_domains', []):
        for domain, count in item.items():
            result['top_blocked_domains'].append({'domain': domain, 'count': count})

    # 计算拦截率
    if result['num_dns_queries'] > 0:
        result['block_rate'] = round(result['num_blocked_filtering'] / result['num_dns_queries'] * 100, 1)
    else:
        result['block_rate'] = 0

    _adguard_stats_cache['data'] = result
    _adguard_stats_cache['time'] = now
    return jsonify(result)


# ============================================================
# 全局流量监控模式（全局ARP欺骗）
# ============================================================

@app.route('/api/global-spoof/status')
def api_global_spoof_status():
    """获取全局欺骗状态"""
    return jsonify(get_global_spoof_status())


@app.route('/api/global-spoof/enable', methods=['POST'])
def api_global_spoof_enable():
    """开启全局流量监控模式"""
    try:
        success = start_global_spoof()
        if success:
            # 同时启动IPv6 NDP欺骗
            try:
                start_ipv6_spoof()
            except Exception as e:
                print(f"[IPv6] 启动IPv6欺骗失败: {e}")
            return jsonify({
                "success": True,
                "message": "全局流量监控模式已开启（IPv4+IPv6双栈），所有设备流量将经过Orange Pi",
                "status": get_global_spoof_status(),
                "ipv6_status": get_ipv6_spoof_status()
            })
        else:
            return jsonify({"error": "开启失败"}), 500
    except Exception as e:
        return jsonify({"error": f"开启失败: {str(e)}"}), 500


@app.route('/api/global-spoof/disable', methods=['POST'])
def api_global_spoof_disable():
    """关闭全局流量监控模式"""
    try:
        success = stop_global_spoof()
        if success:
            # 同时关闭IPv6欺骗
            try:
                stop_ipv6_spoof()
            except Exception as e:
                print(f"[IPv6] 关闭IPv6欺骗失败: {e}")
            return jsonify({
                "success": True,
                "message": "全局流量监控模式已关闭，设备网络已恢复",
                "status": get_global_spoof_status(),
                "ipv6_status": get_ipv6_spoof_status()
            })
        else:
            return jsonify({"error": "关闭失败"}), 500
    except Exception as e:
        return jsonify({"error": f"关闭失败: {str(e)}"}), 500


@app.route('/api/system')
def api_system():
    """系统信息"""
    import platform
    return jsonify({
        "name": "NetPulse",
        "version": "1.0.0",
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "uptime": int(time.time() - app_start_time),
        "scanner_running": scanner is not None and scanner.is_alive(),
        "traffic_monitor_running": traffic_monitor is not None and traffic_monitor.is_alive(),
    })


@app.route('/api/health-check', methods=['POST'])
def api_health_check():
    """手动触发完整健康检查和自动修复"""
    try:
        results = full_health_check()
        return jsonify({
            "success": True,
            "results": results,
            "message": "健康检查完成，已自动修复发现的问题"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


app_start_time = time.time()


def health_check_loop():
    """健康检查线程 - 定期检测并自动修复各种异常"""
    import threading
    check_count = 0
    while True:
        try:
            check_count += 1
            # 每30秒执行完整健康检查（IPv4+IPv6双栈）
            if check_count % 3 == 0:  # 每30秒
                try:
                    full_health_check()
                except Exception as e:
                    print(f"[HealthCheck] 完整检查异常: {e}")

            # 每分钟输出一次健康状态
            if check_count % 6 == 0:
                status = get_global_spoof_status()
                ipv6_status = get_ipv6_spoof_status()
                print(f"[HealthCheck] ARP欺骗={status['enabled']}, 线程={status.get('thread_alive')}, 心跳={status.get('heartbeat_age')}s | IPv6欺骗={ipv6_status['enabled']}, 线程={ipv6_status.get('thread_alive')}")

            time.sleep(10)
        except Exception as e:
            print(f"[HealthCheck] 健康检查异常: {e}")
            time.sleep(10)


def main():
    """主函数"""
    global scanner, traffic_monitor

    print("=" * 60)
    print("  NetPulse - 网络设备管理系统")
    print("=" * 60)

    # 初始化数据库
    print("[Init] 初始化数据库...")
    init_db()

    # 启动扫描线程
    print("[Init] 启动设备扫描线程...")
    scanner = Scanner(interval=5)
    scanner.start()

    # 启动流量监控线程
    print("[Init] 启动流量监控线程...")
    traffic_monitor = TrafficMonitor(interval=3)
    traffic_monitor.start()

    # 初始化设备管理系统
    print("[Init] 初始化设备管理系统...")
    init_device_manager()

    print("[Init] Global ARP/NDP monitoring remains disabled")

    # 启动全局接口速率监控线程
    print("[Init] 启动全局接口速率监控线程...")
    global_rate_thread = threading.Thread(target=_monitor_global_interface_speed, daemon=True)
    global_rate_thread.start()

    # 启动健康检查线程（自动修复机制）
    print("[Init] 启动健康检查线程（自动修复）...")
    health_thread = threading.Thread(target=health_check_loop, daemon=True)
    health_thread.start()

    # 启动Web服务
    print(f"[Init] Web服务启动: http://{WEB_HOST}:{WEB_PORT}")
    print("=" * 60)

    try:
        app.run(host=WEB_HOST, port=WEB_PORT, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\n[Shutdown] stopping service...")
        if scanner:
            scanner.stop()
        if traffic_monitor:
            traffic_monitor.stop()
        cleanup_device_manager()
        print("[Shutdown] 服务已停止")




@app.route('/api/device/<mac>/priority', methods=['POST'])
def api_set_priority(mac):
    """设置设备网络优先级"""
    try:
        data = request.get_json(force=True, silent=True)
        if data is None:
            data = {}
        priority = data.get('priority', 'medium')
    except Exception as e:
        return jsonify({'success': False, 'error': f'JSON解析失败: {str(e)}'}), 400

    if priority not in ['high', 'medium', 'low']:
        return jsonify({'success': False, 'error': '无效的优先级'}), 400

    device = get_device_by_mac(mac)
    if not device:
        return jsonify({'success': False, 'error': '设备不存在'}), 404

    ip = device.get('ip')
    if not ip or ip == '0.0.0.0':
        return jsonify({'success': False, 'error': '设备无有效IP'}), 400

    try:
        # 设置优先级
        if priority == 'medium':
            remove_device_priority(ip, mac)
        else:
            set_device_priority(ip, mac, priority)

        # 更新数据库
        update_device_priority(mac, priority)
    except Exception as e:
        return jsonify({'success': False, 'error': f'设置失败: {str(e)}'}), 500

    return jsonify({'success': True, 'priority': priority})


@app.route('/api/qos/status')
def api_qos_status():
    """获取 QoS 状态"""
    return jsonify(get_qos_status())


@app.route('/api/qos/init', methods=['POST'])
def api_qos_init():
    """重新初始化 QoS"""
    init_qos()
    return jsonify({'success': True})




# 测速实时速率监控
speedtest_realtime = {'upload': 0, 'download': 0, 'running': False, 'history': []}
speedtest_monitor_thread = None


def _monitor_global_interface_speed():
    """常驻监控转发流量速率（独立统计链，IPv4+IPv6，只统计其他设备）"""
    import time
    import subprocess
    from device_manager import setup_stats_chain, read_stats_counters, ensure_stats_chain
    global global_interface_rates

    # 初始化独立统计链（幂等，会重建为正确结构）
    setup_stats_chain()

    time.sleep(2)

    last_upload, last_download, _ = read_stats_counters()
    last_time = time.time()
    last_deep_check = time.time()

    print("[GlobalRate] 转发流量监控线程已启动（独立统计链 NETSTATS，IPv4+IPv6）")

    while True:
        try:
            time.sleep(3)
            current_upload, current_download, chain_ok = read_stats_counters()

            # 统计链丢失（被清空/服务重启/规则被冲掉），3秒内立即重建
            if not chain_ok:
                print("[GlobalRate] 检测到统计链丢失，立即重建...")
                try:
                    setup_stats_chain()
                except Exception as e:
                    print(f"[GlobalRate] 重建失败: {e}")
                last_upload, last_download, _ = read_stats_counters()
                last_time = time.time()
                continue

            now = time.time()
            delta = now - last_time

            # 如果计数器回退（链被重建），重置基线而不是计算负速率
            if current_upload < last_upload or current_download < last_download:
                print("[GlobalRate] 检测到计数器重置（统计链重建），重置基线")
                last_upload = current_upload
                last_download = current_download
                last_time = now
                continue

            if delta > 0:
                upload_kbps = max(0, (current_upload - last_upload) / delta / 1024)
                download_kbps = max(0, (current_download - last_download) / delta / 1024)
                global_interface_rates['upload_kbps'] = upload_kbps
                global_interface_rates['download_kbps'] = download_kbps
                global_interface_rates['upload_bytes'] = current_upload
                global_interface_rates['download_bytes'] = current_download
                global_interface_rates['last_update'] = now

            last_upload = current_upload
            last_download = current_download
            last_time = now

            # 每90秒做一次深度校验（检测网段变化、IPv6前缀变化并重建）
            if now - last_deep_check > 90:
                last_deep_check = now
                try:
                    ensure_stats_chain()
                except Exception:
                    pass

        except Exception as e:
            print(f"[GlobalRate] 监控错误: {e}")
            time.sleep(3)


def _monitor_interface_speed():
    """监控eth0接口的实时速率（测速时使用）"""
    import time
    global speedtest_realtime
    last_rx = 0
    last_tx = 0
    last_time = time.time()

    # 读取初始值
    try:
        with open('/proc/net/dev', 'r') as f:
            for line in f:
                if 'eth0:' in line:
                    parts = line.split()
                    last_rx = int(parts[1])
                    last_tx = int(parts[9])
                    break
    except Exception:
        pass

    speedtest_realtime['history'] = []

    while speedtest_realtime['running']:
        try:
            time.sleep(1)
            current_rx = 0
            current_tx = 0
            with open('/proc/net/dev', 'r') as f:
                for line in f:
                    if 'eth0:' in line:
                        parts = line.split()
                        current_rx = int(parts[1])
                        current_tx = int(parts[9])
                        break

            now = time.time()
            delta = now - last_time
            if delta > 0:
                # 转换为 Mbps
                download_mbps = (current_rx - last_rx) * 8 / delta / 1024 / 1024
                upload_mbps = (current_tx - last_tx) * 8 / delta / 1024 / 1024
                speedtest_realtime['download'] = max(0, download_mbps)
                speedtest_realtime['upload'] = max(0, upload_mbps)
                speedtest_realtime['history'].append({
                    'time': now,
                    'download': max(0, download_mbps),
                    'upload': max(0, upload_mbps)
                })
                # 只保留最近60条
                if len(speedtest_realtime['history']) > 60:
                    speedtest_realtime['history'].pop(0)

            last_rx = current_rx
            last_tx = current_tx
            last_time = now
        except Exception as e:
            print(f"[Speedtest Monitor] 监控错误: {e}")
            time.sleep(1)


def _test_download_speed_cn():
    """使用国内镜像站大文件测试下载速度（用curl更可靠）"""
    import subprocess
    import time

    # 国内可用的大文件下载源（已验证可用）
    urls = [
        'https://mirrors.tuna.tsinghua.edu.cn/nodejs-release/v20.10.0/node-v20.10.0-linux-x64.tar.xz',
        'https://mirrors.cloud.tencent.com/nodejs-release/v20.10.0/node-v20.10.0-linux-x64.tar.xz',
        'https://cdn.npmmirror.com/binaries/node/v20.10.0/node-v20.10.0-linux-x64.tar.xz',
    ]

    best_speed = 0
    best_url = ''

    for url in urls:
        try:
            start_time = time.time()
            # 用curl下载，最多15秒，输出速度信息
            result = subprocess.run(
                ['curl', '-o', '/dev/null', '-s', '-w', '%{speed_download} %{size_download} %{http_code}',
                 '-L', '--max-time', '15', url],
                capture_output=True, text=True, timeout=20
            )
            elapsed = time.time() - start_time
            parts = result.stdout.strip().split()
            if len(parts) >= 3:
                speed_bytes = float(parts[0])
                size_bytes = float(parts[1])
                http_code = int(parts[2])
                if http_code == 200 and size_bytes > 1024 * 1024:  # 至少下载1MB
                    speed_mbps = speed_bytes * 8 / 1024 / 1024
                    if speed_mbps > best_speed:
                        best_speed = speed_mbps
                        best_url = url
                    # 更新实时速率
                    speedtest_realtime['download'] = max(0, speed_mbps)
                    break
        except Exception as e:
            print(f"[Speedtest] 下载源 {url[:50]}... 失败: {e}")
            continue

    return best_speed, best_url


def _test_ping_cn():
    """测试到国内服务器的延迟"""
    import subprocess
    try:
        result = subprocess.run(
            ['ping', '-c', '3', '-W', '2', 'www.baidu.com'],
            capture_output=True, text=True, timeout=10
        )
        # 解析平均延迟
        for line in result.stdout.splitlines():
            if 'rtt' in line or 'avg' in line:
                parts = line.split('/')
                if len(parts) >= 5:
                    return float(parts[4])
    except Exception:
        pass
    return 0.0


@app.route('/api/speedtest/start', methods=['POST'])
def api_speedtest_start():
    global speedtest_monitor_thread
    try:
        # 启动实时速率监控线程
        speedtest_realtime['running'] = True
        speedtest_realtime['upload'] = 0
        speedtest_realtime['download'] = 0
        speedtest_monitor_thread = threading.Thread(target=_monitor_interface_speed, daemon=True)
        speedtest_monitor_thread.start()

        # 1. 测试下载速度（国内源）
        dl, used_url = _test_download_speed_cn()

        # 2. 测试延迟（国内服务器）
        ping = _test_ping_cn()

        # 3. 上传速度用speedtest-cli（加超时保护，国内没有好的上传测试源）
        ul = 0
        srv = '国内镜像站 - ' + used_url.split('/')[2] if used_url else '国内镜像站'
        try:
            import speedtest
            upload_result = [0]
            def _do_upload():
                try:
                    st = speedtest.Speedtest()
                    st.get_best_server()
                    upload_result[0] = st.upload() / 1024 / 1024
                except Exception:
                    pass
            upload_thread = threading.Thread(target=_do_upload, daemon=True)
            upload_thread.start()
            upload_thread.join(timeout=15)  # 最多等15秒
            ul = upload_result[0]
            if ul <= 0:
                ul = dl * 0.3  # 超时则估算上传为下载的30%
        except Exception as e:
            print(f"[Speedtest] 上传测试失败: {e}")
            ul = dl * 0.3  # 估算上传为下载的30%

        # 停止监控
        speedtest_realtime['running'] = False

        add_speedtest_result(dl, ul, ping, srv)
        return jsonify({
            'success': True,
            'download': round(dl, 2),
            'upload': round(ul, 2),
            'ping': round(ping, 1),
            'server': srv,
            'note': '下载速度使用国内镜像站测试，更准确'
        })
    except Exception as e:
        speedtest_realtime['running'] = False
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/speedtest/realtime')
def api_speedtest_realtime():
    """获取测速时的实时接口速率"""
    return jsonify({
        'running': speedtest_realtime['running'],
        'upload': round(speedtest_realtime['upload'], 2),
        'download': round(speedtest_realtime['download'], 2),
        'history': speedtest_realtime.get('history', [])[-30:]
    })

@app.route('/api/speedtest/history', methods=['GET'])
def api_speedtest_history():
    try:
        return jsonify({'success': True, 'history': get_speedtest_history(20)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/speedtest/history', methods=['DELETE'])
def api_speedtest_clear():
    try:
        clear_speedtest_history()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    main()
