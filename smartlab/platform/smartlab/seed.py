"""Danh mục ban đầu cho Khoa Hóa Sinh – BVĐK Đà Nẵng (23 điểm).

Tên tủ, khu vực, ngưỡng là GIÁ TRỊ TẠM: sửa trên trang /catalog sau khảo sát.
Chạy: python -m smartlab.seed   (chỉ thêm điểm còn thiếu, không ghi đè điểm đã có)
"""
from sqlalchemy import select

from .db import SessionLocal, init_db
from .models import Point

FRIDGE = dict(group="fridge", temp_min=2, temp_max=8, alarm_delay_min=15, offline_after_min=60)
FREEZER = dict(group="freezer", temp_min=-25, temp_max=-15, alarm_delay_min=15, offline_after_min=60)
ROOM = dict(group="room", temp_min=18, temp_max=30, hum_min=30, hum_max=75, alarm_delay_min=30, offline_after_min=60)
RO = dict(group="ro", ec_max=10, alarm_delay_min=6, offline_after_min=15)
XN = dict(group="analyzer", source="lis", alarm_delay_min=0, offline_after_min=30)

POINTS = (
    [dict(code=f"TL{i:02d}", name=n, topic=f"zigbee2mqtt/hs/tl{i:02d}", **FRIDGE) for i, n in enumerate([
        "Tủ mát hóa chất 1", "Tủ mát hóa chất 2", "Tủ mát chất chuẩn – QC", "Tủ mát sinh phẩm MD 1",
        "Tủ mát sinh phẩm MD 2", "Tủ mát lưu mẫu 1", "Tủ mát lưu mẫu 2", "Tủ mát kho",
        "Tủ mát cạnh cobas pro 1", "Tủ mát cạnh cobas pro 2"], start=1)]
    + [dict(code="TL11", name="Tủ đông lưu mẫu", topic="zigbee2mqtt/hs/tl11", **FREEZER),
       dict(code="TL12", name="Tủ đông hóa chất", topic="zigbee2mqtt/hs/tl12", **FREEZER)]
    + [dict(code=f"MT{i:02d}", name=n, topic=f"zigbee2mqtt/hs/mt{i:02d}", **ROOM) for i, n in enumerate([
        "Phòng máy cobas pro", "Khu nhận mẫu", "Kho hóa chất", "Phòng xét nghiệm thủ công",
        "Phòng lưu mẫu", "Khu máy RO"], start=1)]
    + [dict(code="RO01", name="Máy RO 1", topic="smartlab/hs/ro01", source="camera", **RO),
       dict(code="RO02", name="Máy RO 2", topic="smartlab/hs/ro02", source="camera", **RO),
       dict(code="RO03", name="Máy RO 3", topic="smartlab/hs/ro03", source="api", **RO)]
    + [dict(code="XN01", name="cobas pro 1", topic="smartlab/hs/xn01", **XN),
       dict(code="XN02", name="cobas pro 2", topic="smartlab/hs/xn02", **XN)]
)


def seed(session):
    added = 0
    for i, p in enumerate(POINTS):
        if session.scalar(select(Point).where(Point.code == p["code"])):
            continue
        session.add(Point(sort=i, **p))
        added += 1
    session.commit()
    return added


if __name__ == "__main__":
    init_db()
    with SessionLocal() as s:
        print(f"Đã thêm {seed(s)} điểm đo.")
