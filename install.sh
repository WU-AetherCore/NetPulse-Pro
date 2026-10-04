#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
    echo "请使用 sudo bash install.sh"
    exit 1
fi
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
target=/opt/netpulse-pro
python3 -c 'import sys; assert sys.version_info >= (3,10), "需要 Python 3.10+"'
apt-get update
apt-get install -y python3-venv python3-pip iptables iproute2 sudo curl iputils-ping
install -d "$target/templates" "$target/static"
if [[ "$source_dir" != "$target" ]]; then
    if [[ -f "$target/app.py" ]]; then
        backup="/opt/netpulse-pro-backup-$(date +%Y%m%d-%H%M%S)"
        cp -a "$target" "$backup"
        echo "已备份到 $backup"
    fi
    cp "$source_dir"/*.py "$target/"
    cp "$source_dir"/templates/*.html "$target/templates/"
    cp -a "$source_dir/static/." "$target/static/"
    cp "$source_dir/requirements.txt" "$target/"
fi
if [[ ! -f "$target/.env" ]]; then
    install -m 600 "$source_dir/env.example" "$target/.env"
fi
python3 -m venv "$target/.venv"
"$target/.venv/bin/pip" install -r "$target/requirements.txt"
"$target/.venv/bin/python" -m compileall -q "$target"
install -m 644 "$source_dir/deploy/netpulse.service" /etc/systemd/system/netpulse.service
systemctl daemon-reload
systemctl enable --now netpulse
systemctl restart netpulse
echo "应用已安装。默认仅监听 127.0.0.1:8081；网络与访问配置请阅读 docs/DEPLOYMENT.md。"
