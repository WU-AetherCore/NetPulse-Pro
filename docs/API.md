# API 参考

与 Web 使用相同主机和端口。默认通过本机/SSH 隧道访问；此版本没有完整登录鉴权，不应直接暴露 API 到公网。

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/api/summary` | 总量和实时速率；速率数字按 KiB/s |
| GET | `/api/devices` | 设备列表和在线状态 |
| GET | `/api/device/<mac>` | 设备详情 |
| POST | `/api/scan` | 触发扫描 |
| GET | `/api/traffic-ranking?days=7&sort=total` | 历史排行 |
| GET | `/api/events?limit=100` | 连接事件 |
| GET | `/api/network/overview` | 接口、路由、热点、租约、最近应用状态 |
| POST | `/api/network/hotspot` | 保存热点名称、密码和信道 |
| GET | `/api/wifi/status` | NetworkManager 无线状态 |
| GET | `/api/wifi/scan?interface=wlan1` | 指定接口扫描网络 |
| POST | `/api/wifi/connect` | 连接指定 Wi‑Fi |
| GET | `/api/uplink/status` | 上游接口及默认路由 |
| GET | `/api/period-summary` | 周期统计 |
| POST | `/api/period-settings` | 设置周期 |
| GET | `/api/adguard-stats` | 可选 AdGuard 统计 |
| GET | `/api/browsing-history` | 可选 DNS 查询记录 |

热点请求：

```json
{"ssid":"My-OrangePi-AP","password":"","channel":"6"}
```

密码空字符串表示保留原密码。返回 `success: true` 代表已提交，最终结果看 `/api/network/overview` 中 `apply.state`（`pending` / `success` / `error`）。不要把提交成功当作手机已经重新联网。

Wi‑Fi 连接请求结构：

```json
{"interface":"wlan1","ssid":"Your-WiFi","password":"YOUR_WIFI_PASSWORD"}
```

密码仅用于运行时请求，不要把真实请求体放入日志或公开 Issue。切换上游可能中断 HTTP 响应；恢复后读取状态确认。

旧版 `/api/uplink/set`、ARP/NDP、限速接口仍保留，但不是完整的网络编排 API：改变路由 metric 不会自动重写 NAT 出口或把板载热点改成上游。请按部署文档操作。
