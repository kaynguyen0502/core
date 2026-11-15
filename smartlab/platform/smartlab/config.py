"""Cấu hình đọc từ biến môi trường (file .env khi chạy Docker)."""
import os


def _load_env_file():
    """Đọc file .env (chạy trực tiếp trên Windows, không qua Docker).

    Biến đã có sẵn trong môi trường (Docker env_file) được giữ nguyên.
    """
    path = os.environ.get("SMARTLAB_ENV_FILE") or os.path.join(os.getcwd(), ".env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


_load_env_file()


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

# Sổ theo dõi ISO 15189: các mốc ghi sổ trong ngày (giờ Việt Nam)
LOG_TIMES = [t.strip() for t in _env("SMARTLAB_LOG_TIMES", "07:00,13:00").split(",") if t.strip()]
# Thư mục xuất dữ liệu tự động hằng tháng
EXPORT_DIR = _env("SMARTLAB_EXPORT_DIR", os.path.join(os.getcwd(), "exports"))

ADMIN_USER = _env("SMARTLAB_ADMIN_USER", "admin")
ADMIN_PASSWORD = _env("SMARTLAB_ADMIN_PASSWORD", "")    # bắt buộc đặt khi triển khai
