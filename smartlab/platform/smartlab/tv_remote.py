"""Điều khiển tivi Samsung (Tizen) qua mạng LAN: mở dashboard, bấm phím, tắt/bật, âm lượng.

- Phím / mở ứng dụng: WebSocket wss://<IP>:8002/api/v2/channels/samsung.remote.control (lần đầu tivi hỏi "Cho phép",
  sau đó tivi cấp token, lưu lại để không hỏi nữa).
- Trạng thái: http://<IP>:8001/api/v2/  (PowerState: on / standby; có MAC).
- Bật tivi từ chế độ chờ: Wake-on-LAN tới MAC (tivi phải bật "Bật nguồn bằng điện thoại").
"""
import base64
import ipaddress
import json
import socket
import ssl
import time

import httpx

APP_NAME = "SmartLab"
BROWSER = "org.tizen.browser"
KEYS = {"enter": "KEY_ENTER", "power": "KEY_POWER", "vol_up": "KEY_VOLUP", "vol_down": "KEY_VOLDOWN",
        "mute": "KEY_MUTE", "home": "KEY_HOME", "back": "KEY_RETURN"}


class TvError(RuntimeError):
    pass


def info(ip, timeout=4):
    """Thông tin tivi qua REST (không cần cho phép). None nếu tivi tắt hẳn / không trả lời."""
    try:
        r = httpx.get(f"http://{ip}:8001/api/v2/", timeout=timeout)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError):
        return None


def power_state(ip):
    """'on' | 'standby' | 'off' (không trả lời)."""
    d = info(ip)
    if not d:
        return "off"
    return (d.get("device") or {}).get("PowerState", "on")


def _url(ip, token):
    name = base64.b64encode(APP_NAME.encode()).decode()
    u = f"wss://{ip}:8002/api/v2/channels/samsung.remote.control?name={name}"
    return u + (f"&token={token}" if token else "")


def _session(ip, token, timeout=20):
    """Mở kết nối điều khiển. Trả về (ws, token mới). Lần đầu: chờ người bấm Cho phép trên tivi."""
    from websockets.sync.client import connect
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE          # tivi dùng chứng chỉ tự ký, chỉ trong mạng LAN
    try:
        ws = connect(_url(ip, token), ssl=ctx, open_timeout=8, close_timeout=2)
    except (OSError, TimeoutError) as e:
        raise TvError(f"Không kết nối được tivi {ip} (tivi tắt, sai IP, hoặc chưa bật 'Điều khiển từ xa IP'): {e}") from e
    except Exception as e:  # noqa: BLE001 - lỗi bắt tay websocket
        raise TvError(f"Tivi từ chối kết nối: {e}") from e
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            msg = json.loads(ws.recv(timeout=max(0.5, deadline - time.monotonic())))
        except TimeoutError:
            break
        except Exception as e:  # noqa: BLE001 - tivi đóng kết nối = bấm Từ chối
            raise TvError("Tivi đã từ chối. Bấm 'Cho phép' trên tivi khi được hỏi") from e
        ev = msg.get("event")
        if ev == "ms.channel.connect":
            return ws, (msg.get("data") or {}).get("token") or token
        if ev == "ms.channel.unauthorized":
            ws.close()
            raise TvError("Tivi từ chối quyền điều khiển. Vào Cài đặt tivi → Trình quản lý thiết bị bên ngoài → "
                          "Danh sách thiết bị, cho phép 'SmartLab' rồi thử lại")
    ws.close()
    raise TvError("Tivi chưa trả lời. Nếu tivi đang hỏi 'Cho phép thiết bị SmartLab?', bấm Cho phép rồi thử lại")


def run(ip, token, steps):
    """Thực hiện lần lượt các bước: ("key", "KEY_ENTER") | ("open", url) | ("sleep", giây). Trả về token."""
    ws, token = _session(ip, token)
    try:
        for kind, arg in steps:
            if kind == "sleep":
                time.sleep(arg)
            elif kind == "key":
                ws.send(json.dumps({"method": "ms.remote.control", "params": {
                    "Cmd": "Click", "DataOfCmd": arg, "Option": "false", "TypeOfRemote": "SendRemoteKey"}}))
                time.sleep(0.4)
            elif kind == "open":
                ws.send(json.dumps({"method": "ms.channel.emit", "params": {
                    "event": "ed.apps.launch", "to": "host",
                    "data": {"appId": BROWSER, "action_type": "NATIVE_LAUNCH", "metaTag": arg}}}))
                time.sleep(0.5)
    finally:
        ws.close()
    return token


def open_dashboard_steps(url, wait_s=10):
    """Mở trình duyệt vào dashboard, chờ trang tải xong rồi bấm OK để bật âm thanh cảnh báo."""
    return [("open", url), ("sleep", wait_s), ("key", "KEY_ENTER")]


def wake(mac, ip=None, broadcast=""):
    """Gửi gói Wake-on-LAN (nhiều địa chỉ phát để chắc tới được tivi)."""
    hexmac = mac.replace(":", "").replace("-", "").strip().lower()
    if len(hexmac) != 12 or any(c not in "0123456789abcdef" for c in hexmac):
        raise TvError("Địa chỉ MAC không đúng (dạng AA:BB:CC:DD:EE:FF)")
    packet = b"\xff" * 6 + bytes.fromhex(hexmac) * 16
    targets = {"255.255.255.255"}
    if broadcast:
        targets.add(broadcast)
    if ip:
        targets.add(str(ipaddress.ip_network(f"{ip}/24", strict=False).broadcast_address))
        targets.add(ip)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for t in targets:
            for port in (9, 7):
                try:
                    s.sendto(packet, (t, port))
                except OSError:
                    pass
    return sorted(targets)


def browser_visible(ip):
    """Trình duyệt tivi có đang hiển thị không (None = tivi không cho biết)."""
    try:
        r = httpx.get(f"http://{ip}:8001/api/v2/applications/{BROWSER}", timeout=4)
        if r.status_code != 200:
            return None
        return bool(r.json().get("visible"))
    except (httpx.HTTPError, ValueError):
        return None


def find_mac(ip):
    """MAC của tivi: ưu tiên bảng ARP của máy chủ (đúng cổng LAN đang dùng), sau đó MAC Wi-Fi tivi báo."""
    import re
    import subprocess
    try:
        out = subprocess.run(["arp", "-a", ip], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"([0-9a-fA-F]{2}[-:]){5}[0-9a-fA-F]{2}", out)
        if m:
            return m.group(0).replace("-", ":").upper()
    except (OSError, subprocess.SubprocessError):
        pass
    dev = (info(ip) or {}).get("device") or {}
    return (dev.get("wifiMac") or "").upper()
