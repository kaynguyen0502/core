#!/usr/bin/env python3
"""Watchdog chạy trên VPS của Bên B (ngoài bệnh viện).

Home Assistant tại khoa POST nhịp tim mỗi phút tới /hb/<site>?key=<KEY>.
Nếu quá TIMEOUT phút không nhận được -> gửi Zalo "mất kết nối" (mất mạng / mất điện
kéo dài / máy chủ treo). Khi có lại -> gửi Zalo "đã kết nối lại".

Chỉ dùng thư viện chuẩn Python. Cấu hình bằng biến môi trường:
  WATCHDOG_KEY        khóa bí mật, trùng với key trong watchdog_heartbeat_url
  ZALO_SEND_URL       https://bot-api.zaloplatforms.com/bot<TOKEN>/sendMessage
  ZALO_CHAT_IDS       danh sách chat_id, cách nhau bởi dấu phẩy
  TIMEOUT_MIN         mặc định 5
  PORT                mặc định 8080 (đặt sau nginx/caddy có HTTPS)
"""
import hmac
import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

KEY = os.environ["WATCHDOG_KEY"]
SEND_URL = os.environ["ZALO_SEND_URL"]
CHAT_IDS = [c.strip() for c in os.environ["ZALO_CHAT_IDS"].split(",") if c.strip()]
TIMEOUT = int(os.environ.get("TIMEOUT_MIN", "5")) * 60
PORT = int(os.environ.get("PORT", "8080"))

last_seen = {}  # site -> epoch giây
down = set()
lock = threading.Lock()


def send_zalo(text):
    for chat_id in CHAT_IDS:
        body = json.dumps({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(SEND_URL, body, {"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=15).read()
        except Exception as err:  # không để lỗi gửi làm chết watchdog
            print("Gửi Zalo lỗi:", err, flush=True)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        url = urlparse(self.path)
        key = parse_qs(url.query).get("key", [""])[0]
        if not url.path.startswith("/hb/") or not hmac.compare_digest(key, KEY):
            self.send_response(403)
            self.end_headers()
            return
        site = url.path[4:]
        with lock:
            last_seen[site] = time.time()
            recovered = site in down
            down.discard(site)
        if recovered:
            send_zalo(f"[SmartLab {site}] ✅ Máy chủ tại khoa đã kết nối lại lúc {time.strftime('%H:%M %d/%m')}.")
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


def checker():
    while True:
        time.sleep(30)
        now = time.time()
        with lock:
            lost = [s for s, t in last_seen.items() if now - t > TIMEOUT and s not in down]
            down.update(lost)
        for site in lost:
            send_zalo(
                f"[SmartLab {site}] 🚨 MẤT KẾT NỐI với máy chủ tại khoa hơn {TIMEOUT // 60} phút "
                "(mất mạng / mất điện kéo dài / máy chủ treo). Cảnh báo nhiệt độ tạm thời KHÔNG gửi được – "
                "đề nghị kiểm tra tủ lạnh trực tiếp."
            )


if __name__ == "__main__":
    os.environ.setdefault("TZ", "Asia/Ho_Chi_Minh")
    time.tzset()
    threading.Thread(target=checker, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
