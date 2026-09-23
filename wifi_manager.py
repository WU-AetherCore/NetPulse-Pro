"""NetworkManager-backed Wi-Fi configuration helpers for the local admin UI."""
import os
import re
import shlex
import subprocess
from typing import Any


def _run(args, timeout=20):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip(), result.returncode, result.stderr.strip()
    except Exception as exc:
        return "", -1, str(exc)


def wifi_interfaces():
    output, code, _ = _run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
    result = []
    if code != 0:
        return result
    for line in output.splitlines():
        parts = line.split(":", 3)
        if len(parts) >= 3 and parts[1] == "wifi" and not parts[0].startswith("p2p-"):
            result.append({"device": parts[0], "type": parts[1], "state": parts[2], "connection": parts[3] if len(parts) > 3 else ""})
    return result


def status() -> dict[str, Any]:
    devices = wifi_interfaces()
    active = [x for x in devices if x["state"] == "connected"]
    return {"interfaces": devices, "active": active, "network_manager": _run(["systemctl", "is-active", "NetworkManager"])[0] == "active"}


def scan(interface=None):
    interface = interface or (wifi_interfaces()[0]["device"] if wifi_interfaces() else None)
    if not interface:
        return {"interface": None, "networks": [], "error": "未检测到可用无线网卡"}
    output, code, error = _run(["nmcli", "-t", "--escape", "no", "-f", "IN-USE,SSID,SIGNAL,SECURITY,CHAN", "device", "wifi", "list", "ifname", interface, "--rescan", "yes"], timeout=30)
    networks = []
    if code == 0:
        seen = set()
        for line in output.splitlines():
            parts = line.split(":")
            if len(parts) < 5:
                continue
            ssid = parts[1].strip()
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            networks.append({"in_use": parts[0] == "*", "ssid": ssid, "signal": parts[2], "security": parts[3], "channel": parts[4]})
    return {"interface": interface, "networks": networks, "error": error if code != 0 else None}


def connect(ssid, password, interface=None):
    if not ssid or not password:
        return {"success": False, "error": "SSID 和密码不能为空"}
    interfaces = wifi_interfaces()
    names = {x["device"] for x in interfaces}
    interface = interface or (interfaces[0]["device"] if interfaces else None)
    if not interface or interface not in names:
        return {"success": False, "error": "没有可用的无线网卡"}
    # nmcli receives arguments directly; credentials never go through a shell.
    output, code, error = _run(["nmcli", "device", "wifi", "connect", ssid, "password", password, "ifname", interface], timeout=45)
    if code != 0:
        return {"success": False, "error": error or output or "连接失败", "interface": interface}
    return {"success": True, "message": output or "Wi-Fi 连接成功", "interface": interface, "status": status()}
