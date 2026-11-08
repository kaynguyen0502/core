"""Tính trạng thái điểm đo và sinh sự kiện cảnh báo.

Trạng thái: ok (bình thường) | warn (gần ngưỡng hoặc vừa vượt, đang chờ đủ thời gian xác nhận)
           | alarm (vượt ngưỡng liên tục >= alarm_delay_min) | offline (mất tín hiệu)
Sự kiện : alarm | reminder (nhắc lại) | normal (trở lại bình thường) | offline | online
"""
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from . import config

_TZ = ZoneInfo(config.TIMEZONE)


def hhmm(dt, with_date=False):
    """Giờ Việt Nam từ thời điểm UTC lưu trong CSDL."""
    loc = dt.replace(tzinfo=timezone.utc).astimezone(_TZ)
    return loc.strftime("%H:%M %d/%m" if with_date else "%H:%M")

UNIT = {"temperature": "°C", "humidity": "%", "ec": "µS/cm"}
LABEL = {"temperature": "nhiệt độ", "humidity": "độ ẩm", "ec": "EC"}


def fmt(v, digits=1):
    if v is None:
        return "--"
    s = f"{v:.{digits}f}".replace(".", ",")
    return s.replace("-", "−")


def limits(point):
    """[(metric, min, max, warn_margin)] theo ngưỡng đã khai báo."""
    out = []
    if point.temp_min is not None or point.temp_max is not None:
        lo, hi = point.temp_min, point.temp_max
        span = (hi - lo) if (lo is not None and hi is not None) else 5
        out.append(("temperature", lo, hi, max(0.5, span * 0.1)))
    if point.hum_min is not None or point.hum_max is not None:
        out.append(("humidity", point.hum_min, point.hum_max, 3))
    if point.ec_max is not None:
        out.append(("ec", None, point.ec_max, point.ec_max * 0.2))
    return out


def classify(point, values):
    """-> ('ok'|'warn'|'out', mô tả). Chỉ xét số đo đang có."""
    worst, notes = "ok", []
    for metric, lo, hi, margin in limits(point):
        v = values.get(metric)
        if v is None:
            continue
        digits = {"ec": 2, "humidity": 0}.get(metric, 1)
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            worst = "out"
            notes.append(f"{LABEL[metric]} {fmt(v, digits)} {UNIT[metric]} ngoài ngưỡng {range_text(lo, hi, metric)}")
        elif (lo is not None and v < lo + margin) or (hi is not None and v > hi - margin):
            if worst == "ok":
                worst = "warn"
            notes.append(f"{LABEL[metric]} {fmt(v, digits)} {UNIT[metric]} gần ngưỡng")
    return worst, "; ".join(notes)


def range_text(lo, hi, metric):
    vals = [x for x in (lo, hi) if x is not None]
    d = 0 if all(float(x).is_integer() for x in vals) else 1
    u = UNIT[metric]
    if lo is None:
        return f"≤ {fmt(hi, d)} {u}"
    if hi is None:
        return f"≥ {fmt(lo, d)} {u}"
    return f"{fmt(lo, d)}…{fmt(hi, d)} {u}"


def evaluate(point, st, now, remind_minutes=30, seen_floor=None):
    """Cập nhật `st` (PointState) tại thời điểm `now`, trả về danh sách (kind, message).

    seen_floor: thời điểm tiến trình khởi động. Sau khi máy chủ khởi động lại, mỗi điểm được
    chờ đủ `offline_after_min` tính từ lúc đó rồi mới bị coi là mất tín hiệu.
    """
    events = []
    tag = f"{point.code} · {point.name}"

    # Chưa từng có dữ liệu (chưa ghép nối): hiện "Chưa có dữ liệu", không gửi cảnh báo
    if st.last_seen is None:
        st.state, st.message = "offline", "Chưa có dữ liệu"
        return events

    # Mất tín hiệu
    seen = max(st.last_seen, seen_floor) if seen_floor else st.last_seen
    if now - seen > timedelta(minutes=point.offline_after_min):
        if st.state != "offline":
            since = hhmm(st.last_seen, with_date=True)
            events.append(("offline", f"MẤT TÍN HIỆU {tag} (lần cuối: {since}). Kiểm tra pin, sóng, nguồn."))
            st.state, st.out_since, st.alarm_since = "offline", None, None
            st.message = "Mất tín hiệu"
        return events
    if st.state == "offline":
        if st.message != "Chưa có dữ liệu":
            events.append(("online", f"Có tín hiệu lại: {tag}."))
        st.state = "ok"

    if point.group == "analyzer":
        status = (st.extra or {}).get("status", "")
        st.state = "alarm" if status == "error" else "ok"
        st.message = (st.extra or {}).get("message", "")
        return events

    level, note = classify(point, st.values or {})
    if level == "out":
        if st.out_since is None:
            st.out_since = now
        if now - st.out_since >= timedelta(minutes=point.alarm_delay_min):
            if st.state != "alarm":
                st.state, st.alarm_since, st.last_notified = "alarm", now, now
                events.append(("alarm", f"VƯỢT NGƯỠNG {tag}: {note}. Từ {hhmm(st.out_since)}."))
            elif st.last_notified and now - st.last_notified >= timedelta(minutes=remind_minutes):
                st.last_notified = now
                events.append(("reminder", f"VẪN VƯỢT NGƯỠNG {tag}: {note}. Từ {hhmm(st.out_since)}."))
        else:
            st.state = "warn"
        st.message = note
    else:
        if st.state == "alarm":
            events.append(("normal", f"ĐÃ BÌNH THƯỜNG {tag}: {note or 'trong ngưỡng'}."))
        st.out_since, st.alarm_since = None, None
        st.state = "warn" if level == "warn" else "ok"
        st.message = note
    return events
