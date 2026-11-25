"""Web: dashboard TV (/tv), API trạng thái, trang danh mục điểm đo (/catalog).

Chạy: uvicorn smartlab.web:app --host 0.0.0.0 --port 8000
"""
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from fastapi import Request
from sqlalchemy import select

from datetime import date, datetime, timedelta

from . import alarms, api_poll, config, notify, records, service, settings, simulate, tts, tv_auto, tv_remote
from . import db as dbmod
from .db import init_db
from .models import GROUPS, SOURCES, AlarmEvent, AuditLog, Point, PointState, ZaloLog

@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield


app = FastAPI(title="SmartLab", lifespan=lifespan)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
security = HTTPBasic()
TZ = ZoneInfo(config.TIMEZONE)


def db():
    with dbmod.SessionLocal() as s:   # lấy lúc chạy (kiểm thử có thể thay CSDL)
        yield s


def admin(creds: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(creds.username, config.ADMIN_USER)
    ok_pass = bool(config.ADMIN_PASSWORD) and secrets.compare_digest(creds.password, config.ADMIN_PASSWORD)
    if not (ok_user and ok_pass):
        raise HTTPException(401, "Sai tài khoản", headers={"WWW-Authenticate": "Basic"})
    return creds.username


def local(dt):
    return dt.replace(tzinfo=timezone.utc).astimezone(TZ) if dt else None


def point_view(s, p, st, now):
    st_state = st.state if st else "offline"
    vals = (st.values if st else {}) or {}
    v = {
        "code": p.code, "name": p.name, "group": p.group, "group_label": GROUPS.get(p.group, p.group),
        "source": p.source, "source_label": SOURCES.get(p.source, p.source), "location": p.location,
        "state": st_state, "message": st.message if st else "Chưa có dữ liệu",
        "last_seen": local(st.last_seen).strftime("%H:%M") if st and st.last_seen else None,
        "values": vals, "extra": (st.extra if st else {}) or {},
    }
    if p.group in ("fridge", "freezer"):
        v["display"] = alarms.fmt(vals.get("temperature"))
        v["range"] = alarms.range_text(p.temp_min, p.temp_max, "temperature")
        v["spark"] = service.sparkline(s, p.id, "temperature", now=now)
    elif p.group == "room":
        v["display"] = alarms.fmt(vals.get("temperature"))
        v["display2"] = alarms.fmt(vals.get("humidity"), 0)
        v["range"] = (alarms.range_text(p.temp_min, p.temp_max, "temperature") + " · "
                      + alarms.range_text(p.hum_min, p.hum_max, "humidity"))
    elif p.group == "ro":
        v["display"] = alarms.fmt(vals.get("ec"), 2)
        v["range"] = alarms.range_text(None, p.ec_max, "ec")
    if st_state == "offline":
        v["display"] = "--"
        v["display2"] = "--"
    return v


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/tv")


@app.get("/api/status")
def api_status(s=Depends(db)):
    now = service.utcnow()
    points = s.scalars(select(Point).where(Point.active.is_(True)).order_by(Point.sort, Point.code)).all()
    states = {st.point_id: st for st in s.scalars(select(PointState))}
    items = [point_view(s, p, states.get(p.id), now) for p in points]
    active = [i for i in items if i["state"] in ("alarm", "offline")]
    return {
        "site": config.SITE_NAME,
        "now": local(now).isoformat(),
        "counts": service.counts_by_state(i["state"] for i in items),
        "total": len(items),
        "active_alarms": [{"code": i["code"], "name": i["name"], "state": i["state"], "message": i["message"]}
                          for i in active],
        "points": items,
    }


@app.get("/api/events")
def api_events(limit: int = 50, s=Depends(db)):
    rows = s.execute(select(AlarmEvent, Point.code).join(Point, Point.id == AlarmEvent.point_id)
                     .order_by(AlarmEvent.ts.desc()).limit(min(limit, 500))).all()
    return [{"ts": local(e.ts).isoformat(), "code": code, "kind": e.kind, "message": e.message,
             "notified": e.notified} for e, code in rows]


def _point(s, code):
    p = s.scalar(select(Point).where(Point.code == code))
    if p is None:
        raise HTTPException(404, "Không có điểm đo này")
    return p


@app.get("/api/history/{code}")
def api_history(code: str, days: int = 1, s=Depends(db)):
    """Lịch sử một điểm đo: 1, 7 hoặc 30 ngày gần nhất."""
    p = _point(s, code)
    days = max(1, min(days, 90))
    end = service.utcnow()
    start = end - timedelta(days=days)
    return {"code": p.code, "name": p.name, "group": p.group, "days": days,
            "limits": {"temp_min": p.temp_min, "temp_max": p.temp_max, "hum_min": p.hum_min,
                       "hum_max": p.hum_max, "ec_max": p.ec_max},
            "metrics": records.history(s, p.id, start, end)}


def _csv_response(text, filename):
    return Response(text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/export/readings.csv")
def export_readings(code: str = "", start: str = "", end: str = "", s=Depends(db)):
    """Số đo chi tiết. start/end dạng YYYY-MM-DD (giờ VN, end tính cả ngày). Bỏ trống code = mọi điểm."""
    today = records.to_local(service.utcnow()).date()
    d1 = records.parse_day(start, today - timedelta(days=6))
    d2 = records.parse_day(end, today)
    t1 = records.to_utc(datetime.combine(d1, datetime.min.time()))
    t2 = records.to_utc(datetime.combine(d2 + timedelta(days=1), datetime.min.time()))
    codes = [c for c in code.split(",") if c] or None
    name = f"so-do_{code or 'tat-ca'}_{d1}_{d2}.csv".replace(",", "-")
    return _csv_response(records.readings_csv(s, t1, t2, codes), name)


@app.get("/export/logbook.csv")
def export_logbook(month: str = "", s=Depends(db)):
    y, m = _month(month)
    return _csv_response(records.logbook_csv(s, y, m), f"so-theo-doi_{y:04d}-{m:02d}.csv")


def _month(text):
    today = records.to_local(service.utcnow()).date()
    try:
        y, m = (int(x) for x in text.split("-"))
        date(y, m, 1)
        return y, m
    except (ValueError, TypeError):
        return today.year, today.month


@app.get("/so-theo-doi", response_class=HTMLResponse)
def logbook_page(request: Request, month: str = "", s=Depends(db)):
    y, m = _month(month)
    slots, points, cells = records.logbook(s, y, m)
    prev = date(y, m, 1) - timedelta(days=1)
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    groups = [(GROUPS["fridge"] + " / " + GROUPS["freezer"], [p for p in points if p.group in ("fridge", "freezer")]),
              (GROUPS["room"], [p for p in points if p.group == "room"]),
              (GROUPS["ro"], [p for p in points if p.group == "ro"])]
    return templates.TemplateResponse(request, "logbook.html", {
        "site": config.SITE_NAME, "year": y, "month": m, "slots": slots, "groups": groups, "cells": cells,
        "local": records.to_local, "fmt": alarms.fmt, "range_text": alarms.range_text,
        "prev": f"{prev.year:04d}-{prev.month:02d}", "next": f"{nxt.year:04d}-{nxt.month:02d}",
        "log_times": ", ".join(config.LOG_TIMES)})


@app.get("/tv", response_class=HTMLResponse)
def tv(request: Request):
    return templates.TemplateResponse(request, "tv.html", {"site": config.SITE_NAME})


@app.get("/catalog", response_class=HTMLResponse)
def catalog(request: Request, user=Depends(admin), s=Depends(db)):
    points = s.scalars(select(Point).order_by(Point.sort, Point.code)).all()
    log = s.scalars(select(AuditLog).order_by(AuditLog.ts.desc()).limit(30)).all()
    return templates.TemplateResponse(request, "catalog.html", {
        "points": points, "groups": GROUPS, "sources": SOURCES, "log": log, "local": local})


EDITABLE = ("name", "group", "source", "topic", "location", "temp_min", "temp_max", "hum_min", "hum_max",
            "ec_max", "alarm_delay_min", "offline_after_min", "sort", "active")


def _coerce(field, raw):
    raw = (raw or "").strip()
    if field in ("temp_min", "temp_max", "hum_min", "hum_max", "ec_max"):
        return float(raw.replace(",", ".")) if raw else None
    if field in ("alarm_delay_min", "offline_after_min", "sort"):
        return int(raw or 0)
    if field == "active":
        return raw in ("1", "on", "true")
    return raw


@app.post("/catalog/{code}")
async def catalog_save(code: str, request: Request, user=Depends(admin), s=Depends(db)):
    form = await request.form()
    p = s.scalar(select(Point).where(Point.code == code))
    creating = p is None
    if creating:
        p = Point(code=code.upper().strip())
        s.add(p)
    changes = []
    for f in EDITABLE:
        if f not in form and f != "active":
            continue
        try:
            new = _coerce(f, form.get(f))
        except ValueError:
            raise HTTPException(400, f"Giá trị không hợp lệ ở ô {f}")
        old = getattr(p, f, None)
        if creating or old != new:
            changes.append(f"{f}: {old!r} → {new!r}")
            setattr(p, f, new)
    if creating:
        s.flush()
    if changes:
        s.add(AuditLog(ts=service.utcnow(), user=user, action="tạo" if creating else "sửa",
                       detail=f"{p.code}: " + "; ".join(changes)))
    s.commit()
    if creating:
        service.ensure_states(s)
    return RedirectResponse("/catalog", status_code=303)


# ---------------------------------------------------------------- cấu hình trên web: Zalo, âm thanh
def _nav(request, active):
    return {"request": request, "active": active, "site": config.SITE_NAME}


@app.get("/cai-dat", include_in_schema=False)
def settings_home(user=Depends(admin)):
    return RedirectResponse("/cai-dat/zalo")


@app.get("/cai-dat/zalo", response_class=HTMLResponse)
def zalo_page(request: Request, user=Depends(admin)):
    return templates.TemplateResponse(request, "settings_zalo.html", _nav(request, "zalo"))


def _zalo_public(z):
    return {**z, "api_key": settings.mask(z["api_key"])}


def _bad(msg, code=400):
    return JSONResponse({"error": msg}, status_code=code)


@app.get("/api/notify/settings")
def notify_settings(user=Depends(admin), s=Depends(db)):
    return _zalo_public(settings.get(s, "zalo"))


@app.post("/api/notify/settings")
async def notify_settings_save(request: Request, user=Depends(admin), s=Depends(db)):
    try:
        b = await request.json()
        assert isinstance(b, dict)
    except Exception:  # noqa: BLE001
        return _bad("Dữ liệu không hợp lệ")
    recipients = []
    for r in b.get("recipients") or []:
        tid = str(r.get("thread_id") or "").strip()
        if tid:
            recipients.append({"thread_id": tid, "thread_type": "group" if r.get("thread_type") == "group" else "user",
                               "name": str(r.get("name") or "").strip()[:80]})
    try:
        repeat = max(0, min(int(b.get("repeat_minutes") or 0), 600))
    except (TypeError, ValueError):
        repeat = 30
    new = {"enabled": bool(b.get("enabled")),
           "base_url": str(b.get("base_url") or "").strip().rstrip("/"),
           "api_key": str(b.get("api_key") or "").strip(),
           "account_id": str(b.get("account_id") or "").strip(),
           "account_name": str(b.get("account_name") or "").strip()[:80],
           "dashboard_url": str(b.get("dashboard_url") or "").strip().rstrip("/"),
           "recipients": recipients,
           "on_alarm": bool(b.get("on_alarm")), "on_alarm_end": bool(b.get("on_alarm_end")),
           "on_offline": bool(b.get("on_offline")), "repeat_minutes": repeat}
    if new["enabled"]:
        trial = {**settings.get(s, "zalo"), **{k: v for k, v in new.items() if k != "api_key" or (v and not v.startswith("•"))}}
        if settings.zalo_ready(trial):
            return _bad("Bật thông báo cần đủ: địa chỉ Zalo, API key, nick Zalo gửi và ít nhất 1 người nhận")
    return _zalo_public(settings.put(s, "zalo", new, user))


@app.post("/api/notify/test")
async def notify_test(request: Request, user=Depends(admin), s=Depends(db)):
    try:
        b = await request.json()
    except Exception:  # noqa: BLE001
        b = {}
    r = (b or {}).get("recipient")
    only = None
    if isinstance(r, dict) and str(r.get("thread_id") or "").strip():
        only = {"thread_id": str(r["thread_id"]).strip(), "name": str(r.get("name") or ""),
                "thread_type": "group" if r.get("thread_type") == "group" else "user"}
    try:
        return {"results": notify.send_test(s, only)}
    except RuntimeError as e:
        return _bad(str(e))


@app.post("/api/notify/lookup")
async def notify_lookup(request: Request, user=Depends(admin)):
    try:
        b = await request.json()
        return notify.lookup(b.get("base_url"), b.get("email"), b.get("password"), b.get("account_id") or "",
                             b.get("search") or "", b.get("thread_type") or "")
    except RuntimeError as e:
        return _bad(str(e))
    except Exception:  # noqa: BLE001
        return _bad("Dữ liệu không hợp lệ")


@app.get("/api/notify/logs")
def notify_logs(user=Depends(admin), s=Depends(db)):
    rows = s.scalars(select(ZaloLog).order_by(ZaloLog.id.desc()).limit(100)).all()
    return {"logs": [{"at": local(r.ts).strftime("%d/%m/%Y %H:%M:%S"), "to": r.to, "text": r.text, "ok": r.ok,
                      "error": r.error} for r in rows]}


# ---------------------------------------------------------------- API thiết bị
def _api_points(s):
    return s.scalars(select(Point).where(Point.group.in_(list(api_poll.METRIC))).order_by(Point.sort, Point.code)).all()


@app.get("/cai-dat/api", response_class=HTMLResponse)
def api_page(request: Request, user=Depends(admin), s=Depends(db)):
    return templates.TemplateResponse(request, "settings_api.html", {**_nav(request, "api"), "points": _api_points(s)})


@app.get("/api/sources")
def api_sources(user=Depends(admin), s=Depends(db)):
    return {"sources": [{**src, "token": settings.mask(src.get("token", ""))} for src in settings.get(s, "api")["sources"]]}


@app.post("/api/sources")
async def api_sources_save(request: Request, user=Depends(admin), s=Depends(db)):
    try:
        b = await request.json()
        rows = b["sources"]
        assert isinstance(rows, list)
    except Exception:  # noqa: BLE001
        return _bad("Dữ liệu không hợp lệ")
    codes = {p.code for p in _api_points(s)}
    old = {x["code"]: x for x in settings.get(s, "api")["sources"]}
    out, seen = [], set()
    for r in rows:
        code = str(r.get("code") or "").strip().upper()
        url = str(r.get("url") or "").strip()
        if not code and not url:
            continue
        if code not in codes:
            return _bad(f"Mã điểm đo không đúng: {code or '(trống)'}")
        if code in seen:
            return _bad(f"{code} bị khai báo 2 lần")
        if not url.startswith(("http://", "https://")):
            return _bad(f"{code}: URL phải bắt đầu bằng http:// hoặc https://")
        seen.add(code)
        token = str(r.get("token") or "").strip()
        if not token or token.startswith("•"):
            token = old.get(code, {}).get("token", "")      # để trống / còn che = giữ token cũ
        try:
            every = max(1, min(int(r.get("every_min") or 5), 120))
        except (TypeError, ValueError):
            every = 5
        out.append({"code": code, "url": url, "token": token, "path": str(r.get("path") or "").strip(), "every_min": every})
    settings.put(s, "api", {"sources": out}, user)
    return api_sources(user, s)


@app.post("/api/sources/test/{code}")
def api_source_test(code: str, user=Depends(admin), s=Depends(db)):
    src = next((x for x in settings.get(s, "api")["sources"] if x["code"] == code), None)
    if src is None:
        return _bad("Chưa lưu nguồn này")
    try:
        data = api_poll.fetch(src)
    except RuntimeError as e:
        return _bad(str(e))
    raw = data if isinstance(data, str) else __import__("json").dumps(data, ensure_ascii=False, indent=2)
    try:
        return {"raw": raw[:4000], "value": api_poll.extract(data, src.get("path", ""))}
    except RuntimeError as e:
        return {"raw": raw[:4000], "value": None, "error": str(e)}


# ---------------------------------------------------------------- giả lập
@app.get("/cai-dat/gia-lap", response_class=HTMLResponse)
def sim_page(request: Request, msg: str = "", user=Depends(admin), s=Depends(db)):
    pts = s.scalars(select(Point).where(Point.group.in_(list(simulate.METRIC))).order_by(Point.sort, Point.code)).all()
    return templates.TemplateResponse(request, "settings_sim.html", {
        **_nav(request, "gia-lap"), "points": pts, "vals": settings.get(s, "simulate")["values"], "msg": msg,
        "metric": simulate.METRIC, "groups": GROUPS})


@app.post("/cai-dat/gia-lap")
async def sim_save(request: Request, user=Depends(admin), s=Depends(db)):
    f = await request.form()
    codes = {p.code for p in s.scalars(select(Point).where(Point.group.in_(list(simulate.METRIC))))}
    vals = {}
    for code in codes:
        raw = (f.get(f"v_{code}") or "").strip().replace(",", ".")
        if raw:
            try:
                vals[code] = float(raw)
            except ValueError:
                return RedirectResponse(f"/cai-dat/gia-lap?msg=Giá trị của {code} không phải số", status_code=303)
    settings.put(s, "simulate", {"values": vals}, user)
    for st in s.scalars(select(PointState).join(Point, Point.id == PointState.point_id)
                        .where(Point.code.in_(list(codes - set(vals))))):
        if (st.extra or {}).get("simulated"):
            st.extra = {**st.extra, "simulated": False}
    s.commit()
    n, _ = simulate.inject(s)                           # áp dụng ngay, không chờ 5 phút
    return RedirectResponse(f"/cai-dat/gia-lap?msg=Đã lưu, đang giả lập {n} điểm", status_code=303)


# ---------------------------------------------------------------- tivi
@app.get("/cai-dat/tivi", response_class=HTMLResponse)
def tv_page(request: Request, user=Depends(admin)):
    return templates.TemplateResponse(request, "settings_tv.html", _nav(request, "tivi"))


def _tv_public(c):
    return {**{k: v for k, v in c.items() if k != "token"}, "paired": bool(c["token"])}


@app.get("/api/tv/settings")
def tv_settings(request: Request, user=Depends(admin), s=Depends(db)):
    c = settings.get(s, "tv")
    if not c["dashboard_url"] and request.url.hostname not in ("localhost", "127.0.0.1", "testserver"):
        c["dashboard_url"] = f"{request.base_url}tv?sound=1"   # mở trang này bằng IP LAN thì tự điền đúng
    return _tv_public(c)


@app.post("/api/tv/settings")
async def tv_settings_save(request: Request, user=Depends(admin), s=Depends(db)):
    try:
        b = await request.json()
        assert isinstance(b, dict)
    except Exception:  # noqa: BLE001
        return _bad("Dữ liệu không hợp lệ")
    import ipaddress
    import re
    ip = str(b.get("ip") or "").strip()
    try:
        if ip:
            ipaddress.ip_address(ip)
    except ValueError:
        return _bad("IP tivi không đúng")
    daily = str(b.get("daily_time") or "").strip()
    if daily and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", daily):
        return _bad("Giờ mở hằng ngày dạng HH:MM, vd 07:00")
    try:
        wait = max(3, min(int(b.get("wait_seconds") or 10), 60))
    except (TypeError, ValueError):
        wait = 10
    old = settings.get(s, "tv")
    new = {"enabled": bool(b.get("enabled")), "ip": ip, "mac": str(b.get("mac") or "").strip().upper(),
           "broadcast": str(b.get("broadcast") or "").strip(), "dashboard_url": str(b.get("dashboard_url") or "").strip(),
           "wait_seconds": wait, "daily_time": daily, "wake_daily": bool(b.get("wake_daily")),
           "on_power": bool(b.get("on_power")), "keep_open": bool(b.get("keep_open"))}
    if ip != old["ip"]:
        new["token"] = ""                  # tivi khác -> phải bấm Cho phép lại
    return _tv_public(settings.put(s, "tv", new, user))


@app.get("/api/tv/status")
def tv_status(user=Depends(admin), s=Depends(db)):
    c = settings.get(s, "tv")
    if not c["ip"]:
        return {"state": "chưa cấu hình"}
    d = tv_remote.info(c["ip"])
    dev = (d or {}).get("device") or {}
    return {"state": tv_remote.power_state(c["ip"]), "name": dev.get("name") or (d or {}).get("name", ""),
            "model": dev.get("modelName", ""), "browser": tv_remote.browser_visible(c["ip"]),
            "mac": tv_remote.find_mac(c["ip"]) if d else "", "paired": bool(c["token"])}


@app.post("/api/tv/do/{action}")
def tv_do(action: str, user=Depends(admin), s=Depends(db)):
    c = settings.get(s, "tv")
    if not c["ip"]:
        return _bad("Chưa lưu IP tivi")
    try:
        if action == "open":
            tv_auto.open_dashboard(s, c)
            return {"ok": True, "msg": "Đã mở dashboard và bấm OK bật âm thanh"}
        if action == "wake":
            if not c["mac"]:
                return _bad("Chưa có MAC tivi (bấm Kiểm tra tivi để tự lấy, rồi Lưu)")
            tv_remote.wake(c["mac"], c["ip"], c["broadcast"])
            return {"ok": True, "msg": "Đã gửi lệnh bật. Tivi lên sau khoảng 5–15 giây"}
        key = {"off": "power", "enter": "enter", "vol_up": "vol_up", "vol_down": "vol_down", "mute": "mute",
               "home": "home", "back": "back", "pair": None}.get(action, "missing")
        if key == "missing":
            return _bad("Lệnh không hợp lệ", 404)
        if action == "off" and tv_remote.power_state(c["ip"]) != "on":
            return {"ok": True, "msg": "Tivi đang tắt"}
        token = tv_remote.run(c["ip"], c["token"], [("key", tv_remote.KEYS[key])] if key else [])
        if token and token != c["token"]:
            settings.put(s, "tv", {"token": token}, "tivi")
        return {"ok": True, "msg": "Đã kết nối tivi" if action == "pair" else "Đã gửi lệnh"}
    except tv_remote.TvError as e:
        return _bad(str(e))


@app.get("/cai-dat/am-thanh", response_class=HTMLResponse)
def sound_page(request: Request, msg: str = "", user=Depends(admin), s=Depends(db)):
    c = settings.get(s, "sound")
    have = tts.clips(s)
    want = tts.wanted(s)
    total = sum(len(v) for v in want.values())
    ready = sum(len(v) for v in have.values())
    sample = next(iter(want.items()), None)
    return templates.TemplateResponse(request, "settings_sound.html", {
        **_nav(request, "am-thanh"), "c": c, "key_mask": settings.mask(c["fpt_key"]), "voices": settings.FPT_VOICES,
        "google_voices": settings.GOOGLE_VOICES, "google_account": tts.google_account(c["google_credentials"]),
        "total": total, "ready": ready, "msg": msg, "dups": tts.duplicates(s),
        "sample_code": sample[0] if sample else "", "sample_text": sample[1]["alarm"][0] if sample and sample[1] else ""})


@app.post("/cai-dat/am-thanh")
async def sound_save(request: Request, user=Depends(admin), s=Depends(db)):
    f = await request.form()

    def num(name, lo, hi, default):
        try:
            return max(lo, min(int(f.get(name) or default), hi))
        except ValueError:
            return default
    voice = f.get("voice") if f.get("voice") in ("off", "browser", "fpt", "google") else "off"
    gvoice = f.get("google_voice") if f.get("google_voice") in settings.GOOGLE_VOICES else "vi-VN-Chirp3-HD-Despina"
    creds = ""
    up = f.get("google_file")
    if up is not None and hasattr(up, "read"):
        creds = (await up.read()).decode("utf-8-sig", "replace").strip()
    creds = creds or (f.get("google_json") or "").strip()
    if creds and not tts.google_account(creds):
        return RedirectResponse("/cai-dat/am-thanh?msg=File khóa Google không đúng định dạng JSON service account", status_code=303)
    fpt_voice = f.get("fpt_voice") if f.get("fpt_voice") in settings.FPT_VOICES else "myan"
    settings.put(s, "sound", {
        "default_on": f.get("default_on") == "1",
        "chime": f.get("chime") if f.get("chime") in ("0", "1", "3") else "3",
        "volume": num("volume", 5, 100, 70),
        "repeat_minutes": num("repeat_minutes", 1, 60, 5),
        "mute_minutes": num("mute_minutes", 5, 240, 30),
        "offline_chime": f.get("offline_chime") == "1",
        "voice": voice, "fpt_key": (f.get("fpt_key") or "").strip(), "fpt_voice": fpt_voice,
        "google_credentials": creds, "google_voice": gvoice,
        "speed": num("speed", -3, 3, 0),
        "alarm_text": (f.get("alarm_text") or "").strip()[:200],
        "offline_text": (f.get("offline_text") or "").strip()[:200],
    }, user)
    return RedirectResponse("/cai-dat/am-thanh?msg=Đã lưu", status_code=303)


@app.post("/cai-dat/am-thanh/tao-giong")
def sound_build(user=Depends(admin), s=Depends(db)):
    made, have, errors = tts.build(s)
    return {"ok": not errors, "made": made, "have": have, "errors": errors}


@app.get("/api/sound")
def api_sound(s=Depends(db)):
    """Cấu hình âm thanh cho màn hình dashboard (không kèm khóa bí mật) + các file giọng đọc đã có."""
    c = settings.get(s, "sound")
    c.pop("fpt_key", None)
    c.pop("google_credentials", None)
    return {**c, "clips": tts.clips(s) if c["voice"] in ("fpt", "google") else {}}


@app.get("/audio/{name}")
def audio(name: str):
    if not tts.NAME_RE.match(name):
        raise HTTPException(404)
    path = Path(config.AUDIO_DIR) / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=31536000"})
