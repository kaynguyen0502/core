"""Tự động cho tivi dashboard (chạy trong tiến trình nền): mở dashboard khi tivi vừa bật, mỗi sáng, hoặc khi bị đóng."""
import logging
import threading
import time
from datetime import datetime

from . import settings, tv_remote

log = logging.getLogger("smartlab.tv")


def open_dashboard(session, cfg=None):
    """Mở dashboard trên tivi + bấm OK bật âm thanh. Lưu token mới nếu tivi cấp."""
    cfg = cfg or settings.get(session, "tv")
    if not cfg["ip"] or not cfg["dashboard_url"]:
        raise tv_remote.TvError("Chưa nhập IP tivi hoặc địa chỉ dashboard")
    token = tv_remote.run(cfg["ip"], cfg["token"], tv_remote.open_dashboard_steps(cfg["dashboard_url"], cfg["wait_seconds"]))
    if token and token != cfg["token"]:
        settings.put(session, "tv", {"token": token}, "tivi")
    return True


class Auto:
    def __init__(self):
        self.last_state = None
        self.daily_done = ""
        self.last_open = 0.0

    def tick(self, session, now=None):
        cfg = settings.get(session, "tv")
        if not cfg["enabled"] or not cfg["ip"]:
            return None
        now = now or datetime.now()
        state = tv_remote.power_state(cfg["ip"])
        reason = None
        due = cfg["daily_time"] and now.strftime("%H:%M") >= cfg["daily_time"] and self.daily_done != now.date().isoformat()
        if due and state != "on" and cfg["wake_daily"] and cfg["mac"]:
            tv_remote.wake(cfg["mac"], cfg["ip"], cfg["broadcast"])
            log.info("Tivi: gửi lệnh bật (giờ mở hằng ngày)")
        if state == "on":
            if self.last_state not in (None, "on") and cfg["on_power"]:
                reason = "tivi vừa bật"
            elif due:
                reason = "giờ mở hằng ngày"
            elif cfg["keep_open"] and time.monotonic() - self.last_open > 600 \
                    and tv_remote.browser_visible(cfg["ip"]) is False:
                reason = "trình duyệt đã bị đóng"
        self.last_state = state
        if reason:
            if due:
                self.daily_done = now.date().isoformat()
            self.last_open = time.monotonic()
            open_dashboard(session, cfg)
            log.info("Tivi: đã mở dashboard (%s)", reason)
        return reason


def start():
    """Luồng nền kiểm tra tivi mỗi 30 giây."""
    from . import db

    def loop():
        auto = Auto()
        while True:
            try:
                with db.SessionLocal() as s:
                    auto.tick(s)
            except tv_remote.TvError as e:
                log.warning("Tivi: %s", e)
            except Exception:  # noqa: BLE001
                log.exception("Lỗi tự động tivi")
            time.sleep(30)
    threading.Thread(target=loop, name="tv-auto", daemon=True).start()
