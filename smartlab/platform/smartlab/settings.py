"""Cấu hình sửa trên web: Zalo và âm thanh cảnh báo. Lưu trong bảng settings, sửa có ghi nhật ký (AuditLog)."""
import copy
import re

from .models import AuditLog, Setting

DEFAULTS = {
    "zalo": {                             # gửi qua máy chủ Zalo (giống hệ thống Huyết học – Truyền máu)
        "enabled": False,
        "base_url": "",                   # vd https://zalo.ten-mien.vn hoặc http://192.168.1.20:3000
        "api_key": "",                    # trang quản lý Zalo: Cài đặt → API & Webhook
        "account_id": "",                 # zaloAccountId của nick Zalo gửi tin
        "account_name": "",
        "dashboard_url": "",              # link gắn cuối tin
        "recipients": [],                 # [{"thread_id": "...", "thread_type": "group|user", "name": "..."}]
        "on_alarm": True,                 # vượt ngưỡng (+ nhắc lại)
        "on_alarm_end": True,             # trở lại bình thường
        "on_offline": True,               # mất tín hiệu / có tín hiệu lại
        "repeat_minutes": 30,             # nhắc lại khi vẫn vượt ngưỡng; 0 = không nhắc
    },
    "sound": {
        "default_on": False,              # mọi màn hình dashboard tự hỏi bật âm thanh khi mở
        "chime": "3",                     # "3" = 3 hồi, "1" = 1 hồi, "0" = không chuông (chỉ giọng đọc)
        "volume": 70,                     # %
        "repeat_minutes": 5,
        "mute_minutes": 30,
        "offline_chime": True,
        "voice": "off",                   # off | browser | fpt
        "fpt_key": "",
        "fpt_voice": "myan",
        "speed": 0,                       # -3 (chậm) .. 3 (nhanh)
        "alarm_text": "Cảnh báo. {ten} vượt ngưỡng. Đề nghị kiểm tra ngay.",
        "offline_text": "Chú ý. {ten} mất tín hiệu.",
    },
}
SECRETS = {"zalo": ("api_key",), "sound": ("fpt_key",)}
# loại sự kiện -> công tắc trên trang cấu hình Zalo
KIND_SWITCH = {"alarm": "on_alarm", "reminder": "on_alarm", "normal": "on_alarm_end",
               "offline": "on_offline", "online": "on_offline"}
FPT_VOICES = {"myan": "Mỹ An – nữ miền Trung", "giahuy": "Gia Huy – nam miền Trung",
              "ngoclam": "Ngọc Lam – nữ miền Trung", "banmai": "Ban Mai – nữ miền Bắc",
              "thuminh": "Thu Minh – nữ miền Bắc", "leminh": "Lê Minh – nam miền Bắc",
              "lannhi": "Lan Nhi – nữ miền Nam", "linhsan": "Linh San – nữ miền Nam",
              "minhquang": "Minh Quang – nam miền Nam"}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def get(session, key):
    row = session.get(Setting, key)
    return _merge(DEFAULTS[key], row.value if row else {})


def put(session, key, value, user="system"):
    """Lưu cấu hình. Ô bí mật để trống (hoặc còn dạng che ••••) = giữ giá trị cũ. Ghi nhật ký (không ghi bí mật)."""
    old = get(session, key)
    new = _merge(old, value)
    for s in SECRETS.get(key, ()):
        v = (value.get(s) or "").strip()
        if not v or v.startswith("•"):
            new[s] = old[s]
    changed = sorted(k for k in new if new[k] != old.get(k))
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=new))
    else:
        row.value = new
    if changed:
        from .service import utcnow
        session.add(AuditLog(ts=utcnow(), user=user, action=f"cấu hình {key}", detail="Đổi: " + ", ".join(changed)))
    session.commit()
    return new


def mask(secret):
    secret = secret or ""
    if not secret:
        return ""
    return "•" * 6 + (secret[-4:] if len(secret) >= 12 else "")      # khóa ngắn: không lộ ký tự nào


def zalo_ready(z):
    """Lý do chưa gửi được (chuỗi rỗng = đủ cấu hình)."""
    if not (z["base_url"] and z["api_key"] and z["account_id"]):
        return "Cần lưu địa chỉ Zalo, API key và nick Zalo gửi trước"
    if not z["recipients"]:
        return "Chưa có người nhận"
    return ""
