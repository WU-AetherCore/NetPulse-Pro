"""
NetPulse - 设备管理模块
实现设备踢出网络（ARP欺骗+iptables DROP）和设备限速（tc HTB）
"""
import subprocess
import time
import threading
import struct
import socket
import os
from config import GATEWAY_IP, LOCAL_MAC, MANAGE_INTERFACE
from config import SPOOF_WHITELIST


def run_cmd(cmd, timeout=10):
    """执行shell命令"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip(), result.returncode
    except Exception as e:
        return str(e), -1


def run_sudo(cmd, timeout=10):
    """执行需要管理员权限的命令。
    生产环境 netpulse.service 以 root 运行，直接执行即可；
    非 root 环境（如开发调试）依赖免密 sudo（sudo -n），不在代码中保存任何密码。
    """
    try:
        if hasattr(os, 'geteuid') and os.geteuid() == 0:
            return run_cmd(cmd, timeout)
        return run_cmd(f"sudo -n {cmd}", timeout)
    except Exception as e:
        return str(e), -1


# ============================================================
# ARP欺骗模块
# ============================================================

def send_arp_reply(target_ip, target_mac, spoof_ip, spoof_mac, interface=MANAGE_INTERFACE):
    """
    发送ARP响应包
    告诉target_ip: spoof_ip的MAC地址是spoof_mac
    """
    try:
        # 构造ARP包
        # Ethernet header
        eth_dest = bytes.fromhex(target_mac.replace(':', ''))
        eth_src = bytes.fromhex(spoof_mac.replace(':', ''))
        eth_type = struct.pack('!H', 0x0806)  # ARP

        # ARP header
        arp_htype = struct.pack('!H', 0x0001)  # Ethernet
        arp_ptype = struct.pack('!H', 0x0800)  # IPv4
        arp_hlen = struct.pack('!B', 6)
        arp_plen = struct.pack('!B', 4)
        arp_op = struct.pack('!H', 0x0002)  # Reply

        arp_sender_mac = bytes.fromhex(spoof_mac.replace(':', ''))
        arp_sender_ip = socket.inet_aton(spoof_ip)
        arp_target_mac = bytes.fromhex(target_mac.replace(':', ''))
        arp_target_ip = socket.inet_aton(target_ip)

        packet = (eth_dest + eth_src + eth_type +
                  arp_htype + arp_ptype + arp_hlen + arp_plen + arp_op +
                  arp_sender_mac + arp_sender_ip + arp_target_mac + arp_target_ip)

        # 发送原始套接字
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0806))
        sock.bind((interface, 0))
        sock.send(packet)
        sock.close()
        return True
    except Exception as e:
        print(f"发送ARP包失败: {e}")
        return False


def get_mac_by_ip(ip):
    """通过IP获取MAC地址（从ARP表）"""
    out, _ = run_cmd(f"arp -n {ip} 2>/dev/null | grep {ip} | awk '{{print $3}}'")
    if out and ':' in out:
        return out.lower()
    # 用ping触发ARP
    run_cmd(f"ping -c 1 -W 1 {ip} > /dev/null 2>&1")
    time.sleep(0.5)
    out, _ = run_cmd(f"arp -n {ip} 2>/dev/null | grep {ip} | awk '{{print $3}}'")
    if out and ':' in out:
        return out.lower()
    return None


class ArpSpoofer:
    """ARP欺骗管理器 - 后台线程持续发送ARP包"""

    def __init__(self):
        self.targets = {}  # {mac: {ip, target_mac, active}}
        self.lock = threading.Lock()
        self.running = False
        self.thread = None

    def add_target(self, ip, mac):
        """添加需要欺骗的目标"""
        # 检查白名单
        if mac.lower() in [m.lower() for m in SPOOF_WHITELIST]:
            print(f"[ArpSpoofer] 设备 {ip} ({mac}) 在白名单中，跳过欺骗")
            return
        with self.lock:
            self.targets[mac] = {
                'ip': ip,
                'target_mac': mac,
                'active': True
            }
        print(f"[ArpSpoofer] 添加欺骗目标: {ip} ({mac})")

    def remove_target(self, mac):
        """移除欺骗目标"""
        with self.lock:
            if mac in self.targets:
                del self.targets[mac]
        print(f"[ArpSpoofer] 移除欺骗目标: {mac}")

    def get_targets(self):
        with self.lock:
            return list(self.targets.keys())

    def start(self):
        """启动欺骗线程"""
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._spoof_loop, daemon=True)
        self.thread.start()
        print("[ArpSpoofer] ARP欺骗线程已启动")

    def stop(self):
        """停止欺骗线程"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=3)
        print("[ArpSpoofer] ARP欺骗线程已停止")

    def _spoof_loop(self):
        """持续发送ARP包"""
        while self.running:
            try:
                with self.lock:
                    targets = list(self.targets.values())

                for target in targets:
                    if not target['active']:
                        continue
                    ip = target['ip']
                    mac = target['target_mac']

                    # 欺骗目标设备：网关的MAC是我们的MAC
                    send_arp_reply(ip, mac, GATEWAY_IP, LOCAL_MAC, MANAGE_INTERFACE)

                    # 欺骗网关：目标设备的MAC是我们的MAC
                    gateway_mac = get_mac_by_ip(GATEWAY_IP)
                    if gateway_mac:
                        send_arp_reply(GATEWAY_IP, gateway_mac, ip, LOCAL_MAC, MANAGE_INTERFACE)

                time.sleep(2)  # 每2秒发送一次
            except Exception as e:
                print(f"[ArpSpoofer] 欺骗循环错误: {e}")
                time.sleep(2)


# 全局ARP欺骗器
arp_spoofer = ArpSpoofer()


# ============================================================
# iptables 设备封禁模块
# ============================================================

BLOCK_CHAIN = "NETPULSE_BLOCK"


def init_block_chain():
    """初始化封禁链"""
    # 创建自定义链
    run_sudo(f"iptables -N {BLOCK_CHAIN} 2>/dev/null")
    # 挂载到FORWARD链
    run_sudo(f"iptables -C FORWARD -j {BLOCK_CHAIN} 2>/dev/null || iptables -A FORWARD -j {BLOCK_CHAIN}")
    print(f"[Block] 封禁链 {BLOCK_CHAIN} 已初始化")


def block_device(ip, mac):
    """封禁设备 - 丢弃所有转发流量"""
    # 添加iptables DROP规则
    run_sudo(f"iptables -A {BLOCK_CHAIN} -s {ip} -j DROP")
    run_sudo(f"iptables -A {BLOCK_CHAIN} -d {ip} -j DROP")
    # 添加ARP欺骗
    arp_spoofer.add_target(ip, mac)
    print(f"[Block] 设备已封禁: {ip} ({mac})")
    return True


def unblock_device(ip, mac):
    """解除设备封禁"""
    # 删除iptables规则
    run_sudo(f"iptables -D {BLOCK_CHAIN} -s {ip} -j DROP 2>/dev/null")
    run_sudo(f"iptables -D {BLOCK_CHAIN} -d {ip} -j DROP 2>/dev/null")
    # 移除ARP欺骗
    arp_spoofer.remove_target(mac)
    # 发送正确的ARP包恢复
    gateway_mac = get_mac_by_ip(GATEWAY_IP)
    if gateway_mac:
        send_arp_reply(ip, mac, GATEWAY_IP, gateway_mac, MANAGE_INTERFACE)
        send_arp_reply(GATEWAY_IP, gateway_mac, ip, mac, MANAGE_INTERFACE)
    print(f"[Block] 设备已解封: {ip} ({mac})")
    return True


def get_blocked_devices():
    """获取已封禁设备列表"""
    out, _ = run_sudo(f"iptables -L {BLOCK_CHAIN} -n 2>/dev/null | grep DROP")
    blocked = set()
    for line in out.split('\n'):
        parts = line.split()
        if len(parts) >= 4:
            if parts[0] == 'DROP':
                # source or destination
                if '/' in parts[3]:
                    blocked.add(parts[3].split('/')[0])
    return list(blocked)


# ============================================================
# tc 限速模块
# ============================================================

LIMIT_IFACE = MANAGE_INTERFACE
LIMIT_ROOT_HANDLE = "1:"


def init_tc():
    """初始化tc qdisc（含QoS优先级）"""
    # 清除现有配置
    run_sudo(f"tc qdisc del dev {LIMIT_IFACE} root 2>/dev/null")
    # 创建HTB根qdisc，默认走中优先级类
    run_sudo(f"tc qdisc add dev {LIMIT_IFACE} root handle {LIMIT_ROOT_HANDLE} htb default 20")
    run_sudo(f"tc class add dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} classid 1:1 htb rate 1000mbit ceil 1000mbit burst 512k cburst 512k")
    # 创建三个优先级类
    # 高优先级：保证50%带宽，prio 1（最高）
    run_sudo(f"tc class add dev {LIMIT_IFACE} parent 1:1 classid 1:10 htb rate 500mbit ceil 1000mbit burst 512k cburst 512k prio 1")
    run_sudo(f"tc qdisc add dev {LIMIT_IFACE} parent 1:10 handle 100: sfq perturb 10 2>/dev/null")
    # 中优先级：保证30%带宽，prio 2（默认）
    run_sudo(f"tc class add dev {LIMIT_IFACE} parent 1:1 classid 1:20 htb rate 300mbit ceil 1000mbit burst 512k cburst 512k prio 2")
    run_sudo(f"tc qdisc add dev {LIMIT_IFACE} parent 1:20 handle 200: sfq perturb 10 2>/dev/null")
    # 低优先级：保证20%带宽，prio 3（最低）
    run_sudo(f"tc class add dev {LIMIT_IFACE} parent 1:1 classid 1:30 htb rate 200mbit ceil 1000mbit burst 512k cburst 512k prio 3")
    run_sudo(f"tc qdisc add dev {LIMIT_IFACE} parent 1:30 handle 300: sfq perturb 10 2>/dev/null")
    # 兼容旧的默认类
    run_sudo(f"tc class add dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} classid 1:999 htb rate 1000mbit ceil 1000mbit burst 512k cburst 512k")
    # 初始化 iptables mangle 链
    run_sudo("iptables -t mangle -F NETPULSE_QOS 2>/dev/null")
    run_sudo("iptables -t mangle -X NETPULSE_QOS 2>/dev/null")
    run_sudo("iptables -t mangle -N NETPULSE_QOS 2>/dev/null")
    run_sudo("iptables -t mangle -C PREROUTING -j NETPULSE_QOS 2>/dev/null || iptables -t mangle -A PREROUTING -j NETPULSE_QOS")
    run_sudo("iptables -t mangle -C POSTROUTING -j NETPULSE_QOS 2>/dev/null || iptables -t mangle -A POSTROUTING -j NETPULSE_QOS")
    print(f"[TC] tc qdisc已初始化（含QoS优先级） on {LIMIT_IFACE}")


def limit_device(ip, mac, upload_kbps, download_kbps):
    """
    限速设备
    upload_kbps: 上传限速（kbps）
    download_kbps: 下载限速（kbps）
    """
    # 生成class ID（基于IP最后一段）
    ip_parts = ip.split('.')
    class_id = int(ip_parts[3])
    if class_id < 2 or class_id > 254:
        class_id = 100 + (hash(ip) % 100)

    handle = f"1:{class_id}"

    # 删除旧规则
    run_sudo(f"tc class del dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} classid {handle} 2>/dev/null")
    run_sudo(f"tc filter del dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} prio {class_id} 2>/dev/null")

    # 总带宽取上下行较大值（简化处理，实际应该分开）
    total_rate = max(upload_kbps, download_kbps)
    total_ceil = total_rate

    # 创建HTB类
    run_sudo(f"tc class add dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} classid {handle} "
             f"htb rate {total_rate}kbit ceil {total_ceil}kbit burst 15k")

    # 添加SFQ qdisc（公平队列）
    run_sudo(f"tc qdisc add dev {LIMIT_IFACE} parent {handle} handle {class_id * 10}: sfq perturb 10 2>/dev/null")

    # 添加过滤器（按源IP匹配上传流量）
    run_sudo(f"tc filter add dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} protocol ip prio {class_id} "
             f"u32 match ip src {ip}/32 flowid {handle}")

    # 添加过滤器（按目的IP匹配下载流量）
    run_sudo(f"tc filter add dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} protocol ip prio {class_id + 1000} "
             f"u32 match ip dst {ip}/32 flowid {handle}")

    # 添加ARP欺骗（让流量经过我们）
    arp_spoofer.add_target(ip, mac)

    print(f"[TC] 设备已限速: {ip} -> {total_rate} kbps (class {handle})")
    return True


def unlimit_device(ip, mac):
    """取消设备限速"""
    ip_parts = ip.split('.')
    class_id = int(ip_parts[3])
    if class_id < 2 or class_id > 254:
        class_id = 100 + (hash(ip) % 100)

    handle = f"1:{class_id}"

    # 删除过滤器和类
    run_sudo(f"tc filter del dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} prio {class_id} 2>/dev/null")
    run_sudo(f"tc filter del dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} prio {class_id + 1000} 2>/dev/null")
    run_sudo(f"tc qdisc del dev {LIMIT_IFACE} parent {handle} 2>/dev/null")
    run_sudo(f"tc class del dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} classid {handle} 2>/dev/null")

    # 检查设备是否还在封禁列表中，如果不在则移除ARP欺骗
    blocked = get_blocked_devices()
    if ip not in blocked:
        arp_spoofer.remove_target(mac)
        # 恢复ARP
        gateway_mac = get_mac_by_ip(GATEWAY_IP)
        if gateway_mac:
            send_arp_reply(ip, mac, GATEWAY_IP, gateway_mac, MANAGE_INTERFACE)
            send_arp_reply(GATEWAY_IP, gateway_mac, ip, mac, MANAGE_INTERFACE)

    print(f"[TC] 设备已取消限速: {ip}")
    return True


def get_limited_devices():
    """获取已限速设备列表"""
    out, _ = run_sudo(f"tc class show dev {LIMIT_IFACE} 2>/dev/null")
    limited = []
    for line in out.split('\n'):
        if 'htb' in line and '1:' in line and '1:999' not in line:
            parts = line.split()
            for p in parts:
                if p.startswith('rate'):
                    rate = p.replace('rate', '')
                    limited.append({'class': parts[2], 'rate': rate})
    return limited


# ============================================================
# 系统初始化
# ============================================================

def init_device_manager():
    """初始化设备管理系统"""
    print("=" * 60)
    print("  NetPulse 设备管理系统初始化")
    print("=" * 60)

    # 开启IP转发
    run_sudo("sysctl -w net.ipv4.ip_forward=1")
    print("[Init] IP转发已开启")

    # 初始化封禁链
    init_block_chain()

    # 初始化tc
    init_tc()

    # 启动ARP欺骗线程
    arp_spoofer.start()

    # 恢复已有的封禁和限速规则
    try:
        from database import get_db
        conn = get_db()
        c = conn.cursor()
        # 恢复封禁规则
        c.execute("SELECT ip, mac FROM devices WHERE is_blocked=1 AND ip IS NOT NULL AND ip != ''")
        for ip, mac in c.fetchall():
            try:
                block_device(ip, mac)
                print(f"[Init] 恢复封禁: {ip}")
            except Exception as e:
                print(f"[Init] 恢复封禁失败 {ip}: {e}")
        # 恢复限速规则
        c.execute("SELECT ip, mac, upload_limit, download_limit FROM devices WHERE (upload_limit>0 OR download_limit>0) AND ip IS NOT NULL AND ip != ''")
        for ip, mac, ul, dl in c.fetchall():
            try:
                limit_device(ip, mac, ul or 0, dl or 0)
                print(f"[Init] 恢复限速: {ip} ({ul}/{dl}kbps)")
            except Exception as e:
                print(f"[Init] 恢复限速失败 {ip}: {e}")
        # 恢复优先级规则
        c.execute("SELECT ip, mac, priority FROM devices WHERE priority != 'medium' AND ip IS NOT NULL AND ip != ''")
        for ip, mac, priority in c.fetchall():
            try:
                if priority in ['high', 'low']:
                    set_device_priority(ip, mac, priority)
                    print(f"[Init] 恢复优先级: {ip} ({priority})")
            except Exception as e:
                print(f"[Init] 恢复优先级失败 {ip}: {e}")
        conn.close()
    except Exception as e:
        print(f"[Init] 恢复规则失败: {e}")

    print("[Init] 设备管理系统初始化完成")
    print("=" * 60)


def cleanup_device_manager():
    """清理设备管理系统"""
    print("[Cleanup] 停止设备管理系统...")
    # 先停止全局欺骗
    if global_spoof_enabled:
        stop_global_spoof()
    arp_spoofer.stop()

    # 清除iptables
    run_sudo(f"iptables -F {BLOCK_CHAIN} 2>/dev/null")
    run_sudo(f"iptables -D FORWARD -j {BLOCK_CHAIN} 2>/dev/null")
    run_sudo(f"iptables -X {BLOCK_CHAIN} 2>/dev/null")

    # 清除tc
    run_sudo(f"tc qdisc del dev {LIMIT_IFACE} root 2>/dev/null")

    # 关闭IP转发
    run_sudo("sysctl -w net.ipv4.ip_forward=0")

    print("[Cleanup] 设备管理系统已清理")


# ============================================================
# 全局ARP欺骗模块 - 让所有设备流量自动经过Orange Pi
# ============================================================

global_spoof_enabled = False
global_spoof_thread = None
global_spoof_running = False
global_spoof_heartbeat = 0  # 心跳时间戳，用于检测线程是否存活

# ARP 请求实时抢答线程（监听 who-has 网关请求并立即应答，比定时推送可靠得多）
arp_sniffer_thread = None
arp_sniffer_running = False
arp_sniffer_heartbeat = 0


def _get_local_ip(interface=MANAGE_INTERFACE):
    """动态获取本机指定接口的 IPv4 地址"""
    try:
        out, _ = run_cmd(f"ip -4 -o addr show {interface} 2>/dev/null | awk '{{print $4}}' | cut -d/ -f1")
        out = out.strip()
        if out:
            return out.splitlines()[0].strip()
    except Exception:
        pass
    return "192.168.1.10"


def restore_arp_for_device(ip, mac):
    """恢复设备的正确ARP表（发送正确的网关MAC）"""
    try:
        gateway_mac = get_mac_by_ip(GATEWAY_IP)
        if gateway_mac and mac and ip:
            # 告诉设备正确的网关MAC
            send_arp_reply(ip, mac, GATEWAY_IP, gateway_mac, MANAGE_INTERFACE)
            # 告诉网关正确的设备MAC
            send_arp_reply(GATEWAY_IP, gateway_mac, ip, mac, MANAGE_INTERFACE)
    except Exception as e:
        print(f"恢复ARP失败: {e}")


def _global_spoof_loop():
    """全局欺骗线程 - 定期扫描在线设备并发送ARP欺骗包"""
    global global_spoof_running, global_spoof_heartbeat
    print("[GlobalSpoof] 全局ARP欺骗线程已启动")

    while global_spoof_running:
        try:
            # 更新心跳
            global_spoof_heartbeat = time.time()

            # 从数据库获取所有在线设备
            try:
                from database import get_all_devices
                devices = get_all_devices()
                online_devices = [d for d in devices if d.get('is_online') == 1
                                  and d.get('ip') and d.get('mac')
                                  and d['ip'].startswith('192.168.1.')
                                  and d['ip'] != GATEWAY_IP
                                  and d['ip'] != '192.168.1.10'
                                  and d['mac'].lower() not in [m.lower() for m in SPOOF_WHITELIST]]  # 排除自己、网关和白名单设备
            except Exception as e:
                print(f"[GlobalSpoof] 获取设备列表失败: {e}")
                online_devices = []

            # 获取网关MAC
            gateway_mac = get_mac_by_ip(GATEWAY_IP)

            # 对每个在线设备发送ARP欺骗包
            for dev in online_devices:
                ip = dev['ip']
                mac = dev['mac']

                # 欺骗设备：网关的MAC是我们的MAC
                send_arp_reply(ip, mac, GATEWAY_IP, LOCAL_MAC, MANAGE_INTERFACE)

                # 欺骗网关：设备的MAC是我们的MAC
                if gateway_mac:
                    send_arp_reply(GATEWAY_IP, gateway_mac, ip, LOCAL_MAC, MANAGE_INTERFACE)

            # 每1秒推送一次（实时抢答为主、高频定时推送兜底，持续占住设备网关缓存，
            # 防止设备省电休眠醒来后被中继在WiFi侧重刷）
            time.sleep(1)

        except Exception as e:
            print(f"[GlobalSpoof] 欺骗循环错误: {e}")
            time.sleep(1)

    print("[GlobalSpoof] 全局ARP欺骗线程已停止")


def _arp_sniffer_loop():
    """ARP 请求实时抢答线程。

    持续监听网卡上的 ARP Request（who-has）：
      - 设备询问网关 MAC 时，立即抢答"网关是我"（在真网关/中继路由器应答前发出）；
      - 网关询问某设备 MAC 时，立即抢答"该设备是我"。
    设备主动发起查询时一定会接受应答，比定时推送 unsolicited reply 可靠得多，
    尤其针对无线中继场景下手机/平板忽略主动推送的问题。
    """
    global arp_sniffer_running, arp_sniffer_heartbeat
    print("[ArpSniffer] ARP实时抢答线程已启动")

    whitelist = {m.lower() for m in SPOOF_WHITELIST}
    local_ip = _get_local_ip()
    gateway_mac_cache = get_mac_by_ip(GATEWAY_IP)

    sock = None
    while arp_sniffer_running:
        try:
            if sock is None:
                sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0806))
                sock.bind((MANAGE_INTERFACE, socket.htons(0x0806)))
                sock.settimeout(1.0)

            arp_sniffer_heartbeat = time.time()
            try:
                pkt = sock.recv(65535)
            except socket.timeout:
                continue
            except OSError:
                sock = None
                time.sleep(1)
                continue

            if len(pkt) < 42:
                continue
            # 仅处理 ARP
            eth_proto = struct.unpack('!H', pkt[12:14])[0]
            if eth_proto != 0x0806:
                continue
            op = struct.unpack('!H', pkt[20:22])[0]
            if op not in (1, 2):  # 1=request 请求抢答；2=reply 用于侦听敌方网关宣告并压制
                continue

            sender_mac = ':'.join(f'{b:02x}' for b in pkt[22:28])
            sender_ip = socket.inet_ntoa(pkt[28:32])
            target_ip = socket.inet_ntoa(pkt[38:42])

            if sender_mac.lower() == LOCAL_MAC.lower():
                continue  # 忽略自己发的

            def _burst_gateway(dev_ip, dev_mac, n=3):
                """对单个设备连发 n 个'网关是本机'reply，提高WiFi客户端接收概率"""
                for _ in range(n):
                    send_arp_reply(dev_ip, dev_mac, GATEWAY_IP, LOCAL_MAC, MANAGE_INTERFACE)
                    time.sleep(0.02)

            if op == 1:  # ARP Request（who-has）
                is_lan_device = (sender_ip.startswith('192.168.1.')
                                 and sender_ip != GATEWAY_IP and sender_ip != local_ip
                                 and sender_mac.lower() not in whitelist)

                # 情况1：局域网设备询问网关 -> 立即抢答"网关MAC是本机"（连发抢占）
                if target_ip == GATEWAY_IP and is_lan_device:
                    _burst_gateway(sender_ip, sender_mac, 4)
                    if not gateway_mac_cache:
                        gateway_mac_cache = get_mac_by_ip(GATEWAY_IP)
                    if gateway_mac_cache:
                        send_arp_reply(GATEWAY_IP, gateway_mac_cache, sender_ip, LOCAL_MAC, MANAGE_INTERFACE)

                # 情况2：网关询问某局域网设备 -> 立即抢答"该设备MAC是本机"
                elif (sender_ip == GATEWAY_IP
                        and target_ip.startswith('192.168.1.')
                        and target_ip != GATEWAY_IP and target_ip != local_ip):
                    if not gateway_mac_cache:
                        gateway_mac_cache = get_mac_by_ip(GATEWAY_IP)
                    if gateway_mac_cache:
                        send_arp_reply(GATEWAY_IP, gateway_mac_cache, target_ip, LOCAL_MAC, MANAGE_INTERFACE)

                # 情况3：设备有任何ARP活动（刚唤醒/重连）-> 借机强化一次它的网关缓存
                elif is_lan_device:
                    _burst_gateway(sender_ip, sender_mac, 2)

            elif op == 2:  # ARP Reply：侦听敌方（真网关/中继）的网关宣告并立即压制
                # 形如 "192.168.1.1 is-at <非本机MAC>"，目标是某局域网设备
                if (sender_ip == GATEWAY_IP
                        and sender_mac.lower() != LOCAL_MAC.lower()
                        and target_ip.startswith('192.168.1.')
                        and target_ip != GATEWAY_IP and target_ip != local_ip):
                    # 查到该设备真实MAC后，用更高频的"网关是本机"覆盖
                    dev_mac = get_mac_by_ip(target_ip)
                    if dev_mac and dev_mac.lower() not in whitelist:
                        _burst_gateway(target_ip, dev_mac, 4)

        except Exception as e:
            print(f"[ArpSniffer] 抢答循环错误: {e}")
            try:
                if sock:
                    sock.close()
            except Exception:
                pass
            sock = None
            time.sleep(1)

    try:
        if sock:
            sock.close()
    except Exception:
        pass
    print("[ArpSniffer] ARP实时抢答线程已停止")


def ensure_nat_and_forwarding():
    """确保IP转发和NAT规则正常（自动修复机制）"""
    try:
        # 检查IP转发
        result = subprocess.run(["cat", "/proc/sys/net/ipv4/ip_forward"],
                                capture_output=True, text=True, timeout=5)
        if result.stdout.strip() != "1":
            print("[HealthCheck] IP转发未开启，正在开启...")
            run_sudo("sysctl -w net.ipv4.ip_forward=1")

        # 确保eth0的NAT规则（主网络接口）
        output, code = run_sudo("iptables -t nat -C POSTROUTING -s 192.168.1.0/24 -o eth0 -j MASQUERADE 2>/dev/null")
        if code != 0:
            print("[HealthCheck] eth0 NAT规则缺失，正在添加...")
            run_sudo("iptables -t nat -A POSTROUTING -s 192.168.1.0/24 -o eth0 -j MASQUERADE")

        # 确保wlan0的NAT规则（备用接口）
        output, code = run_sudo("iptables -t nat -C POSTROUTING -s 192.168.1.0/24 -o wlan0 -j MASQUERADE 2>/dev/null")
        if code != 0:
            print("[HealthCheck] wlan0 NAT规则缺失，正在添加...")
            run_sudo("iptables -t nat -A POSTROUTING -s 192.168.1.0/24 -o wlan0 -j MASQUERADE")

        return True
    except Exception as e:
        print(f"[HealthCheck] NAT检查失败: {e}")
        return False


def ensure_global_spoof_running():
    """检查ARP欺骗线程（定时推送 + 实时抢答）是否存活，死亡则自动重启"""
    global global_spoof_enabled, global_spoof_thread, global_spoof_running, global_spoof_heartbeat
    global arp_sniffer_thread, arp_sniffer_running

    if not global_spoof_enabled:
        return False  # 用户未开启，不需要修复

    # 检查定时欺骗线程
    thread_alive = global_spoof_thread is not None and global_spoof_thread.is_alive()
    heartbeat_ok = (time.time() - global_spoof_heartbeat) < 30 if global_spoof_heartbeat > 0 else False

    # 检查实时抢答线程
    sniffer_alive = arp_sniffer_thread is not None and arp_sniffer_thread.is_alive()
    sniffer_hb_ok = (time.time() - arp_sniffer_heartbeat) < 30 if arp_sniffer_heartbeat > 0 else False

    if thread_alive and heartbeat_ok and sniffer_alive and sniffer_hb_ok:
        return True  # 全部正常

    print(f"[HealthCheck] ARP欺骗异常！推送线程存活={thread_alive}/心跳={heartbeat_ok}, "
          f"抢答线程存活={sniffer_alive}/心跳={sniffer_hb_ok}，正在自动重启...")

    # 强制重置状态
    global_spoof_running = False
    arp_sniffer_running = False
    if global_spoof_thread:
        global_spoof_thread.join(timeout=3)
    if arp_sniffer_thread:
        arp_sniffer_thread.join(timeout=3)
    global_spoof_thread = None
    arp_sniffer_thread = None
    global_spoof_enabled = False

    # 重新启动（会同时拉起推送线程和抢答线程）
    time.sleep(1)
    success = start_global_spoof()
    if success:
        print("[HealthCheck] ARP欺骗线程（含实时抢答）已自动重启成功")
    else:
        print("[HealthCheck] ARP欺骗线程重启失败")
    return success


def start_global_spoof():
    """开启全局ARP欺骗模式 - 所有设备流量自动经过Orange Pi"""
    global global_spoof_enabled, global_spoof_thread, global_spoof_running, global_spoof_heartbeat
    global arp_sniffer_thread, arp_sniffer_running, arp_sniffer_heartbeat

    if global_spoof_enabled:
        print("[GlobalSpoof] 全局欺骗已在运行中")
        return True

    # 确保IP转发和NAT规则
    ensure_nat_and_forwarding()

    # 启动定时推送欺骗线程
    global_spoof_running = True
    global_spoof_heartbeat = time.time()
    global_spoof_thread = threading.Thread(target=_global_spoof_loop, daemon=True)
    global_spoof_thread.start()

    # 启动 ARP 请求实时抢答线程
    arp_sniffer_running = True
    arp_sniffer_heartbeat = time.time()
    arp_sniffer_thread = threading.Thread(target=_arp_sniffer_loop, daemon=True)
    arp_sniffer_thread.start()

    global_spoof_enabled = True

    print("[GlobalSpoof] 全局ARP欺骗模式已开启（定时推送 + 实时抢答）")
    print("[GlobalSpoof] 所有设备流量将自动经过Orange Pi")
    return True


def stop_global_spoof():
    """关闭全局ARP欺骗模式 - 恢复所有设备的正常网络"""
    global global_spoof_enabled, global_spoof_running
    global arp_sniffer_running, arp_sniffer_thread

    if not global_spoof_enabled:
        return True

    print("[GlobalSpoof] 正在关闭全局ARP欺骗...")
    global_spoof_running = False
    arp_sniffer_running = False

    # 等待线程结束
    if global_spoof_thread:
        global_spoof_thread.join(timeout=5)
    if arp_sniffer_thread:
        arp_sniffer_thread.join(timeout=5)

    # 恢复所有设备的ARP表
    try:
        from database import get_all_devices
        devices = get_all_devices()
        gateway_mac = get_mac_by_ip(GATEWAY_IP)

        for dev in devices:
            ip = dev.get('ip')
            mac = dev.get('mac')
            if ip and mac and ip.startswith('192.168.1.') and ip != GATEWAY_IP and ip != '192.168.1.10':
                # 发送多次确保恢复
                for _ in range(3):
                    if gateway_mac:
                        send_arp_reply(ip, mac, GATEWAY_IP, gateway_mac, MANAGE_INTERFACE)
                        send_arp_reply(GATEWAY_IP, gateway_mac, ip, mac, MANAGE_INTERFACE)
                    time.sleep(0.2)
    except Exception as e:
        print(f"[GlobalSpoof] 恢复ARP时出错: {e}")

    global_spoof_enabled = False
    print("[GlobalSpoof] 全局ARP欺骗已关闭，设备网络已恢复")
    return True


def get_global_spoof_status():
    """获取全局欺骗状态"""
    thread_alive = global_spoof_thread is not None and global_spoof_thread.is_alive()
    heartbeat_age = time.time() - global_spoof_heartbeat if global_spoof_heartbeat > 0 else -1
    sniffer_alive = arp_sniffer_thread is not None and arp_sniffer_thread.is_alive()
    sniffer_age = time.time() - arp_sniffer_heartbeat if arp_sniffer_heartbeat > 0 else -1
    return {
        "enabled": global_spoof_enabled,
        "running": global_spoof_running,
        "thread_alive": thread_alive,
        "heartbeat_age": round(heartbeat_age, 1),
        "sniffer_alive": sniffer_alive,
        "sniffer_heartbeat_age": round(sniffer_age, 1),
        "description": "全局流量监控模式" if global_spoof_enabled else "未开启"
    }


# ============================================================
# QoS 网络优先级模块
# ============================================================

QOS_IFACE = MANAGE_INTERFACE
QOS_ROOT_HANDLE = "2:"
QOS_TOTAL_BANDWIDTH = "1000mbit"  # 总带宽（HTB的ceil上限）

# 优先级配置：(class_id, mark, rate比例, prio)
PRIORITY_CONFIG = {
    'high':   {'class': '1:10', 'rate': '500mbit', 'prio': 1, 'label': '高'},
    'medium': {'class': '1:20', 'rate': '300mbit', 'prio': 2, 'label': '中'},
    'low':    {'class': '1:30', 'rate': '200mbit', 'prio': 3, 'label': '低'},
}


def init_qos():
    """初始化 QoS（调用 init_tc，已整合）"""
    init_tc()
    return True


def set_device_priority(ip, mac, priority):
    """
    设置设备网络优先级
    priority: 'high' / 'medium' / 'low'
    """
    if priority not in PRIORITY_CONFIG:
        return False

    cfg = PRIORITY_CONFIG[priority]

    # 先清除该设备旧的规则
    remove_device_priority(ip, mac)

    # medium 优先级不需要特殊规则（走默认类）
    if priority == 'medium':
        print(f"[QoS] 设备 {ip} ({mac}) 优先级设置为: medium（默认）")
        return True

    # 添加 tc filter 规则（根据源IP和目的IP匹配）
    # 使用 u32 匹配，prio 设为较小值以优先匹配
    prio_base = 100 if priority == 'high' else 300
    run_sudo(f"tc filter add dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} protocol ip prio {prio_base} "
             f"u32 match ip src {ip}/32 flowid {cfg['class']}")
    run_sudo(f"tc filter add dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} protocol ip prio {prio_base + 1} "
             f"u32 match ip dst {ip}/32 flowid {cfg['class']}")

    print(f"[QoS] 设备 {ip} ({mac}) 优先级设置为: {priority} ({cfg['label']})")
    return True


def remove_device_priority(ip, mac):
    """移除设备优先级设置"""
    # 删除 tc filter 规则（高优先级 prio 100/101，低优先级 prio 300/301）
    run_sudo(f"tc filter del dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} prio 100 2>/dev/null")
    run_sudo(f"tc filter del dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} prio 101 2>/dev/null")
    run_sudo(f"tc filter del dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} prio 300 2>/dev/null")
    run_sudo(f"tc filter del dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} prio 301 2>/dev/null")
    return True


def get_qos_status():
    """获取 QoS 状态"""
    out, _ = run_sudo(f"tc class show dev {QOS_IFACE} 2>/dev/null")
    classes = []
    for line in out.split('\n'):
        if 'htb' in line:
            classes.append(line.strip())

    out2, _ = run_sudo("iptables -t mangle -L NETPULSE_QOS -n -v 2>/dev/null")
    rules = []
    for line in out2.split('\n'):
        if 'MARK' in line:
            rules.append(line.strip())

    return {'classes': classes, 'rules': rules}


# ============================================================
# IPv6 NDP 欺骗模块（让IPv6流量也经过Orange Pi）
# ============================================================

IPV6_GATEWAY = "fe80::1"  # 路由器的IPv6链路本地地址
IPV6_SPOOF_ENABLED = False
IPV6_SPOOF_THREAD = None
IPV6_SPOOF_RUNNING = False
IPV6_SPOOF_HEARTBEAT = 0


def _get_current_ipv6_prefix(interface=MANAGE_INTERFACE):
    """动态获取当前IPv6前缀"""
    try:
        import subprocess
        result = subprocess.run(["ip", "-6", "addr", "show", interface],
                                capture_output=True, text=True)
        for line in result.stdout.split("\n"):
            if "inet6" in line and "scope global" in line and "temporary" not in line:
                addr = line.split()[1].split("/")[0]
                # 提取前64位前缀
                parts = addr.split(":")
                if len(parts) >= 4:
                    prefix = ":".join(parts[:4]) + "::"
                    return prefix
    except Exception:
        pass
    return None  # 检测失败返回 None，由调用方跳过前缀信息（不硬编码任何真实前缀）


def send_ra_advertisement(interface=MANAGE_INTERFACE):
    """发送Router Advertisement消息，让设备以为Orange Pi是IPv6路由器"""
    try:
        from scapy.all import IPv6, ICMPv6ND_RA, ICMPv6NDOptPrefixInfo, send
        from scapy.layers.inet6 import ICMPv6NDOptSrcLLAddr

        # 获取本机IPv6链路本地地址
        import subprocess
        result = subprocess.run(["ip", "-6", "addr", "show", interface],
                                capture_output=True, text=True)
        local_ipv6 = ""
        for line in result.stdout.split("\n"):
            if "fe80::" in line and "scope link" in line:
                local_ipv6 = line.split()[1].split("/")[0]
                break

        if not local_ipv6:
            local_ipv6 = "fe80::1"

        # 动态获取当前IPv6前缀
        current_prefix = _get_current_ipv6_prefix(interface)

        # 构造RA消息
        ra = IPv6(src=local_ipv6, dst="ff02::1") / ICMPv6ND_RA(
            routerlifetime=1800,  # 路由器生存时间30分钟
            reachabletime=0,
            retranstimer=0
        ) / ICMPv6NDOptSrcLLAddr(lladdr=LOCAL_MAC)

        # 添加前缀信息（仅在动态检测到当前IPv6前缀时；检测不到则跳过，不使用硬编码前缀）
        if current_prefix:
            try:
                prefix_info = ICMPv6NDOptPrefixInfo(
                    prefixlen=64,
                    L=1, A=1,
                    validlifetime=2592000,
                    preferredlifetime=604800,
                    prefix=current_prefix
                )
                ra = ra / prefix_info
            except Exception as e:
                print(f"[IPv6Spoof] 前缀信息构造失败: {e}, prefix={current_prefix}")

        send(ra, iface=interface, verbose=0)
        return True
    except Exception as e:
        print(f"[IPv6Spoof] 发送RA失败: {e}")
        return False


def send_na_spoof(target_ipv6, target_mac, spoof_ipv6, interface=MANAGE_INTERFACE):
    """发送Neighbor Advertisement欺骗消息"""
    try:
        from scapy.all import IPv6, ICMPv6ND_NA, send

        na = IPv6(src=spoof_ipv6, dst=target_ipv6) / ICMPv6ND_NA(
            tgt=spoof_ipv6,
            S=1,  # 响应请求
            R=0,  # 不是路由器
            O=1   # 覆盖缓存
        )
        send(na, iface=interface, verbose=0)
        return True
    except Exception as e:
        print(f"[IPv6Spoof] 发送NA失败: {e}")
        return False


def _ipv6_spoof_loop():
    """IPv6 NDP欺骗线程"""
    global IPV6_SPOOF_RUNNING, IPV6_SPOOF_HEARTBEAT
    print("[IPv6Spoof] IPv6 NDP欺骗线程已启动")

    while IPV6_SPOOF_RUNNING:
        try:
            IPV6_SPOOF_HEARTBEAT = time.time()

            # 1. 定期发送RA消息，让设备把我们当作IPv6网关
            send_ra_advertisement()

            # 2. 对在线设备发送NA欺骗
            try:
                from database import get_all_devices
                devices = get_all_devices()
                for dev in devices:
                    if dev.get('is_online') and dev.get('ip') and dev.get('mac'):
                        ip = dev['ip']
                        mac = dev['mac']
                        # 跳过自己和网关
                        if ip in ['192.168.1.10', GATEWAY_IP]:
                            continue
                        if mac.lower() in [m.lower() for m in SPOOF_WHITELIST]:
                            continue
                        # 构造设备的IPv6链路本地地址（基于MAC）
                        # fe80:: + EUI-64
                        try:
                            mac_parts = mac.split(':')
                            if len(mac_parts) == 6:
                                first = int(mac_parts[0], 16) ^ 0x02  # 翻转U/L位
                                target_ipv6 = f"fe80::{first:02x}{mac_parts[1]}:{mac_parts[2]}ff:fe{mac_parts[3]}:{mac_parts[4]}{mac_parts[5]}"
                                send_na_spoof(target_ipv6, mac, IPV6_GATEWAY)
                        except Exception:
                            pass
            except Exception as e:
                print(f"[IPv6Spoof] 设备NA欺骗失败: {e}")

            time.sleep(8)  # 每8秒发送一次

        except Exception as e:
            print(f"[IPv6Spoof] 欺骗循环错误: {e}")
            time.sleep(5)

    print("[IPv6Spoof] IPv6 NDP欺骗线程已停止")


def enable_ipv6_forwarding():
    """启用IPv6转发"""
    try:
        run_sudo("sysctl -w net.ipv6.conf.all.forwarding=1")
        run_sudo("sysctl -w net.ipv6.conf.default.forwarding=1")
        # 接受RA
        run_sudo("sysctl -w net.ipv6.conf.all.accept_ra=2")
        run_sudo("sysctl -w net.ipv6.conf.eth0.accept_ra=2")
        print("[IPv6Spoof] IPv6转发已启用")
        return True
    except Exception as e:
        print(f"[IPv6Spoof] 启用IPv6转发失败: {e}")
        return False


def start_ipv6_spoof():
    """开启IPv6 NDP欺骗"""
    global IPV6_SPOOF_ENABLED, IPV6_SPOOF_THREAD, IPV6_SPOOF_RUNNING, IPV6_SPOOF_HEARTBEAT

    if IPV6_SPOOF_ENABLED:
        print("[IPv6Spoof] IPv6欺骗已在运行中")
        return True

    # 启用IPv6转发
    enable_ipv6_forwarding()

    IPV6_SPOOF_RUNNING = True
    IPV6_SPOOF_HEARTBEAT = time.time()
    IPV6_SPOOF_THREAD = threading.Thread(target=_ipv6_spoof_loop, daemon=True)
    IPV6_SPOOF_THREAD.start()
    IPV6_SPOOF_ENABLED = True

    print("[IPv6Spoof] IPv6 NDP欺骗已开启")
    return True


def stop_ipv6_spoof():
    """关闭IPv6 NDP欺骗"""
    global IPV6_SPOOF_ENABLED, IPV6_SPOOF_RUNNING

    if not IPV6_SPOOF_ENABLED:
        return True

    IPV6_SPOOF_RUNNING = False
    if IPV6_SPOOF_THREAD:
        IPV6_SPOOF_THREAD.join(timeout=5)
    IPV6_SPOOF_ENABLED = False
    print("[IPv6Spoof] IPv6 NDP欺骗已关闭")
    return True


def ensure_ipv6_spoof_running():
    """检查IPv6欺骗线程是否存活，死亡则自动重启"""
    global IPV6_SPOOF_ENABLED, IPV6_SPOOF_THREAD, IPV6_SPOOF_RUNNING, IPV6_SPOOF_HEARTBEAT

    if not IPV6_SPOOF_ENABLED:
        return False

    thread_alive = IPV6_SPOOF_THREAD is not None and IPV6_SPOOF_THREAD.is_alive()
    heartbeat_ok = (time.time() - IPV6_SPOOF_HEARTBEAT) < 30 if IPV6_SPOOF_HEARTBEAT > 0 else False

    if thread_alive and heartbeat_ok:
        return True

    print(f"[HealthCheck] IPv6欺骗异常！线程存活={thread_alive}, 心跳正常={heartbeat_ok}，正在重启...")
    IPV6_SPOOF_RUNNING = False
    if IPV6_SPOOF_THREAD:
        IPV6_SPOOF_THREAD.join(timeout=3)
    IPV6_SPOOF_THREAD = None
    IPV6_SPOOF_ENABLED = False
    time.sleep(1)
    return start_ipv6_spoof()


def get_ipv6_spoof_status():
    """获取IPv6欺骗状态"""
    thread_alive = IPV6_SPOOF_THREAD is not None and IPV6_SPOOF_THREAD.is_alive()
    heartbeat_age = time.time() - IPV6_SPOOF_HEARTBEAT if IPV6_SPOOF_HEARTBEAT > 0 else -1
    return {
        "enabled": IPV6_SPOOF_ENABLED,
        "running": IPV6_SPOOF_RUNNING,
        "thread_alive": thread_alive,
        "heartbeat_age": round(heartbeat_age, 1)
    }


def full_health_check():
    """完整健康检查和自动修复（IPv4+IPv6）"""
    results = {}

    # 1. 检查IP转发
    try:
        result = subprocess.run(["cat", "/proc/sys/net/ipv4/ip_forward"],
                                capture_output=True, text=True, timeout=5)
        if result.stdout.strip() != "1":
            run_sudo("sysctl -w net.ipv4.ip_forward=1")
            results["ipv4_forward"] = "修复中"
        else:
            results["ipv4_forward"] = "正常"
    except Exception as e:
        results["ipv4_forward"] = f"错误: {e}"

    # 2. 检查IPv6转发
    try:
        result = subprocess.run(["cat", "/proc/sys/net/ipv6/conf/all/forwarding"],
                                capture_output=True, text=True, timeout=5)
        if result.stdout.strip() != "1":
            enable_ipv6_forwarding()
            results["ipv6_forward"] = "修复中"
        else:
            results["ipv6_forward"] = "正常"
    except Exception as e:
        results["ipv6_forward"] = f"错误: {e}"

    # Only repair ARP spoofing when the user explicitly enabled it.
    if global_spoof_enabled:
        results["arp_spoof"] = "enabled"
    else:
        results["arp_spoof"] = "disabled"

    # 4. 检查IPv6欺骗（如果全局监控开启）
    if global_spoof_enabled:
        if not IPV6_SPOOF_ENABLED:
            start_ipv6_spoof()
        results["ipv6_spoof"] = "正常" if ensure_ipv6_spoof_running() else "异常"
    else:
        results["ipv6_spoof"] = "未开启"

    # 5. 检查NAT规则
    results["nat"] = "正常" if ensure_nat_and_forwarding() else "异常"

    # 6. 检查独立流量统计链 NETSTATS / NETSTATS6（丢失自动重建）
    try:
        stats_ok = ensure_stats_chain()
        results["stats_chain"] = "正常" if stats_ok else "异常"
    except Exception as e:
        results["stats_chain"] = f"错误: {e}"

    print(f"[HealthCheck] 完整检查结果: {results}")
    return results


# ============================================================
# 独立流量统计链 NETSTATS / NETSTATS6
# 与设备管理链 NETPULSE 完全分离，设备规则增删不影响统计
# ============================================================

STATS_CHAIN_V4 = "NETSTATS"
STATS_CHAIN_V6 = "NETSTATS6"


def detect_lan_subnets():
    """自动检测所有局域网IPv4网段（从所有非lo、非tailscale接口推导）"""
    subnets = set()
    try:
        out, _ = run_cmd("ip -4 -o addr show", timeout=5)
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            iface = parts[1]
            if iface not in ('eth0', 'wlan0'):
                continue
            cidr = parts[3]  # 如 192.168.1.10/24
            if '/' in cidr:
                ip_addr, prefix = cidr.split('/')
                prefix = int(prefix)
                if prefix == 24:
                    # 推导 /24 网段
                    octs = ip_addr.split('.')
                    subnets.add(f"{octs[0]}.{octs[1]}.{octs[2]}.0/24")
                elif prefix <= 32:
                    subnets.add(cidr)
    except Exception as e:
        print(f"[Stats] 检测IPv4网段失败: {e}")
    # 兜底：至少包含常见网段
    if not subnets:
        subnets.update(["192.168.111.0/24", "192.168.112.0/24"])
    return sorted(subnets)


def detect_ipv6_prefixes():
    """自动检测所有全局IPv6 /64前缀"""
    prefixes = set()
    try:
        out, _ = run_cmd("ip -6 -o addr show scope global", timeout=5)
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            iface = parts[1]
            if iface not in ('eth0', 'wlan0'):
                continue
            cidr = parts[3]
            if '/' in cidr:
                addr, prefix = cidr.split('/')
                if int(prefix) == 64 and addr.count(':') >= 3:
                    # 取前4组作为 /64 前缀
                    groups = addr.split(':')
                    # 处理 :: 缩写
                    full = []
                    empty_idx = None
                    tmp_groups = addr.split('::')
                    if len(tmp_groups) == 2:
                        left = tmp_groups[0].split(':') if tmp_groups[0] else []
                        right = tmp_groups[1].split(':') if tmp_groups[1] else []
                        all_groups = left + ['0'] * (8 - len(left) - len(right)) + right
                    else:
                        all_groups = addr.split(':')
                    if len(all_groups) >= 4:
                        prefix6 = ':'.join(all_groups[:4]) + '::/64'
                        prefixes.add(prefix6)
    except Exception as e:
        print(f"[Stats] 检测IPv6前缀失败: {e}")
    return sorted(prefixes)


def setup_stats_chain():
    """创建/重建独立统计链（幂等操作，可安全重复调用）。
    结构：
      FORWARD 第1条 -> jump NETSTATS
      NETSTATS: 每个网段两条统计规则(上传/下载)，最后 RETURN
    IPv6 同理使用 NETSTATS6。
    返回 (ipv4_subnets, ipv6_prefixes)
    """
    # ---------- IPv4 ----------
    v4_subnets = detect_lan_subnets()
    try:
        # 先从 FORWARD 摘除旧跳转，清空并删除旧链
        run_sudo(f"while iptables -C FORWARD -j {STATS_CHAIN_V4} 2>/dev/null; do iptables -D FORWARD -j {STATS_CHAIN_V4} || break; done")
        run_sudo(f"iptables -F {STATS_CHAIN_V4} 2>/dev/null")
        run_sudo(f"iptables -X {STATS_CHAIN_V4} 2>/dev/null")
        time.sleep(0.2)
        # 创建新链
        run_sudo(f"iptables -N {STATS_CHAIN_V4}")
        # 为每个网段添加上传/下载统计规则（无target，仅计数，继续匹配下一条）
        for net in v4_subnets:
            run_sudo(f"iptables -A {STATS_CHAIN_V4} -s {net}")   # 上传
            run_sudo(f"iptables -A {STATS_CHAIN_V4} -d {net}")   # 下载
        # RETURN 回 FORWARD
        run_sudo(f"iptables -A {STATS_CHAIN_V4} -j RETURN")
        # 插入到 FORWARD 第一条（在所有其他链之前统计）
        run_sudo(f"iptables -I FORWARD 1 -j {STATS_CHAIN_V4}")
        print(f"[Stats] IPv4统计链已建立，网段: {v4_subnets}")
    except Exception as e:
        print(f"[Stats] IPv4统计链建立失败: {e}")

    # ---------- IPv6 ----------
    v6_prefixes = detect_ipv6_prefixes()
    try:
        run_sudo(f"while ip6tables -C FORWARD -j {STATS_CHAIN_V6} 2>/dev/null; do ip6tables -D FORWARD -j {STATS_CHAIN_V6} || break; done")
        run_sudo(f"ip6tables -F {STATS_CHAIN_V6} 2>/dev/null")
        run_sudo(f"ip6tables -X {STATS_CHAIN_V6} 2>/dev/null")
        time.sleep(0.2)
        run_sudo(f"ip6tables -N {STATS_CHAIN_V6}")
        for pfx in v6_prefixes:
            run_sudo(f"ip6tables -A {STATS_CHAIN_V6} -s {pfx}")
            run_sudo(f"ip6tables -A {STATS_CHAIN_V6} -d {pfx}")
        run_sudo(f"ip6tables -A {STATS_CHAIN_V6} -j RETURN")
        run_sudo(f"ip6tables -I FORWARD 1 -j {STATS_CHAIN_V6}")
        print(f"[Stats] IPv6统计链已建立，前缀: {v6_prefixes}")
    except Exception as e:
        print(f"[Stats] IPv6统计链建立失败: {e}")

    return v4_subnets, v6_prefixes


def read_stats_counters():
    from stats_counters import read_forward_counters
    return read_forward_counters()


def verify_stats_chain():
    """验证统计链完整性。返回 True/False。"""
    # 检查 IPv4
    try:
        result = subprocess.run(
            ['sudo', 'iptables', '-L', 'FORWARD', '-n'],
            capture_output=True, text=True, timeout=5
        )
        if STATS_CHAIN_V4 not in result.stdout:
            return False
        result2 = subprocess.run(
            ['sudo', 'iptables', '-L', STATS_CHAIN_V4, '-n'],
            capture_output=True, text=True, timeout=5
        )
        # 链中至少要有统计规则（包含局域网网段）
        if '192.168.' not in result2.stdout:
            return False
    except Exception:
        return False
    return True


def ensure_stats_chain():
    """确保统计链完整，异常则重建。返回 True 表示正常（含已修复）。"""
    if verify_stats_chain():
        return True
    print("[Stats] 检测到统计链异常，正在重建...")
    try:
        setup_stats_chain()
        return verify_stats_chain()
    except Exception as e:
        print(f"[Stats] 重建失败: {e}")
        return False
