# 自动检测和恢复

此功能是可选的 systemd timer，不会在安装应用时自动启用。须确认实际 USB 上游接口和当前网络配置后部署。

- 每次检查结束 30 秒后再检查，开机等待 90 秒。
- 已启用的应用、热点、DHCP 服务异常退出时尝试启动；不启动被用户禁用的服务。
- USB 上游连续三次未连接或无默认路由才修复，修复之间至少间隔 5 分钟。
- 断线时重连已保存网络；有连接但缺路由时使用 NetworkManager reapply。
- 网关邻居连续解析失败时也重连 USB 上游；单纯 ping 不通不会触发重连。
- 连接跟踪表接近容量且上限低于 32768 时扩容到 32768，不清空现有连接，不无限扩容。
- 使用现有幂等路由脚本恢复转发及 NAT 规则，不清空防火墙。
- 网络探测失败但无线和默认路由正常时，仅记录故障，避免把主路由器/运营商/DNS问题变成反复重启热点。
- USB 被拔出、驱动挂死或主 Wi-Fi 不可用无法保证自动恢复；不会自动重启整板或卸载驱动。

`/etc/default/netpulse-router` 必须有正确的 `NETPULSE_UPLINK`，可额外填写 `NETPULSE_UPLINK_UUID` 将恢复限制到一个已保存连接 UUID（用 `nmcli connection show` 查看）。切换上游网络后需同步更新该 UUID，否则恢复可能重新连接旧网络。

```bash
sudo install -m 644 network_watchdog.py /opt/netpulse-pro/network_watchdog.py
sudo install -m 644 deploy/netpulse-watchdog.service deploy/netpulse-watchdog.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo install -m 644 deploy/90-netpulse-conntrack.conf /etc/sysctl.d/
sudo sysctl -p /etc/sysctl.d/90-netpulse-conntrack.conf
sudo systemctl enable --now netpulse-watchdog.timer
sudo systemctl start netpulse-watchdog.service
sudo journalctl -u netpulse-watchdog -n 30 --no-pager
cat /run/netpulse-watchdog.json
```

运行状态包括最近探测、失败次数、最近恢复时间和命令是否成功。命令成功不等于互联网已恢复，应继续检查下一轮探测。

停用：`sudo systemctl disable --now netpulse-watchdog.timer`。主动停止热点/应用进行维护前先停用 timer，否则它会尝试启动仍处于 enabled 状态的服务。

2026-10-04 已在 Orange Pi Zero 2 部署并检查周期日志、路由、DNS 和 HTTPS 连通性，验证了 USB 重新枚举后的连接恢复。整板重启和物理更换 USB 插口尚未实测。

可设置 `NETPULSE_UPLINK_MAC` 为目标 USB 网卡的永久 MAC，检查时根据 MAC 重新查找接口名。NetworkManager 配置应绑定永久 MAC，并清空接口名约束，避免依赖 USB 插口或枚举顺序。重启后通过 boot ID 重置检查状态，避免旧的 monotonic 时间影响恢复冷却。
