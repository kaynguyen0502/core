import base64
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SMARTLAB_ADMIN_PASSWORD", "test-pass")

from smartlab import alarms, db, ingest, seed, service  # noqa: E402
from smartlab.models import AlarmEvent, AuditLog, Point, PointState  # noqa: E402

T0 = datetime(2026, 11, 8, 7, 0, 0)


@pytest.fixture()
def session(tmp_path, monkeypatch):
    eng = db.make_engine(f"sqlite:///{tmp_path}/t.db")
    db.init_db(eng)
    Session = sessionmaker(bind=eng, expire_on_commit=False)
    monkeypatch.setattr(db, "SessionLocal", Session)
    with Session() as s:
        seed.seed(s)
        yield s


# ---------- đọc bản tin ----------
def test_parse_zigbee():
    base, values, extra = ingest.parse("zigbee2mqtt/hs/tl01", b'{"temperature": 4.6, "battery": 90, "linkquality": 120}')
    assert base == "zigbee2mqtt/hs/tl01" and values == {"temperature": 4.6} and extra["battery"] == 90


def test_parse_camera_value_and_error():
    assert ingest.parse("smartlab/hs/ro01/main/value", b"0.82") == ("smartlab/hs/ro01", {"ec": 0.82}, {})
    assert ingest.parse("smartlab/hs/ro01/main/error", b"no error")[2] == {"read_error": ""}
    assert ingest.parse("smartlab/hs/ro01/main/error", b"Rate too high")[2] == {"read_error": "Rate too high"}
    assert ingest.parse("smartlab/hs/ro01/main/raw", b"0082") is None


def test_parse_ignores_bridge_and_garbage():
    assert ingest.parse("zigbee2mqtt/bridge/state", b'{"state":"online"}') is None
    assert ingest.parse("zigbee2mqtt/hs/tl01/availability", b'{"state":"online"}') is None
    assert ingest.parse("zigbee2mqtt/hs/tl01", b"not json") is None
    assert ingest.parse("smartlab/hs/ro01/main/value", b"NaN") is None


# ---------- cảnh báo ----------
def _send(s, topic, payload, t):
    return service.handle_message(s, topic, payload.encode(), now=t)


def test_alarm_after_delay_then_normal(session):
    tl07 = "zigbee2mqtt/hs/tl07"
    assert _send(session, tl07, '{"temperature": 5.0}', T0) == []
    ev = _send(session, tl07, '{"temperature": 9.3}', T0 + timedelta(minutes=1))
    assert ev == []  # mới vượt, đang chờ 15 phút xác nhận
    st = session.get(PointState, session.query(Point).filter_by(code="TL07").one().id)
    assert st.state == "warn"
    ev = _send(session, tl07, '{"temperature": 9.4}', T0 + timedelta(minutes=16))
    assert [e.kind for e in ev] == ["alarm"] and "9,4" in ev[0].message
    # nhắc lại sau 30 phút
    ev = service.check_all(session, now=T0 + timedelta(minutes=47))
    assert [e.kind for e in ev if "TL07" in e.message] == ["reminder"]
    ev = _send(session, tl07, '{"temperature": 5.2}', T0 + timedelta(minutes=50))
    assert [e.kind for e in ev] == ["normal"]


def test_short_door_opening_does_not_alarm(session):
    tl01 = "zigbee2mqtt/hs/tl01"
    _send(session, tl01, '{"temperature": 9.0}', T0)
    ev = _send(session, tl01, '{"temperature": 5.0}', T0 + timedelta(minutes=5))
    assert ev == []
    assert session.query(AlarmEvent).count() == 0


def test_offline_and_back(session):
    _send(session, "zigbee2mqtt/hs/tl10", '{"temperature": 5.0}', T0)
    ev = service.check_all(session, now=T0 + timedelta(minutes=61))
    assert any(e.kind == "offline" and "TL10" in e.message for e in ev)
    ev = _send(session, "zigbee2mqtt/hs/tl10", '{"temperature": 5.0}', T0 + timedelta(minutes=70))
    assert [e.kind for e in ev] == ["online"]


def test_ro_camera_threshold(session):
    p = session.query(Point).filter_by(code="RO01").one()
    _send(session, "smartlab/hs/ro01/main/value", "12.5", T0)
    ev = _send(session, "smartlab/hs/ro01/main/value", "12.8", T0 + timedelta(minutes=7))
    assert [e.kind for e in ev] == ["alarm"] and "µS/cm" in ev[0].message
    assert session.get(PointState, p.id).values["ec"] == 12.8


def test_humidity_warn(session):
    _send(session, "zigbee2mqtt/hs/mt03", '{"temperature": 24, "humidity": 73}', T0)
    st = session.get(PointState, session.query(Point).filter_by(code="MT03").one().id)
    assert st.state == "warn" and "độ ẩm" in st.message


def test_unknown_topic_ignored(session):
    assert _send(session, "zigbee2mqtt/hs/tl99", '{"temperature": 5}', T0) == []


def test_fmt():
    assert alarms.fmt(-19.8) == "−19,8" and alarms.fmt(0.823, 2) == "0,82" and alarms.fmt(None) == "--"


# ---------- web ----------
def test_api_status_and_catalog(session):
    from fastapi.testclient import TestClient
    from smartlab import web

    _send(session, "zigbee2mqtt/hs/tl01", '{"temperature": 4.6}', service.utcnow())
    c = TestClient(web.app)
    d = c.get("/api/status").json()
    assert d["total"] == 23
    tl01 = next(p for p in d["points"] if p["code"] == "TL01")
    assert tl01["display"] == "4,6" and tl01["range"] == "2…8 °C"
    assert c.get("/tv").status_code == 200
    assert c.get("/catalog").status_code == 401
    auth = {"Authorization": "Basic " + base64.b64encode(b"admin:test-pass").decode()}
    r = c.get("/catalog", headers=auth)
    assert r.status_code == 200 and "TL01" in r.text
    r = c.post("/catalog/TL01", headers=auth, data={"name": "Tủ mát hóa chất A", "group": "fridge", "source": "zigbee",
               "topic": "zigbee2mqtt/hs/tl01", "temp_min": "2", "temp_max": "8", "hum_min": "", "hum_max": "",
               "ec_max": "", "alarm_delay_min": "15", "offline_after_min": "60", "sort": "0", "active": "1"},
               follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    assert session.query(Point).filter_by(code="TL01").one().name == "Tủ mát hóa chất A"
    assert "Tủ mát hóa chất A" in session.query(AuditLog).one().detail


def test_never_seen_points_do_not_spam(session):
    ev = service.check_all(session, now=T0 + timedelta(hours=3))
    assert ev == []
    st = session.get(PointState, session.query(Point).filter_by(code="TL01").one().id)
    assert st.state == "offline" and st.message == "Chưa có dữ liệu"
    ev = _send(session, "zigbee2mqtt/hs/tl01", '{"temperature": 5.0}', T0 + timedelta(hours=3, minutes=1))
    assert ev == []  # lần đầu có dữ liệu: không báo "có tín hiệu lại"


def test_restart_grace(session):
    _send(session, "zigbee2mqtt/hs/tl02", '{"temperature": 5.0}', T0)
    start = T0 + timedelta(hours=2)  # máy chủ mất điện 2 giờ rồi khởi động lại
    ev = service.check_all(session, now=start + timedelta(minutes=5), seen_floor=start)
    assert not [e for e in ev if "TL02" in e.message]
    ev = service.check_all(session, now=start + timedelta(minutes=61), seen_floor=start)
    assert [e.kind for e in ev if "TL02" in e.message] == ["offline"]


def test_messages_use_vietnam_time(session):
    _send(session, "smartlab/hs/ro02/main/value", "15", T0)  # T0 = 07:00 UTC = 14:00 giờ VN
    ev = _send(session, "smartlab/hs/ro02/main/value", "15", T0 + timedelta(minutes=7))
    assert "Từ 14:00" in ev[0].message
