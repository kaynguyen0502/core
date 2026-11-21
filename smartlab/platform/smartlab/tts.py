"""Giọng đọc cảnh báo tiếng Việt: tạo SẴN file mp3 cho từng điểm đo (Google Cloud Chirp 3 HD hoặc FPT.AI),
lưu ở thư mục audio/.

Tivi / màn hình chỉ phát file có sẵn -> không cần tivi có giọng tiếng Việt, không cần Internet lúc báo động
(chỉ cần Internet lúc bấm "Tạo file giọng đọc" trên trang /cai-dat/am-thanh).
Câu đọc theo mẫu, có {ten} (tên điểm đo) và {ma} (mã điểm đo); không đọc số đo (số đo thay đổi liên tục).
"""
import base64
import hashlib
import json
import os
import re
import time

import httpx
from sqlalchemy import select

from . import config, settings
from .models import Point

FPT_URL = "https://api.fpt.ai/hmi/tts/v5"
GOOGLE_TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
GOOGLE_RATE = {-2: 0.8, -1: 0.9, 0: 1.0, 1: 1.1, 2: 1.2}
_google_token = {"value": "", "exp": 0, "who": ""}
NAME_RE = re.compile(r"^[0-9a-f]{20}\.mp3$")


def fill(template, point):
    return (template or "").replace("{ten}", point.name).replace("{ma}", point.code).strip()


def clip_name(text, voice, speed):
    return hashlib.sha1(f"{voice}|{speed}|{text}".encode("utf-8")).hexdigest()[:20] + ".mp3"


def fpt_tts(text, key, voice, speed=0, wait_s=40, client=None):
    """Gọi FPT.AI TTS v5 -> bytes mp3. FPT trả về link file, file sẵn sàng sau vài giây."""
    c = client or httpx.Client(timeout=30)
    r = c.post(FPT_URL, content=text.encode("utf-8"),
               headers={"api-key": key, "voice": voice, "speed": str(speed)})
    data = r.json()
    if data.get("error") not in (0, None) or not data.get("async"):
        raise RuntimeError(data.get("message") or f"FPT.AI lỗi (HTTP {r.status_code})")
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        f = c.get(data["async"])
        if f.status_code == 200 and f.content[:3] in (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
            return f.content
        time.sleep(2)
    raise RuntimeError("FPT.AI chưa tạo xong file sau 40 giây, thử lại sau")


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=")


def google_token(creds_json, client=None):
    """Đổi khóa service account lấy access token (JWT RS256), dùng lại tới gần hết hạn."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    try:
        c = json.loads(creds_json)
        email, pem, uri = c["client_email"], c["private_key"], c.get("token_uri", "https://oauth2.googleapis.com/token")
    except (ValueError, KeyError, TypeError) as e:
        raise RuntimeError("File khóa Google không đúng (cần file JSON service account)") from e
    now = int(time.time())
    if _google_token["who"] == email and _google_token["exp"] > now + 120:
        return _google_token["value"]
    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64(json.dumps({"iss": email, "scope": "https://www.googleapis.com/auth/cloud-platform",
                              "aud": uri, "iat": now, "exp": now + 3600}).encode())
    key = serialization.load_pem_private_key(pem.encode(), None)
    sig = _b64(key.sign(head + b"." + claims, padding.PKCS1v15(), hashes.SHA256()))
    r = (client or httpx).post(uri, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                          "assertion": (head + b"." + claims + b"." + sig).decode()}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Google từ chối khóa ({r.status_code}): {r.text[:200]}")
    _google_token.update(value=r.json()["access_token"], exp=now + 3500, who=email)
    return _google_token["value"]


def google_tts(text, creds_json, voice, speed=0, client=None):
    """Google Cloud Text-to-Speech -> bytes mp3."""
    token = google_token(creds_json, client)
    body = {"input": {"text": text}, "voice": {"languageCode": "vi-VN", "name": voice},
            "audioConfig": {"audioEncoding": "MP3"}}
    if GOOGLE_RATE.get(speed, 1.0) != 1.0:
        body["audioConfig"]["speakingRate"] = GOOGLE_RATE[speed]
    for attempt in range(4):
        r = (client or httpx).post(GOOGLE_TTS_URL, json=body, headers={"Authorization": "Bearer " + token}, timeout=40)
        # vừa bật API: vài phút đầu Google lúc nhận lúc báo 403 -> chờ rồi thử lại
        if r.status_code == 403 and "has not been used" in r.text and attempt < 3:
            time.sleep(10)
            continue
        break
    if r.status_code != 200:
        try:
            msg = r.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            msg = r.text[:200]
        if "has not been used" in msg or "disabled" in msg:
            msg = "Dự án Google Cloud chưa bật Text-to-Speech API (vào console.cloud.google.com → APIs → bật Cloud Text-to-Speech API)"
        raise RuntimeError(f"Google TTS lỗi {r.status_code}: {msg}")
    return base64.b64decode(r.json()["audioContent"])


def google_account(creds_json):
    try:
        return json.loads(creds_json).get("client_email", "")
    except (ValueError, AttributeError):
        return ""


def _engine(cfg):
    """(tên engine, giọng) dùng để tạo file theo cấu hình."""
    if cfg["voice"] == "google":
        return "google", cfg["google_voice"]
    return "fpt", cfg["fpt_voice"]


def wanted(session):
    """{mã điểm: {"alarm": (câu, tên file), "offline": (...)}} theo cấu hình hiện tại."""
    cfg = settings.get(session, "sound")
    out = {}
    for p in session.scalars(select(Point).where(Point.active.is_(True), Point.group != "analyzer")):
        out[p.code] = {}
        for kind, tpl in (("alarm", cfg["alarm_text"]), ("offline", cfg["offline_text"])):
            text = fill(tpl, p)
            if text:
                eng, voice = _engine(cfg)
                out[p.code][kind] = (text, clip_name(text, f"{eng}:{voice}", cfg["speed"]))
    return out


def clips(session, base_dir=None):
    """Các file giọng đọc ĐÃ có: {mã: {"alarm": "/audio/x.mp3", ...}}."""
    base_dir = base_dir or config.AUDIO_DIR
    out = {}
    for code, kinds in wanted(session).items():
        for kind, (_, name) in kinds.items():
            if os.path.isfile(os.path.join(base_dir, name)):
                out.setdefault(code, {})[kind] = f"/audio/{name}"
    return out


def build(session, base_dir=None, tts=None):
    """Tạo các file còn thiếu theo giọng đang chọn. Trả về (số file mới, số đã có, [lỗi])."""
    cfg = settings.get(session, "sound")
    eng, voice = _engine(cfg)
    if eng == "google" and not cfg["google_credentials"]:
        return 0, 0, ["Chưa tải lên file khóa Google Cloud (JSON)"]
    if eng == "fpt" and not cfg["fpt_key"]:
        return 0, 0, ["Chưa nhập API key FPT.AI"]
    if tts is None:
        tts = google_tts if eng == "google" else fpt_tts
    secret = cfg["google_credentials"] if eng == "google" else cfg["fpt_key"]
    base_dir = base_dir or config.AUDIO_DIR
    os.makedirs(base_dir, exist_ok=True)
    made, have, errors, done = 0, 0, [], set()
    for code, kinds in wanted(session).items():
        for kind, (text, name) in kinds.items():
            path = os.path.join(base_dir, name)
            if name in done or os.path.isfile(path):
                have += 1
                continue
            try:
                data = tts(text, secret, voice, cfg["speed"])
            except (httpx.HTTPError, RuntimeError, ValueError) as e:
                errors.append(f"{code}: {e}")
                if len(errors) >= 3 or "chưa bật" in str(e) or "từ chối" in str(e):
                    return made, have, errors + ["Dừng lại"]
                continue
            with open(path + ".tmp", "wb") as f:
                f.write(data)
            os.replace(path + ".tmp", path)
            done.add(name)
            made += 1
    return made, have, errors


def duplicates(session):
    """Các nhóm điểm đo có câu đọc vượt ngưỡng GIỐNG NHAU (nghe không phân biệt được tủ nào)."""
    by_text = {}
    for code, kinds in wanted(session).items():
        if "alarm" in kinds:
            by_text.setdefault(kinds["alarm"][0], []).append(code)
    return [codes for codes in by_text.values() if len(codes) > 1]
