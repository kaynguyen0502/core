"""Xử lý nghiệp vụ dùng chung cho worker (MQTT) và web."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from . import alarms, ingest
from .models import AlarmEvent, Point, PointState, Reading


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _state(session, point):
    st = session.get(PointState, point.id)
    if st is None:
        st = PointState(point_id=point.id, values={}, extra={}, state="ok", message="")
        session.add(st)
    return st


def ensure_states(session):
    """Tạo sẵn bản ghi trạng thái cho mọi điểm đo, tránh hai luồng cùng tạo một lúc."""
    have = set(session.scalars(select(PointState.point_id)))
    for pid in session.scalars(select(Point.id)):
        if pid not in have:
            session.add(PointState(point_id=pid, values={}, extra={}, state="ok", message=""))
    session.commit()


def _record(session, point, events, now):
    out = []
    for kind, msg in events:
        ev = AlarmEvent(point_id=point.id, ts=now, kind=kind, message=msg, notified=False)
        session.add(ev)
        out.append(ev)
    return out


def handle_message(session, topic, payload, now=None):
    """Bản tin MQTT -> lưu số đo + cập nhật trạng thái. Trả về các AlarmEvent mới."""
    now = now or utcnow()
    parsed = ingest.parse(topic, payload)
    if parsed is None:
        return []
    base, values, extra = parsed
    point = session.scalar(select(Point).where(Point.topic == base, Point.active.is_(True)))
    if point is None:
        return []
    st = _state(session, point)
    if values:
        for metric, v in values.items():
            session.add(Reading(point_id=point.id, ts=now, metric=metric, value=v))
        st.values = {**(st.values or {}), **values}
        st.last_seen = now
        # số đo giả lập (trang Cài đặt → Giả lập) được đánh dấu; số đo thật tới thì bỏ dấu
        st.extra = {**(st.extra or {}), "simulated": bool(extra.pop("simulated", False))}
    if extra:
        st.extra = {**(st.extra or {}), **extra}
        if point.group == "analyzer":
            st.last_seen = now
    events = alarms.evaluate(point, st, now)
    created = _record(session, point, events, now)
    session.commit()
    return created


def check_all(session, now=None, remind_minutes=30, seen_floor=None):
    """Chạy định kỳ: phát hiện mất tín hiệu, nhắc lại cảnh báo."""
    now = now or utcnow()
    created = []
    for point in session.scalars(select(Point).where(Point.active.is_(True))):
        st = _state(session, point)
        created += _record(session, point, alarms.evaluate(point, st, now, remind_minutes, seen_floor), now)
    session.commit()
    return created


def sparkline(session, point_id, metric, hours=24, buckets=24, now=None):
    """Trung bình theo từng khoảng thời gian, cho biểu đồ nhỏ trên TV."""
    now = now or utcnow()
    start = now - timedelta(hours=hours)
    rows = session.execute(
        select(Reading.ts, Reading.value)
        .where(Reading.point_id == point_id, Reading.metric == metric, Reading.ts >= start)
        .order_by(Reading.ts)
    ).all()
    if not rows:
        return []
    width = timedelta(hours=hours) / buckets
    sums = {}
    for ts, v in rows:
        i = min(int((ts - start) / width), buckets - 1)
        s = sums.setdefault(i, [0.0, 0])
        s[0] += v
        s[1] += 1
    return [round(sums[i][0] / sums[i][1], 2) for i in sorted(sums)]


def counts_by_state(states):
    out = {"ok": 0, "warn": 0, "alarm": 0, "offline": 0}
    for s in states:
        out[s] = out.get(s, 0) + 1
    return out


def reading_count(session):
    return session.scalar(select(func.count(Reading.id)))
