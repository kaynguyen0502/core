"""Danh mục ban đầu – Khoa Hóa Sinh, BVĐK Đà Nẵng (theo "Danh sách tủ lạnh_SH.xlsx" của khoa).

Mã điểm đo dùng mã thiết bị của khoa: SH19-M = tủ SH.19 ngăn mát, SH19-D = ngăn đông.
Ngưỡng lấy từ cột "Ngưỡng an toàn"; ngưỡng một phía (vd "< −20 °C") để trống cận còn lại.
Chạy: python -m smartlab.seed   (chỉ thêm điểm còn thiếu, không ghi đè điểm đã có)
"""
from sqlalchemy import select

from .db import SessionLocal, init_db
from .models import Point

# Cảm biến Sonoff gửi khi số thay đổi, chậm nhất 60 phút/lần -> 90 phút mới coi là mất tín hiệu
ZB_OFFLINE = 90
COOL = dict(group="fridge", temp_min=2, temp_max=8, alarm_delay_min=15, offline_after_min=ZB_OFFLINE)
RO = dict(group="ro", ec_max=10, alarm_delay_min=6, offline_after_min=15)
XN = dict(group="analyzer", source="lis", alarm_delay_min=0, offline_after_min=30)


def _cool(sh, name, location):
    return dict(code=f"SH{sh}-M", name=f"{name} – ngăn mát", location=location,
                topic=f"zigbee2mqtt/hs/sh{sh}_mat", **COOL)


def _frz(sh, name, location, lo, hi):
    return dict(code=f"SH{sh}-D", name=f"{name} – ngăn đông", location=location, group="freezer",
                topic=f"zigbee2mqtt/hs/sh{sh}_dong", temp_min=lo, temp_max=hi,
                alarm_delay_min=15, offline_after_min=ZB_OFFLINE)


def _room(i, name, t_hi, h_lo):
    return dict(code=f"MT{i:02d}", name=name, location=name.rsplit(" ", 1)[0] if name[-1].isdigit() else name,
                group="room", topic=f"zigbee2mqtt/hs/mt{i:02d}", temp_min=15, temp_max=t_hi,
                hum_min=h_lo, hum_max=70, alarm_delay_min=30, offline_after_min=ZB_OFFLINE)


POINTS = [
    # Tủ 2 ngăn: xếp cạnh nhau trên TV
    _cool("19", "Hitachi hóa chất", "Phòng máy"), _frz("19", "Hitachi hóa chất", "Phòng máy", None, -20),
    _cool("20", "Panasonic hóa chất", "Kho"), _frz("20", "Panasonic hóa chất", "Kho", -80, -20),
    _cool("42", "Samsung lưu mẫu", "Lưu mẫu – rửa dụng cụ"), _frz("42", "Samsung lưu mẫu", "Lưu mẫu – rửa dụng cụ", None, -20),
    _cool("61", "Panasonic hóa chất", "Kho"), _frz("61", "Panasonic hóa chất", "Kho", -25, -15),
    # Tủ 1 ngăn
    _cool("25", "Alaska lưu mẫu", "Lưu mẫu – rửa dụng cụ"),
    _cool("26", "Alaska lưu mẫu", "Kho"),
    _cool("63", "Sanaky hóa chất", "Kho"),
    _cool("81", "Sanaky", "Kho"),
    # Môi trường: 6 vị trí
    _room(1, "Phòng kho 1", 25, 50), _room(2, "Phòng kho 2", 25, 50),
    _room(3, "Phòng nhận mẫu", 25, None),
    _room(4, "Phòng máy 1", 25, 50), _room(5, "Phòng máy 2", 25, 50),
    _room(6, "Phòng hành chính", 30, None),
    # Nước RO
    dict(code="RO01", name="Máy RO 1", topic="smartlab/hs/ro01", source="camera", **RO),
    dict(code="RO02", name="Máy RO 2", topic="smartlab/hs/ro02", source="camera", **RO),
    dict(code="RO03", name="Máy RO 3", topic="smartlab/hs/ro03", source="api", **RO),
    # Máy xét nghiệm
    dict(code="XN01", name="cobas pro 1", topic="smartlab/hs/xn01", **XN),
    dict(code="XN02", name="cobas pro 2", topic="smartlab/hs/xn02", **XN),
]


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
