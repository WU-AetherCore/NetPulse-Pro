"""Upstream interface selection for NetPulse."""
import ipaddress
import os
import subprocess

INTERFACES = {
    "usb": {"device": os.environ.get("NETPULSE_UPLINK", "wlan1"), "label": "USB 无线网卡"},
    "onboard": {"device": "wlan0", "label": "板载无线网卡"},
    "wired": {"device": "eth0", "label": "有线网口"},
}


def _run(args, timeout=12):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip(), result.returncode, result.stderr.strip()
    except Exception as exc:
        return "", -1, str(exc)


def _address(device):
    output, _, _ = _run(["ip", "-4", "-o", "addr", "show", "dev", device])
    for part in output.split():
        if "/" in part:
            try:
                return str(ipaddress.ip_interface(part).ip)
            except ValueError:
                pass
    return ""


def _active_connections():
    output, _, _ = _run(["nmcli", "-t", "--escape", "no", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
    result = {}
    for line in output.splitlines():
        parts = line.split(":", 3)
        if len(parts) >= 4:
            result[parts[0]] = {"type": parts[1], "state": parts[2], "connection": parts[3]}
    return result


def status():
    active = _active_connections()
    route_output, _, _ = _run(["ip", "route", "show", "default"])
    default_device = ""
    for token_index, token in enumerate(route_output.split()):
        if token == "dev" and token_index + 1 < len(route_output.split()):
            default_device = route_output.split()[token_index + 1]
            break
    items = []
    for key, info in INTERFACES.items():
        device = info["device"]
        state = active.get(device, {}).get("state", "disconnected")
        items.append({
            "key": key,
            "device": device,
            "label": info["label"],
            "state": state,
            "connection": active.get(device, {}).get("connection", ""),
            "address": _address(device),
            "default_route": device == default_device,
        })
    selected = next((item["key"] for item in items if item["default_route"]), "")
    return {"interfaces": items, "selected": selected, "default_device": default_device}


def _connection_for_device(device):
    info = _active_connections().get(device, {})
    return info.get("connection", "")


def _set_metric(connection, metric):
    if not connection:
        return True
    _, code, _ = _run(["nmcli", "connection", "modify", connection, "ipv4.route-metric", str(metric)])
    return code == 0


def set_uplink(key):
    if key not in INTERFACES:
        return {"success": False, "error": "未知的上游接口"}
    target = INTERFACES[key]["device"]
    if target != "eth0" and _active_connections().get(target, {}).get("state") != "connected":
        return {"success": False, "error": f"{INTERFACES[key]['label']} 当前未连接", "status": status()}
    for item_key, info in INTERFACES.items():
        connection = _connection_for_device(info["device"])
        if connection:
            _set_metric(connection, 100 if item_key == key else 600)
    return {"success": True, "message": f"已选择 {INTERFACES[key]['label']} 作为上游出口", "status": status()}
