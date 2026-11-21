"""Số đo giả lập tạm thời (vd RO chưa nối camera/API): tiến trình nền gửi giá trị cố định mỗi 5 phút.

Số đo giả lập được đánh dấu "GIẢ LẬP" trên dashboard và "GL" trong sổ theo dõi ISO; có số đo thật thì tự bỏ dấu.
"""
import json

from sqlalchemy import select

from . import service, settings
from .models import Point

EVERY_S = 300
METRIC = {"ro": "ec", "fridge": "temperature", "freezer": "temperature", "room": "temperature"}


def inject(session):
    """Gửi 1 vòng số đo giả lập. Trả về (số điểm, các AlarmEvent mới)."""
    vals = settings.get(session, "simulate")["values"]
    if not vals:
        return 0, []
    events, n = [], 0
    for p in session.scalars(select(Point).where(Point.code.in_(list(vals)), Point.active.is_(True))):
        metric = METRIC.get(p.group)
        if metric is None:
            continue
        payload = json.dumps({metric: vals[p.code], "simulated": True}).encode()
        # đi qua đúng đường xử lý số đo thật (lưu, đánh giá ngưỡng, cảnh báo)
        events += service.handle_message(session, p.topic, payload)
        n += 1
    return n, events
