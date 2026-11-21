"""Gửi tin cảnh báo qua Zalo Bot và nhịp tim ra watchdog. Cấu hình ở trang /cai-dat/zalo."""
import logging

import httpx
from sqlalchemy.orm import object_session

from . import config, settings

log = logging.getLogger("smartlab.notify")
ICON = {"alarm": "🔴", "reminder": "🔁", "normal": "✅", "offline": "⚠️", "online": "✅"}
API = "https://bot-api.zaloplatforms.com/bot{token}/{method}"


def call(token, method, payload=None, timeout=15):
    """Gọi Zalo Bot API. Trả về dict kết quả hoặc ném lỗi có thông điệp dễ hiểu."""
    r = httpx.post(API.format(token=token, method=method), json=payload or {}, timeout=timeout)
    try:
        data = r.json()
    except ValueError:
        r.raise_for_status()
        raise RuntimeError(f"Zalo trả về không phải JSON (HTTP {r.status_code})")
    if not data.get("ok", r.is_success):
        raise RuntimeError(data.get("description") or data.get("message") or f"HTTP {r.status_code}")
    return data.get("result", data)


def send_text(z, text, only=None):
    """Gửi text tới các người nhận đang bật. Trả về [(tên, None | lỗi)]."""
    out = []
    for rcp in z["recipients"]:
        if not rcp.get("on", True) or (only and rcp["id"] not in only):
            continue
        try:
            call(z["token"], "sendMessage", {"chat_id": rcp["id"], "text": text})
            out.append((rcp.get("name") or rcp["id"], None))
        except (httpx.HTTPError, RuntimeError) as err:
            log.error("Gửi Zalo lỗi (%s): %s", rcp["id"], err)
            out.append((rcp.get("name") or rcp["id"], str(err)))
    return out


def send_events(events, session=None):
    """Gửi các sự kiện; đánh dấu notified. Loại tin bị tắt trên trang cấu hình thì bỏ qua (coi như đã xử lý)."""
    if not events:
        return
    session = session or object_session(events[0])
    z = settings.zalo_effective(session) if session is not None else settings.DEFAULTS["zalo"]
    for ev in events:
        if not z["kinds"].get(ev.kind, True):
            ev.notified = True
            continue
        text = f"{z['prefix']} {ICON.get(ev.kind, '')} {ev.message}".strip()
        if not (z["token"] and any(r.get("on", True) for r in z["recipients"])):
            log.warning("Chưa cấu hình Zalo, bỏ qua gửi: %s", text)
            continue
        ev.notified = all(err is None for _, err in send_text(z, text))


def heartbeat():
    if not config.WATCHDOG_URL:
        return
    try:
        httpx.post(config.WATCHDOG_URL, timeout=10)
    except httpx.HTTPError as err:
        log.warning("Nhịp tim watchdog lỗi: %s", err)
