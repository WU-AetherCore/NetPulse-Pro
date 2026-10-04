# USB 上游网卡与有线优化

本次实机使用 AIC8800D80 USB 无线网卡替换原上游，保留当前主 Wi-Fi。不同网卡必须先确认芯片、USB ID、驱动与固件兼容，不能仅按外观或产品名称安装。

## 更换插口时保持连接配置

NetworkManager 配置绑定网卡永久 MAC，清空接口名约束并开启自动连接：

```bash
nmcli -f GENERAL.HWADDR device show <接口名>
sudo nmcli connection modify <连接UUID> connection.interface-name "" \
  802-11-wireless.mac-address <永久MAC> \
  802-11-wireless.cloned-mac-address permanent \
  802-11-wireless.powersave 2 connection.autoconnect yes \
  connection.autoconnect-retries 0
```

在 `/etc/default/netpulse-router` 设置相同的 `NETPULSE_UPLINK_MAC`、`NETPULSE_UPLINK_UUID`，以及当前接口名 `NETPULSE_UPLINK`。恢复程序按 MAC 查找接口，避免依赖 USB 枚举名称。保留有线管理入口后再验证拔插；缺少驱动或固件时，连接配置本身不能恢复网卡。

可安装路由 dispatcher，让默认出口变化后恢复规则：

```bash
sudo install -m 755 deploy/netpulse-router /usr/local/sbin/netpulse-router
sudo install -m 755 deploy/90-netpulse-router /etc/NetworkManager/dispatcher.d/90-netpulse-router
```

## 驱动与验证范围

实机内核 6.1.31-sun50iw9，使用匹配内核头文件、AIC8800 驱动和 D80 固件，完成 DKMS 安装。驱动源来自 `radxa-pkg/aic8800`；此硬件的运行 USB ID `368b:8d88` 需要正确设备表和芯片识别支持。内核升级后仍需要匹配头文件和可编译的驱动源码，DKMS 无法保证兼容所有未来内核。

已验证软件 USB 重新枚举后自动恢复连接。物理换口和整板重启尚未实测。

## 有线带宽

eth0 已确认 1000 Mbps 全双工。HTB 三个优先级类现在挂在共同的千兆父类下，允许借用空闲带宽；明确设置的设备限速保持生效。修复统计链重建时残留重复跳转导致的重复计数。

同一路径的 TCP 测试中，开发板到电脑的下行由约 419 提升至 648 Mbps，反向约 879 Mbps。该路径包含电脑无线链路与下游路由器，是本地网络测试，不代表纯网线性能或互联网速度上限。继续使用 2.4 GHz 上游时，互联网速度仍可能受无线链路限制。
