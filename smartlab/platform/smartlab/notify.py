"""Gửi tin cảnh báo qua Zalo Bot và nhịp tim ra watchdog."""
import logging

import httpx

from . import config

log = logging.getLogger("smartlab.notify")
ICON = {"alarm": "🔴", "reminder": "🔁", "normal": "✅", "offline": "⚠️", "online": "✅"}


def send_events(events):
    """Gửi các sự kiện chưa gửi; đánh dấu notified. Không có cấu hình Zalo thì chỉ ghi log."""
    for ev in events:
        text = f"[SmartLab HS] {ICON.get(ev.kind, '')} {ev.message}"
        if not (config.ZALO_SEND_URL and config.ZALO_CHAT_IDS):
            log.warning("Chưa cấu hình Zalo, bỏ qua gửi: %s", text)
            continue
        ok = True
        for chat_id in config.ZALO_CHAT_IDS:
            try:
                r = httpx.post(config.ZALO_SEND_URL, json={"chat_id": chat_id, "text": text}, timeout=15)
                r.raise_for_status()
            except httpx.HTTPError as err:
                ok = False
                log.error("Gửi Zalo lỗi (%s): %s", chat_id, err)
        ev.notified = ok


def heartbeat():
    if not config.WATCHDOG_URL:
        return
    try:
        httpx.post(config.WATCHDOG_URL, timeout=10)
    except httpx.HTTPError as err:
        log.warning("Nhịp tim watchdog lỗi: %s", err)
