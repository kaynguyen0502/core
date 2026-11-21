"""Hồ sơ phục vụ ISO 15189:2022: sổ theo dõi 2 mốc/ngày, lịch sử, xuất CSV, xuất tự động hằng tháng.

- Sổ theo dõi: mỗi mốc (mặc định 07:00 và 13:00 giờ VN) lấy giá trị GẦN mốc nhất trong
  khoảng [mốc − 30 phút, mốc + 5 phút] của từng điểm đo, đánh giá Đạt / Không đạt / Không có dữ liệu.
  Máy chủ tắt lúc đến mốc thì khi chạy lại tự ghi bù từ dữ liệu đã lưu (tối đa 2 ngày).
- Xuất tự động: đầu mỗi tháng tạo thư mục exports/YYYY-MM chứa số đo chi tiết và sổ theo dõi tháng trước.
"""
import csv
import io
import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from . import alarms, config
from .models import GROUPS, LogEntry, Point, PointState, Reading

TZ = ZoneInfo(config.TIMEZONE)
WINDOW_BEFORE = timedelta(minutes=30)
WINDOW_AFTER = timedelta(minutes=5)
STATUS_TEXT = {"dat": "Đạt", "khong_dat": "Không đạt", "khong_du_lieu": "Không có dữ liệu"}
SENSOR_GROUPS = ("fridge", "freezer", "room", "ro")


# ---------------------------------------------------------------- thời gian
def to_utc(local_dt):
    return local_dt.replace(tzinfo=TZ).astimezone(timezone.utc).replace(tzinfo=None)


def to_local(utc_dt):
    return utc_dt.replace(tzinfo=timezone.utc).astimezone(TZ).replace(tzinfo=None)


def slot_times():
    out = []
    for t in config.LOG_TIMES:
        h, m = t.split(":")
        out.append(time(int(h), int(m)))
    return out


def month_range(year, month):
    """(đầu tháng, đầu tháng sau) theo giờ VN, trả về UTC."""
    start = datetime(year, month, 1)
    end = datetime(year + (month == 12), month % 12 + 1, 1)
    return to_utc(start), to_utc(end)


# ---------------------------------------------------------------- sổ theo dõi
def _nearest(session, point_id, metric, slot):
    rows = session.execute(
        select(Reading.ts, Reading.value).where(
            Reading.point_id == point_id, Reading.metric == metric,
            Reading.ts >= slot - WINDOW_BEFORE, Reading.ts <= slot + WINDOW_AFTER)
    ).all()
    if not rows:
        return None
    return min(rows, key=lambda r: abs(r[0] - slot))[1]


def record_slot(session, slot_utc):
    """Ghi sổ mọi điểm đo cảm biến tại một mốc (UTC). Bỏ qua điểm đã ghi. Trả về số dòng mới."""
    done = set(session.scalars(select(LogEntry.point_id).where(LogEntry.slot == slot_utc)))
    added = 0
    for p in session.scalars(select(Point).where(Point.active.is_(True), Point.group.in_(SENSOR_GROUPS))):
        if p.id in done:
            continue
        vals = {m: _nearest(session, p.id, m, slot_utc) for m in ("temperature", "humidity", "ec")}
        present = {k: v for k, v in vals.items() if v is not None}
        if not present:
            status, note = "khong_du_lieu", "Không có số đo trong 30 phút trước mốc"
        else:
            level, note = alarms.classify(p, present)
            status = "khong_dat" if level == "out" else "dat"
            if level != "out":
                note = ""
        st = session.scalar(select(PointState).where(PointState.point_id == p.id))
        if present and st is not None and (st.extra or {}).get("simulated"):
            note = ("Giá trị giả lập. " + note).strip()
        session.add(LogEntry(point_id=p.id, slot=slot_utc, status=status, note=note[:255], **vals))
        added += 1
    session.commit()
    return added


def record_due_slots(session, now_utc=None):
    """Ghi các mốc đã đến hạn (mốc + 5 phút) của hôm qua và hôm nay; chạy lặp lại không ghi trùng."""
    now_utc = now_utc or datetime.now(timezone.utc).replace(tzinfo=None)
    today = to_local(now_utc).date()
    added = 0
    for d in (today - timedelta(days=1), today):
        for t in slot_times():
            slot = to_utc(datetime.combine(d, t))
            if slot + WINDOW_AFTER <= now_utc:
                added += record_slot(session, slot)
    return added


def logbook(session, year, month):
    """Sổ theo dõi một tháng: danh sách mốc, danh sách điểm, ô[(mốc, mã)] = LogEntry."""
    start, end = month_range(year, month)
    points = session.scalars(select(Point).where(Point.group.in_(SENSOR_GROUPS))
                             .order_by(Point.sort, Point.code)).all()
    entries = session.scalars(select(LogEntry).where(LogEntry.slot >= start, LogEntry.slot < end)).all()
    by_id = {p.id: p for p in points}
    cells = {(e.slot, by_id[e.point_id].code): e for e in entries if e.point_id in by_id}
    slots = sorted({e.slot for e in entries})
    return slots, points, cells


# ---------------------------------------------------------------- lịch sử
def history(session, point_id, start, end, max_points=600):
    """{metric: [[giờ VN ISO, giá trị TB], ...]} rút gọn còn tối đa max_points điểm, kèm thống kê."""
    rows = session.execute(
        select(Reading.metric, Reading.ts, Reading.value)
        .where(Reading.point_id == point_id, Reading.ts >= start, Reading.ts < end)
        .order_by(Reading.ts)
    ).all()
    by_metric = {}
    for metric, ts, v in rows:
        by_metric.setdefault(metric, []).append((ts, v))
    width = (end - start) / max_points
    out = {}
    for metric, pts in by_metric.items():
        buckets = {}
        for ts, v in pts:
            i = int((ts - start) / width) if width.total_seconds() else 0
            b = buckets.setdefault(i, [ts, 0.0, 0])
            b[1] += v
            b[2] += 1
        # kèm múi giờ (+07:00) để trình duyệt hiểu đúng, không cộng giờ lần nữa
        series = [[b[0].replace(tzinfo=timezone.utc).astimezone(TZ).isoformat(timespec="minutes"),
                   round(b[1] / b[2], 2)]
                  for _, b in sorted(buckets.items())]
        values = [v for _, v in pts]
        out[metric] = {"series": series, "min": min(values), "max": max(values),
                       "avg": round(sum(values) / len(values), 2), "count": len(values)}
    return out


# ---------------------------------------------------------------- CSV (UTF-8 có BOM để Excel đọc đúng tiếng Việt)
def _csv(rows, header):
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def readings_csv(session, start, end, codes=None):
    q = (select(Point.code, Point.name, Reading.ts, Reading.metric, Reading.value)
         .join(Point, Point.id == Reading.point_id)
         .where(Reading.ts >= start, Reading.ts < end))
    if codes:
        q = q.where(Point.code.in_(codes))
    rows = session.execute(q.order_by(Point.sort, Point.code, Reading.ts)).all()
    return _csv([[c, n, to_local(ts).strftime("%Y-%m-%d %H:%M:%S"), alarms.LABEL.get(m, m), v, alarms.UNIT.get(m, "")]
                 for c, n, ts, m, v in rows],
                ["Mã", "Điểm đo", "Thời gian", "Chỉ số", "Giá trị", "Đơn vị"])


def logbook_csv(session, year, month):
    slots, points, cells = logbook(session, year, month)
    rows = []
    for slot in slots:
        loc = to_local(slot)
        for p in points:
            e = cells.get((slot, p.code))
            if e is None:
                continue
            rng = []
            if p.temp_min is not None or p.temp_max is not None:
                rng.append(alarms.range_text(p.temp_min, p.temp_max, "temperature"))
            if p.hum_min is not None or p.hum_max is not None:
                rng.append(alarms.range_text(p.hum_min, p.hum_max, "humidity"))
            if p.ec_max is not None:
                rng.append(alarms.range_text(None, p.ec_max, "ec"))
            rows.append([loc.strftime("%Y-%m-%d"), loc.strftime("%H:%M"), p.code, p.name,
                         GROUPS.get(p.group, p.group), e.temperature, e.humidity, e.ec,
                         " · ".join(rng), STATUS_TEXT[e.status], e.note])
    return _csv(rows, ["Ngày", "Giờ", "Mã", "Điểm đo", "Nhóm", "Nhiệt độ (°C)", "Độ ẩm (%)",
                       "EC (µS/cm)", "Ngưỡng", "Đánh giá", "Ghi chú"])


# ---------------------------------------------------------------- xuất tự động hằng tháng
def export_month(session, year, month, base_dir=None):
    base_dir = base_dir or config.EXPORT_DIR
    folder = os.path.join(base_dir, f"{year:04d}-{month:02d}")
    os.makedirs(folder, exist_ok=True)
    start, end = month_range(year, month)
    with open(os.path.join(folder, f"so-theo-doi_{year:04d}-{month:02d}.csv"), "w", encoding="utf-8", newline="") as f:
        f.write(logbook_csv(session, year, month))
    with open(os.path.join(folder, f"so-do-chi-tiet_{year:04d}-{month:02d}.csv"), "w", encoding="utf-8", newline="") as f:
        f.write(readings_csv(session, start, end))
    return folder


def export_previous_month_if_due(session, now_utc=None, base_dir=None):
    """Đầu mỗi tháng (hoặc lần chạy đầu tiên sau đó) xuất tháng trước nếu chưa có thư mục."""
    now_utc = now_utc or datetime.now(timezone.utc).replace(tzinfo=None)
    first = to_local(now_utc).date().replace(day=1)
    prev = first - timedelta(days=1)
    base_dir = base_dir or config.EXPORT_DIR
    if os.path.isdir(os.path.join(base_dir, f"{prev.year:04d}-{prev.month:02d}")):
        return None
    return export_month(session, prev.year, prev.month, base_dir)


def parse_day(text, default):
    try:
        return date.fromisoformat(text) if text else default
    except ValueError:
        return default
