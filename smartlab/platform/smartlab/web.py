"""Web: dashboard TV (/tv), API trạng thái, trang danh mục điểm đo (/catalog).

Chạy: uvicorn smartlab.web:app --host 0.0.0.0 --port 8000
"""
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from fastapi import Request
from sqlalchemy import select

from datetime import date, datetime, timedelta

from . import alarms, config, records, service
from . import db as dbmod
from .db import init_db
from .models import GROUPS, SOURCES, AlarmEvent, AuditLog, Point, PointState

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
