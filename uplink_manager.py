"""Upstream interface selection for NetPulse."""
import ipaddress
import os
import re
import subprocess

# wlan0 is the onboard radio on this platform; additional wireless interfaces
# (wlan1, or the MAC-derived wlx* names that USB adapters get) are uplinks.
_USB_NAME = re.compile(r'^(wlx\w+|wlan[1-9][0-9]*)$')
_WIRELESS_DIR = '/sys/class/net'

INTERFACES = {
    "usb": {"device": "wlan1", "label": "USB 无线网卡"},
    "onboard": {"device": "wlan0", "label": "板载无线网卡"},
    "wired": {"device": "eth0", "label": "有线网口"},
}

_usb_cache = None


def _run(args, timeout=12):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip(), result.returncode, result.stderr.strip()
    except Exception as exc:
        return "", -1, str(exc)


def _wireless_names():
    try:
        return sorted(name for name in os.listdir(_WIRELESS_DIR)
                      if os.path.isdir(os.path.join(_WIRELESS_DIR, name, 'wireless')))
    except OSError:
        return []


def _default_device():
    output, _, _ = _run(["ip", "route", "show", "default"])
    tokens = output.split()
    for index, token in enumerate(tokens):
        if token == "dev" and index + 1 < len(tokens):
            return tokens[index + 1]
    return ""


def _connected(device):
    output, _, _ = _run(["nmcli", "-t", "--escape", "no", "-f", "DEVICE,STATE", "device", "status"])
    for line in output.splitlines():
        parts = line.split(":", 1)
        if len(parts) == 2 and parts[0] == device:
            return parts[1].startswith("connected")
    return False


def usb_device(refresh=False):
    """Resolve the USB wireless uplink interface name.

    ``NETPULSE_UPLINK`` wins when it names a wireless interface. Otherwise the
    interface carrying the default route, then any other connected wireless
    interface, is used. USB adapters are renamed to MAC-derived ``wlx*`` names
    by udev, so a literal ``wlan1`` cannot be assumed.
    """
    global _usb_cache
    if _usb_cache is not None and not refresh:
        return _usb_cache
    configured = os.environ.get("NETPULSE_UPLINK", "").strip()
    if configured and _USB_NAME.match(configured):
        _usb_cache = configured
        return _usb_cache
    candidates = [name for name in _wireless_names() if _USB_NAME.match(name)]
    default = _default_device()
    if default in candidates:
        _usb_cache = default
        return _usb_cache
    for name in candidates:
        if _connected(name):
            _usb_cache = name
            return _usb_cache
    _usb_cache = candidates[0] if candidates else "wlan1"
    return _usb_cache


def device_for(key):
    if key == "usb":
        return usb_device()
    return INTERFACES.get(key, {}).get("device", "")


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
        device = device_for(key)
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
    target = device_for(key)
    if target != "eth0" and _active_connections().get(target, {}).get("state") != "connected":
        return {"success": False, "error": f"{INTERFACES[key]['label']} 当前未连接", "status": status()}
    for item_key, info in INTERFACES.items():
        connection = _connection_for_device(device_for(item_key))
        if connection:
            _set_metric(connection, 100 if item_key == key else 600)
    return {"success": True, "message": f"已选择 {INTERFACES[key]['label']} 作为上游出口", "status": status()}
