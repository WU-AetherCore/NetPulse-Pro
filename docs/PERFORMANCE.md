# USB 上游与设备稳定性

## 多核收包

`network_performance.py` 为只有一个接收队列的 USB 默认出口开启 RPS，将流量处理分摊到在线 CPU。保持有线接口、热点、隧道和具有多个接收队列的接口原样，不改设备限速。

```bash
sudo install -m 755 network_performance.py /usr/local/sbin/netpulse-performance
sudo install -m 755 deploy/netpulse-router /usr/local/sbin/netpulse-router
sudo /usr/local/sbin/netpulse-performance
```

现有路由 dispatcher 和恢复检查调用路由脚本时会重新应用，因此支持 USB 重插与接口名称变化。单核设备不启用此功能。

2026-10-10 在现有 2.4GHz USB 上游上进行了两轮同源 8 秒对照：关闭/开启 RPS 的开发板下载吞吐分别约 8.2/10.3 Mbps 和 12.2/13.2 Mbps。另一次开启后样本约 13.4 Mbps。测试接收字节丢弃到 `/dev/null`；定时结束是预期行为。无线环境、测试源及并发流量影响结果，不能保证固定提速幅度，也不能当作有线端到端吞吐。

## 硬件与链路限制

千兆全双工只描述有线协商能力。下游设备上网仍受 USB 上游、无线频段和主路由器限制。当前 2.4GHz、20MHz 连接无法通过软件参数变成千兆互联网连接。

若内核持续报告 `mmcblk0` 读错误或 `sunxi-mmc data error`，必须备份数据库、程序及网络配置，并检查系统卡、卡座和供电。网络参数无法修复存储硬件。不要在正在挂载的系统分区上运行修复型 fsck，也不要用自动重启掩盖故障；系统卡损坏时重启可能无法重新启动。
