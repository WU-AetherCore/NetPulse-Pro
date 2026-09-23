# 常见问题与诊断

## 上传和下载曲线几乎一样

确认运行的是包含 `stats_counters.py` 的 Pro 版本。旧版用 `iptables -L` 固定列索引，`RETURN` 行因为有 target 字段发生错位，导致总计被当成上传。Pro 使用 `iptables-save -c` 并跳过跳转规则。

```bash
sudo iptables-save -c -t filter
curl http://127.0.0.1:8081/api/summary
```

刷新浏览器清掉旧采样点。持续下载时上传一般只包含请求/确认等少量数据，但 P2P、云备份等业务也可能真实地产生大量上传。

## 小米路由器不在仪表盘 TOP 10

先检查有线邻居和设备接口：

```bash
ip neigh show dev eth0
curl http://127.0.0.1:8081/api/devices
```

实时 TOP 10 过滤离线设备。Pro 扫描器同时读取 `eth0` 和 `wlan0`；如果小米 WAN 地址变了，等待下一轮扫描。NAT 路由器下的客户端合并为路由器流量是拓扑限制。

## 热点连上马上断开

```bash
sudo journalctl -u netpulse-ap -n 80 --no-pager
sudo hostapd_cli -i wlan0 all_sta
sudo journalctl -k -n 80 --no-pager
```

检查是否同时被 NetworkManager 和 hostapd 管理；确认密码和信道。当前板载驱动曾在启用 HT/WMM 时出现握手失败，因此默认关闭这两个高速参数。不要在唯一管理连接上反复试验无线模式。

## 连上热点但不能上网

按链路依次检查：

```bash
ip -br addr
ip route
nmcli device status
cat /proc/sys/net/ipv4/ip_forward
sudo iptables -t nat -S POSTROUTING
sudo iptables -S FORWARD
cat /var/lib/misc/dnsmasq.leases
```

USB 必须有有效默认路由；手机应获得 `192.168.112.x`、网关 `192.168.112.1`。手机拿到 `169.254.x.x` 通常表示未获取 DHCP。能访问 IP 但不能访问域名时检查 DNS。

## 有线口千兆，下载为什么只有几 MB/s

```bash
sudo ethtool eth0
iw dev wlan1 link
```

将 `wlan1` 替换为实际 USB 网卡。1000Mbps 是有线物理层速率，上游无线更慢时有线无法突破这个瓶颈。使用 `iperf3` 在本地两端测试才能排除公网服务器影响；项目不保证特定 MB/s。

## 页面拒绝连接

默认仅监听 `127.0.0.1`。使用 SSH 隧道，或按部署说明设置可信内网监听：

```bash
sudo systemctl status netpulse
sudo journalctl -u netpulse -n 80 --no-pager
ss -lntp | grep 8081
```

## 修改热点后页面断开

连接新的 SSID，必要时忘记旧网络。访问热点网关 `192.168.112.1:8081`，不要使用上游 Wi‑Fi 的旧地址。如果服务启动失败，程序会回退旧配置；可以通过有线登录读取日志。

## 迁移数据

停止应用再复制数据库，并保留原文件。不要把真实数据库提交到 GitHub：其中可能包含设备 MAC、主机名、流量和活动时间。

## 提交问题时附带什么

系统版本、Python 版本、网卡型号、拓扑、相关服务日志与是否能够复现。隐藏密码、会话令牌、公网地址和无关设备记录。不要上传 `.env`、NetworkManager 连接配置或完整数据库。
