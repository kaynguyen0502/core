"""Bảng dữ liệu.

Point       : danh mục điểm đo (mã cố định, topic, ngưỡng, tên hiển thị) – sửa trên trang /catalog
Reading     : từng số đo (nhiệt độ, độ ẩm, EC)
PointState  : trạng thái hiện tại của mỗi điểm (để dashboard đọc nhanh, tính cảnh báo có độ trễ)
AlarmEvent  : nhật ký cảnh báo (vượt ngưỡng, mất tín hiệu, trở lại bình thường)
AuditLog    : ai sửa danh mục, sửa gì, lúc nào (phục vụ ISO 15189)
LogEntry    : sổ theo dõi ISO 15189 – giá trị mỗi điểm tại các mốc ghi sổ (mặc định 07:00, 13:00)
"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

# Nhóm điểm đo
GROUPS = {
    "fridge": "Tủ mát",
    "freezer": "Tủ đông",
    "room": "Môi trường",
    "ro": "Nước RO",
    "analyzer": "Máy xét nghiệm",
}
# Nguồn dữ liệu
SOURCES = {
    "zigbee": "Zigbee2MQTT",
    "camera": "Camera (AI-on-the-edge)",
    "api": "API",
    "lis": "LIS",
}


class Point(Base):
    __tablename__ = "points"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)          # TL01 – không đổi sau nghiệm thu
    name: Mapped[str] = mapped_column(String(120))                      # tên hiển thị tiếng Việt
    group: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(16), default="zigbee")
    topic: Mapped[str] = mapped_column(String(160), unique=True)        # zigbee2mqtt/hs/tl01, smartlab/hs/ro01
    location: Mapped[str] = mapped_column(String(160), default="")
    temp_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    temp_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    hum_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    hum_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    ec_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    alarm_delay_min: Mapped[int] = mapped_column(Integer, default=15)   # vượt ngưỡng liên tục bao lâu mới báo
    offline_after_min: Mapped[int] = mapped_column(Integer, default=60)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Reading(Base):
    __tablename__ = "readings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    point_id: Mapped[int] = mapped_column(ForeignKey("points.id"))
    ts: Mapped[datetime] = mapped_column(DateTime)                      # UTC
    metric: Mapped[str] = mapped_column(String(16))                     # temperature | humidity | ec
    value: Mapped[float] = mapped_column(Float)
    __table_args__ = (Index("ix_readings_point_metric_ts", "point_id", "metric", "ts"),)


class PointState(Base):
    __tablename__ = "point_state"
    point_id: Mapped[int] = mapped_column(ForeignKey("points.id"), primary_key=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    values: Mapped[dict] = mapped_column(JSON, default=dict)            # {"temperature": 4.6, "humidity": 58, ...}
    extra: Mapped[dict] = mapped_column(JSON, default=dict)             # máy XN: status, last_result...; camera: lỗi đọc
    state: Mapped[str] = mapped_column(String(16), default="ok")        # ok | warn | alarm | offline
    out_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    alarm_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_notified: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    message: Mapped[str] = mapped_column(String(255), default="")


class AlarmEvent(Base):
    __tablename__ = "alarm_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    point_id: Mapped[int] = mapped_column(ForeignKey("points.id"))
    ts: Mapped[datetime] = mapped_column(DateTime)
    kind: Mapped[str] = mapped_column(String(16))                       # alarm | offline | normal | reminder
    message: Mapped[str] = mapped_column(Text)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime)
    user: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(Text)


class LogEntry(Base):
    """Một dòng sổ theo dõi: giá trị của một điểm đo tại một mốc ghi sổ."""
    __tablename__ = "log_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    point_id: Mapped[int] = mapped_column(ForeignKey("points.id"))
    slot: Mapped[datetime] = mapped_column(DateTime)                    # mốc ghi sổ (UTC)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity: Mapped[float | None] = mapped_column(Float, nullable=True)
    ec: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16))                     # dat | khong_dat | khong_du_lieu
    note: Mapped[str] = mapped_column(String(255), default="")
    __table_args__ = (UniqueConstraint("point_id", "slot", name="uq_log_point_slot"),)
