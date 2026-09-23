import os
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('NETPULSE_DB_PATH', os.path.join(BASE_DIR, 'netpulse.db'))
LOG_PATH = os.path.join(BASE_DIR, 'netpulse.log')
WEB_HOST = os.environ.get('NETPULSE_WEB_HOST', '127.0.0.1')
WEB_PORT = int(os.environ.get('NETPULSE_WEB_PORT', '8081'))
SCAN_INTERVAL = 30
TRAFFIC_INTERVAL = 3
PING_TIMEOUT = 1
PING_COUNT = 1
NETWORK_CIDR = '192.168.111.0/24'
NETWORK_GATEWAY = os.environ.get('NETPULSE_GATEWAY', '192.168.1.1')
MONITOR_INTERFACE = 'eth0'
GATEWAY_IP = NETWORK_GATEWAY
MANAGE_INTERFACE = 'eth0'
LOCAL_MAC = Path('/sys/class/net/eth0/address').read_text().strip() if Path('/sys/class/net/eth0/address').exists() else '00:00:00:00:00:00'
TRAFFIC_CHAIN = 'NETPULSE'
HOURLY_RETENTION_DAYS = 7
DAILY_RETENTION_DAYS = 90
OFFLINE_THRESHOLD = 120
ADMIN_PASSWORD = os.environ.get('NETPULSE_ADMIN_PASSWORD', '')
SPOOF_WHITELIST = []
