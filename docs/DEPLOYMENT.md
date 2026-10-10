# 部署教程

本教程配置 USB Wi‑Fi 上网、`eth0` 有线下游、`wlan0` 板载热点。应用安装与网络变更分开执行，避免一次操作后无法找回开发板。

## 1. 准备与备份

需要 Python 3.10+、Debian/Ubuntu 类系统、systemd，以及支持 AP 的板载无线驱动。USB 网卡需要已有 Linux 驱动及固件；仓库不分发厂商固件二进制。

准备串口或保留一条有线管理路径。正在通过 `wlan0` SSH 登录时不要直接把它改成热点。

```bash
ip -br addr
ip route
nmcli device status
iw list
sudo cp -a /etc/NetworkManager /root/NetworkManager.before-netpulse
```

已有服务请备份应用、环境文件、hostapd/dnsmasq 配置。SQLite 数据库先用 sqlite3 的 `.backup` 或停服务后复制，避免只复制正在写入的数据库文件导致 WAL 数据缺失。

## 2. 安装应用

```bash
sudo apt update
sudo apt install -y git python3 python3-venv network-manager hostapd dnsmasq iw ethtool
git clone https://github.com/WU-AetherCore/NetPulse-Pro.git
cd NetPulse-Pro
sudo bash install.sh
```

应用位于 `/opt/netpulse-pro`，服务名是 `netpulse`。`.env` 如果已存在不会覆盖。安装脚本不迁移原目录的数据库，需要用户自行备份后复制。

安装可能需要下载 Python 依赖。如果板子下载慢，可在电脑下载源码 ZIP 并通过 SCP 上传。Python wheels 必须匹配开发板架构与 Python 版本；不要将 Windows 虚拟环境直接复制到 ARM Linux。

## 3. USB 无线连接上游

用实际接口名替换 `wlan1`，SSID 替换为你自己的主 Wi‑Fi 名称。`--ask` 会交互询问密码，不把密码写入本教程或命令历史。USB 网卡常被 udev 改名为 `wlx` 开头的 MAC 派生名称，用 `nmcli device status` 确认实际名字。

```bash
nmcli device status
sudo nmcli --ask device wifi connect '你的主WiFi名称' ifname wlan1
ip route
```

编辑 `/opt/netpulse-pro/.env`：

```ini
# 默认值就是 0.0.0.0：仪表盘在有线下游和热点上都能直接访问。
NETPULSE_WEB_HOST=0.0.0.0
NETPULSE_WEB_PORT=8081
# 留空即自动识别 USB 无线网卡（wlx* 或 wlan1），无需手填接口名。
NETPULSE_UPLINK=
NETPULSE_GATEWAY=192.168.1.1
```

`NETPULSE_UPLINK` 留空时按此顺序识别上行：该变量指定的接口 → 承载默认路由的无线接口 → 其它已连接的无线接口。板载 `wlan0` 永远不会被当作上行。只有需要强制指定时才填写接口名。

上游默认使用 DHCP。连接不同 Wi‑Fi 时，固定 `192.168.1.x` 并不能保证可用；不要把上游和任一下游改为同一网段。上游连接名与 SSID 可能不同，以 `nmcli connection show` 为准。

```bash
sudo nmcli connection modify '你的连接名' 802-11-wireless.powersave 2
sudo systemctl restart netpulse
```

## 4. 配置有线下游

以下命令会建立一个专用有线配置。已有 `eth0` 配置先记录并确认；不要在远程连接依赖该接口时盲目执行 `connection up`。

```bash
sudo nmcli connection add type ethernet ifname eth0 con-name netpulse-lan \
  ipv4.method manual ipv4.addresses 192.168.111.1/24 \
  ipv4.never-default yes ipv6.method disabled
sudo nmcli connection up netpulse-lan
```

小米两种接法任选其一：

| 模式 | 接线及地址 | 统计效果 |
|---|---|---|
| 小米路由模式，DHCP 开启 | 板子 eth0 → 小米 WAN；WAN 自动获取 192.168.111.x，LAN 可为 192.168.1.1 | 只能识别小米 WAN 合计 |
| 小米有线 AP / 桥接 | 按路由器 AP 模式要求接上联口；客户端由开发板发 DHCP | 可分别识别客户端 |

不要只关闭一个 DHCP 开关就认为路由器变成 AP，也不要在同一广播域运行两个发放不同网关的 DHCP 服务。

## 5. 配置板载热点

```bash
sudo mkdir -p /etc/hostapd
sudo install -m 600 deploy/hostapd.conf.example /etc/hostapd/netpulse.conf
sudo nano /etc/hostapd/netpulse.conf
```

**必须修改 `wpa_passphrase` 占位值**，设置 8–63 位英文、数字或符号。SSID 最多 32 个 UTF‑8 字节。默认热点名为 `OrangePiZero2-AP`。

```bash
sudo install -m 644 deploy/90-netpulse-ap.conf /etc/NetworkManager/conf.d/
sudo nmcli general reload
sudo nmcli device set wlan0 managed no
sudo install -m 644 deploy/netpulse-ap.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now netpulse-ap
sudo hostapd_cli -i wlan0 status
```

确认 `state=ENABLED`。不能同时运行另一份占用 `wlan0` 的 hostapd 或 NetworkManager 热点。模板保留兼容参数，启动成功不等于手机已握手成功，还需实际连接验证。

## 6. 下游 DHCP

检查系统是否已有 DHCP 服务，避免重复绑定接口。

```bash
sudo install -m 644 deploy/dnsmasq-netpulse.conf /etc/dnsmasq.d/netpulse-ap.conf
sudo dnsmasq --test
sudo systemctl enable dnsmasq
sudo systemctl restart dnsmasq
```

该模板设置 `port=0`，只提供 DHCP；客户端 DNS 默认指向外部 DNS，并不会自动安装或使用 AdGuard Home。

## 7. 开启转发和 NAT

```bash
sudo nano /etc/default/netpulse-router
```

填写与你的 USB 接口一致的名称：

```ini
NETPULSE_UPLINK=wlan1
```

```bash
sudo install -m 755 deploy/netpulse-router /usr/local/sbin/netpulse-router
sudo install -m 644 deploy/netpulse-router.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now netpulse-router
```

脚本只追加本拓扑需要的规则，不清空整个防火墙。已有 UFW/nftables 拒绝规则可能优先匹配，需要按实际防火墙策略放行这两个下游；不要直接关闭全部防火墙。更换 USB 接口名称后，应用 `.env` 和路由环境文件都需要更新。

此部署只建立 IPv4 NAT。`NETSTATS6` 能读取规则计数不代表已经配置 IPv6 前缀委派、RA 和可用的 IPv6 下游。

## 8. 访问 Web

默认监听 `0.0.0.0`，因此有线下游和热点都能直接打开仪表盘。**该版本没有内置完整登录**，务必用防火墙把 8081 限制为只允许管理设备访问，且不要做公网端口映射。若要改为只允许本机访问（例如只走 SSH 隧道），把 `.env` 中 `NETPULSE_WEB_HOST` 设为 `127.0.0.1` 后重启服务。

```bash
sudo systemctl restart netpulse
```

手机连接热点后访问 `http://192.168.112.1:8081`；有线下游访问 `http://192.168.111.1:8081`。

## 9. 验收

```bash
systemctl is-active netpulse netpulse-ap dnsmasq netpulse-router
ip route
sudo hostapd_cli -i wlan0 all_sta
cat /var/lib/misc/dnsmasq.leases
sudo iptables -nvxL NETSTATS
sudo iptables -t nat -S POSTROUTING
curl http://127.0.0.1:8081/api/network/overview
```

手机关闭移动数据再打开网页，确认确实通过热点上网；执行一次下载，观察下载曲线明显高于上传。小米路由模式下应看到其 WAN 地址在线。

## 10. 更新与回退

在源码目录 `git pull` 后重新执行安装脚本。已有 `/opt/netpulse-pro` 在跨目录安装时会备份到带时间戳的 `/opt/netpulse-pro-backup-*`，但 systemd 及网络配置需单独备份。回退应用时停止 `netpulse`、恢复原应用和环境文件、恢复原服务文件后执行 `daemon-reload` 再启动。

网络回退请通过保留的管理通道恢复原 NetworkManager 连接，再停止 `netpulse-ap` / `netpulse-router`；停止 oneshot 路由服务不会自动删除已追加的 iptables 规则，需对照脚本逐条用 `-D` 移除。不要直接清空系统全部 NAT 或过滤规则。
