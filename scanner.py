"""
NetPulse - 设备扫描模块
通过ARP扫描和ping扫描发现局域网设备
"""
import subprocess
import re
import time
import threading
from config import NETWORK_CIDR, PING_TIMEOUT, PING_COUNT, OFFLINE_THRESHOLD
from database import upsert_device, mark_device_offline, record_connection_event


# OUI厂商识别（常用前缀）
OUI_DATABASE = {
    # 仅收录公开注册的常见厂商 OUI 前缀；手机随机/本地管理 MAC 无法据此识别厂商。
    # 设备可在 Web 界面手动重命名。可按需自行扩充本表。
    "44:f7:70": "小米路由器",
    "f8:29:eb": "Orange Pi",
    "c8:75:f4": "华为设备",
    "a4:50:46": "小米手机",
    "64:09:80": "小米手机",
    "9c:99:a0": "小米手机",
    "f0:99:bf": "OPPO手机",
    "94:65:2d": "VIVO手机",
    "68:54:5a": "苹果设备",
    "3c:22:fb": "苹果设备",
    "a4:83:e7": "苹果设备",
    "00:11:32": "群晖NAS",
    "00:17:88": "飞利浦Hue",
    "b8:27:eb": "树莓派",
    "dc:a6:32": "树莓派4",
    "e4:5f:01": "树莓派400",
}


def get_vendor(mac):
    """根据MAC地址前3字节识别厂商"""
    if not mac or len(mac) < 8:
        return "未知设备"
    oui = mac.lower()[:8]
    return OUI_DATABASE.get(oui, "未知设备")


def read_arp_table():
    """读取ARP表"""
    devices = {}
    try:
        result = subprocess.run(
            ["ip", "-4", "neigh", "show"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.strip().split('\n'):
            if not line or 'FAILED' in line or 'INCOMPLETE' in line:
                continue
            parts = line.split()
            if 'dev' not in parts:
                continue
            interface_index = parts.index('dev') + 1
            if interface_index >= len(parts) or parts[interface_index] not in ('eth0', 'wlan0'):
                continue
            if len(parts) >= 4 and 'lladdr' in line:
                ip = parts[0]
                mac_idx = parts.index('lladdr') + 1
                if mac_idx < len(parts):
                    mac = parts[mac_idx]
                    if mac and mac != '00:00:00:00:00:00':
                        # 优先使用IPv4地址，如果已经有IPv4就不用IPv6覆盖
                        is_ipv4 = '.' in ip and ':' not in ip
                        if mac not in devices or is_ipv4:
                            devices[mac] = ip
    except Exception as e:
        print(f"读取ARP表失败: {e}")
    return devices


def ping_scan():
    """ping扫描主网段唤醒设备（分批低并发，避免ARP广播风暴冲击设备网关缓存）"""
    try:
        # 仅扫描主网段 192.168.1.x；分批并发，每批40个，避免瞬时数百ARP请求
        for start in range(1, 255, 40):
            cmds = " ".join(
                f"(ping -c 1 -W {PING_TIMEOUT} 192.168.1.{i} >/dev/null 2>&1 &)"
                for i in range(start, min(start + 40, 255))
            )
            subprocess.run(["bash", "-c", cmds], capture_output=True, text=True, timeout=10)
            time.sleep(0.3)
    except Exception as e:
        print(f"ping扫描失败: {e}")


def add_self_device():
    """自动把自己（Orange Pi）添加到设备列表"""
    try:
        import socket
        # 获取本机IP和MAC
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        
        # 从/sys/class/net获取MAC
        mac = None
        for iface in ['eth0', 'wlan0']:
            try:
                with open(f'/sys/class/net/{iface}/address', 'r') as f:
                    mac = f.read().strip()
                    break
            except:
                continue
        
        if local_ip and mac:
            from database import upsert_device
            upsert_device(mac, local_ip, name='Orange Pi Zero2', vendor='Orange Pi')
            print(f"[Scanner] 已添加本机设备: {local_ip} ({mac})")
    except Exception as e:
        print(f"[Scanner] 添加本机设备失败: {e}")


def scan_routed_devices():
    """扫描路由网段设备（非直连，通过路由可达的设备）"""
    routed = {}
    try:
        # 扫描 192.168.0.x 网段（路由可达的设备）
        result = subprocess.run(
            ["bash", "-c",
             "for i in $(seq 1 254); do "
             "  if ping -c 1 -W 1 192.168.0.$i >/dev/null 2>&1; then "
             "    echo 192.168.0.$i; "
             "  fi & "
             "done; wait"],
            capture_output=True, text=True, timeout=20
        )
        for line in result.stdout.strip().split('\n'):
            ip = line.strip()
            if ip and '.' in ip:
                # 生成虚拟MAC地址（基于IP，标记为路由设备）
                parts = ip.split('.')
                virtual_mac = f"00:00:00:{int(parts[1]):02x}:{int(parts[2]):02x}:{int(parts[3]):02x}"
                routed[virtual_mac] = ip
    except Exception as e:
        print(f"路由网段扫描失败: {e}")
    return routed


def scan_devices():
    """扫描所有设备"""
    # 先添加自己
    add_self_device()

    # 先ping扫描唤醒设备
    subprocess.run(["ping", "-b", "-c", "1", "-W", "1", "192.168.112.255"],
                   capture_output=True, timeout=3)

    # 读取ARP表（直连设备，含 eth0/wlan0 两个接口学到的邻居）
    arp_devices = read_arp_table()

    # 更新数据库 - 直连设备
    now = int(time.time())
    for mac, ip in arp_devices.items():
        vendor = get_vendor(mac)
        upsert_device(mac, ip, vendor=vendor)

    # 检查离线设备
    check_offline_devices()

    return dict(arp_devices)


def check_offline_devices():
    """检查并标记离线设备"""
    from database import get_all_devices
    now = int(time.time())
    devices = get_all_devices()
    for dev in devices:
        if dev['is_online'] and (now - dev['last_seen']) > OFFLINE_THRESHOLD:
            mark_device_offline(dev['mac'])


class Scanner(threading.Thread):
    """后台扫描线程"""
    def __init__(self, interval=60):
        super().__init__(daemon=True)
        self.interval = interval
        self.running = True
        self.last_report = 0
        self.last_count = None

    def run(self):
        print(f"[Scanner] 设备扫描线程启动，间隔{self.interval}秒")
        while self.running:
            try:
                devices = scan_devices()
                now = time.monotonic()
                if len(devices) != self.last_count or now - self.last_report >= 60:
                    print(f"[Scanner] 扫描完成，发现{len(devices)}台设备")
                    self.last_count, self.last_report = len(devices), now
            except Exception as e:
                print(f"[Scanner] 扫描异常: {e}")
            time.sleep(self.interval)

    def stop(self):
        self.running = False
