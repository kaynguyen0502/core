"""Cấu hình sửa trên web: Zalo và âm thanh cảnh báo. Lưu trong bảng settings, sửa có ghi nhật ký (AuditLog)."""
import copy
import re

from .models import AuditLog, Setting

DEFAULTS = {
    "zalo": {
        "token": "",                      # token Zalo Bot (bot-api.zaloplatforms.com)
        "recipients": [],                 # [{"id": "...", "name": "Nhóm khoa HS", "on": true}]
        "kinds": {"alarm": True, "reminder": True, "normal": True, "offline": True, "online": True},
        "prefix": "[SmartLab HS]",
        "remind_minutes": 30,
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
SECRETS = {"zalo": ("token",), "sound": ("fpt_key",)}
KINDS = {"alarm": "Vượt ngưỡng", "reminder": "Nhắc lại khi vẫn vượt ngưỡng", "normal": "Trở lại bình thường",
         "offline": "Mất tín hiệu", "online": "Có tín hiệu trở lại"}
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
    """Lưu cấu hình. Ô bí mật để trống = giữ giá trị cũ. Ghi nhật ký (không ghi giá trị bí mật)."""
    old = get(session, key)
    new = _merge(old, value)
    for s in SECRETS.get(key, ()):
        if not (value.get(s) or "").strip():
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


def zalo_effective(session):
    """Cấu hình Zalo dùng để gửi: trang web; nếu chưa đặt token trên web thì lấy từ .env (bản cũ)."""
    from . import config
    z = get(session, "zalo")
    if not z["token"] and config.ZALO_SEND_URL:
        m = re.search(r"/bot([^/]+)/", config.ZALO_SEND_URL)
        if m:
            z["token"] = m.group(1)
        if not z["recipients"]:
            z["recipients"] = [{"id": c, "name": c, "on": True} for c in config.ZALO_CHAT_IDS]
    return z
