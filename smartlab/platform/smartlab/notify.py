"""Thông báo Zalo (giống hệ thống Huyết học – Truyền máu) và nhịp tim watchdog.

API của máy chủ Zalo (header X-Api-Key):
    POST {base}/api/public/messages/send  {zaloAccountId, threadId, threadType, content}
Tìm nick / người nhận (đăng nhập 1 lần, KHÔNG lưu mật khẩu):
    POST {base}/api/v1/auth/login -> token ; GET /api/v1/zalo-accounts ; GET /api/v1/conversations
Cấu hình ở trang /cai-dat/zalo. Tiến trình nền gửi qua hàng đợi (thử lại 3 lần, giãn cách 2 giây/tin).
"""
import logging
import queue
import threading
import time

import httpx

from . import config, settings

log = logging.getLogger("smartlab.notify")
ICON = {"alarm": "🔴", "reminder": "🔴", "normal": "🟢", "offline": "⚠️", "online": "🟢"}
_queue = queue.Queue(maxsize=200)
_thread = None


# ---------------------------------------------------------------- gửi
def _api_err(r):
    try:
        d = r.json()
        if isinstance(d, dict) and isinstance(d.get("error"), str):
            return d["error"]
    except ValueError:
        pass
    return r.text.strip()[:200]


def send_one(z, rcp, text, client=None):
    """Gửi 1 tin tới 1 người nhận. Lỗi -> RuntimeError với thông điệp dễ hiểu."""
    if not (z["base_url"] and z["api_key"] and z["account_id"]):
        raise RuntimeError("chưa cấu hình đủ Zalo")
    body = {"zaloAccountId": z["account_id"], "threadId": rcp["thread_id"],
            "threadType": rcp.get("thread_type") or "user", "content": text}
    try:
        r = (client or httpx).post(z["base_url"].rstrip("/") + "/api/public/messages/send", json=body,
                                   headers={"X-Api-Key": z["api_key"]}, timeout=15)
    except httpx.HTTPError as e:
        raise RuntimeError(f"không kết nối được Zalo: {e}") from e
    if r.status_code // 100 != 2:
        raise RuntimeError(f"Zalo trả lỗi {r.status_code}: {_api_err(r)}")


def label(rcp):
    name = rcp.get("name") or rcp["thread_id"]
    if rcp.get("thread_type") == "group" and not name.lower().startswith("nhóm"):
        return "Nhóm " + name
    return name


def _log(to, text, err):
    from . import db
    from .models import ZaloLog
    from .service import utcnow
    try:
        with db.SessionLocal() as s:
            s.add(ZaloLog(ts=utcnow(), to=to[:120], text=text, ok=err is None, error=(err or "")[:255]))
            s.commit()
    except Exception:  # noqa: BLE001 - ghi nhật ký lỗi không được làm dừng việc gửi
        log.exception("Không ghi được nhật ký Zalo")
    if err:
        log.error("Zalo: gửi tới %s thất bại: %s", to, err)


def deliver(z, text, only=None, attempts=3, pause=2.0, backoff=5.0):
    """Gửi text tới mọi người nhận (hoặc chỉ `only`). Trả về [{"to", "ok", "error"}]."""
    out = []
    targets = [only] if only else z["recipients"]
    for i, rcp in enumerate(targets):
        err = None
        for a in range(attempts):
            try:
                send_one(z, rcp, text)
                err = None
                break
            except RuntimeError as e:
                err = str(e)
                if a + 1 < attempts:
                    time.sleep(backoff * (a + 1))
        _log(label(rcp), text, err)
        out.append({"to": label(rcp), "ok": err is None, "error": err})
        if i + 1 < len(targets):
            time.sleep(pause)                 # giãn cách, tránh Zalo coi là spam
    return out


def _worker():
    while True:
        z, text = _queue.get()
        try:
            deliver(z, text)
        except Exception:  # noqa: BLE001
            log.exception("Lỗi gửi Zalo")


def start_sender():
    """Gọi 1 lần trong tiến trình nền (worker)."""
    global _thread
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_worker, name="zalo-sender", daemon=True)
        _thread.start()


def _enqueue(z, text):
    if _thread is None:                       # không có luồng gửi (vd kiểm thử) -> gửi luôn
        deliver(z, text, attempts=1, pause=0)
        return
    try:
        _queue.put_nowait((z, text))
    except queue.Full:
        log.error("Zalo: hàng đợi gửi đầy, bỏ tin: %s", text)


# ---------------------------------------------------------------- nội dung
def compose(z, kind, message, when_utc):
    from .records import to_local
    lines = [f"{ICON.get(kind, '')} {message}".strip(), "Lúc: " + to_local(when_utc).strftime("%H:%M %d/%m/%Y")]
    if z.get("dashboard_url"):
        lines.append("Xem: " + z["dashboard_url"].rstrip("/") + "/tv")
    return "\n".join(lines)


def send_events(events, session=None):
    """Đưa các sự kiện vào hàng đợi gửi Zalo theo công tắc trên trang cấu hình."""
    if not events:
        return
    if session is None:
        from sqlalchemy.orm import object_session
        session = object_session(events[0])
    z = settings.get(session, "zalo")
    if not z["enabled"] or settings.zalo_ready(z):
        return
    for ev in events:
        if not z.get(settings.KIND_SWITCH.get(ev.kind, ""), False):
            continue
        if ev.kind == "reminder" and not z["repeat_minutes"]:
            continue
        _enqueue(z, compose(z, ev.kind, ev.message, ev.ts))
        ev.notified = True


def send_test(session, only=None):
    z = settings.get(session, "zalo")
    reason = settings.zalo_ready(z) if not only else ("" if z["base_url"] and z["api_key"] and z["account_id"]
                                                      else "Cần lưu địa chỉ Zalo, API key và nick Zalo gửi trước")
    if reason:
        raise RuntimeError(reason)
    from .records import to_local
    from .service import utcnow
    text = (f"✅ {config.SITE_NAME}\nTin nhắn thử: thông báo Zalo đã hoạt động.\n"
            f"Lúc {to_local(utcnow()).strftime('%H:%M %d/%m/%Y')}")
    return deliver(z, text, only=only, attempts=1)


# ---------------------------------------------------------------- tìm nick / người nhận
def lookup(base_url, email, password, account_id="", search="", thread_type="", client=None):
    base_url = (base_url or "").strip().rstrip("/")
    if not (base_url and email and password):
        raise RuntimeError("Cần địa chỉ Zalo, email/SĐT và mật khẩu đăng nhập Zalo")
    c = client or httpx.Client(timeout=20)
    try:
        r = c.post(base_url + "/api/v1/auth/login", json={"identifier": email, "email": email, "password": password})
    except httpx.HTTPError as e:
        raise RuntimeError(f"không kết nối được Zalo: {e}") from e
    if r.status_code != 200:
        raise RuntimeError(f"đăng nhập Zalo thất bại ({r.status_code}): {_api_err(r)}")
    token = (r.json() or {}).get("token")
    if not token:
        raise RuntimeError("Zalo không trả token")
    h = {"Authorization": "Bearer " + token}

    def get(path, params=None):
        g = c.get(base_url + path, params=params, headers=h)
        if g.status_code != 200:
            raise RuntimeError(f"{path}: {g.status_code} {_api_err(g)}")
        return g.json()

    accounts = [{"id": a.get("id"), "displayName": a.get("displayName"), "phone": a.get("phone"),
                 "status": a.get("status")} for a in get("/api/v1/zalo-accounts")]
    params = {"limit": 200}
    if account_id:
        params["accountId"] = account_id
    if search:
        params["search"] = search
    if thread_type in ("user", "group"):
        params["threadType"] = thread_type
    threads = []
    for cv in get("/api/v1/conversations", params).get("conversations", []):
        ct = cv.get("contact") or {}
        name = cv.get("groupName") or ct.get("crmName") or ct.get("fullName") or ""
        threads.append({"threadId": cv.get("externalThreadId"), "threadType": cv.get("threadType"),
                        "zaloAccountId": cv.get("zaloAccountId"), "name": name})
    return {"accounts": accounts, "threads": threads}


def heartbeat():
    if not config.WATCHDOG_URL:
        return
    try:
        httpx.post(config.WATCHDOG_URL, timeout=10)
    except httpx.HTTPError as err:
        log.warning("Nhịp tim watchdog lỗi: %s", err)
