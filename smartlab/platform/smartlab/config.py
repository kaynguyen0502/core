"""Cấu hình đọc từ biến môi trường (file .env khi chạy Docker)."""
import os


def _env(name, default=""):
    return os.environ.get(name, default)


DATABASE_URL = _env("SMARTLAB_DB_URL", "sqlite:///./smartlab.db")
MQTT_HOST = _env("SMARTLAB_MQTT_HOST", "localhost")
MQTT_PORT = int(_env("SMARTLAB_MQTT_PORT", "1883"))
MQTT_USER = _env("SMARTLAB_MQTT_USER")
MQTT_PASSWORD = _env("SMARTLAB_MQTT_PASSWORD")
# Các nhánh topic cần nghe
MQTT_SUBSCRIBE = [t.strip() for t in _env("SMARTLAB_MQTT_SUBSCRIBE", "zigbee2mqtt/#,smartlab/#").split(",") if t.strip()]

SITE_NAME = _env("SMARTLAB_SITE_NAME", "Khoa Hóa Sinh · BVĐK Đà Nẵng")
TIMEZONE = _env("SMARTLAB_TZ", "Asia/Ho_Chi_Minh")

ZALO_SEND_URL = _env("SMARTLAB_ZALO_SEND_URL")          # https://bot-api.zaloplatforms.com/bot<TOKEN>/sendMessage
ZALO_CHAT_IDS = [c.strip() for c in _env("SMARTLAB_ZALO_CHAT_IDS").split(",") if c.strip()]
WATCHDOG_URL = _env("SMARTLAB_WATCHDOG_URL")            # nhịp tim ra máy chủ giám sát ngoài bệnh viện
REMIND_MINUTES = int(_env("SMARTLAB_REMIND_MINUTES", "30"))

ADMIN_USER = _env("SMARTLAB_ADMIN_USER", "admin")
ADMIN_PASSWORD = _env("SMARTLAB_ADMIN_PASSWORD", "")    # bắt buộc đặt khi triển khai
