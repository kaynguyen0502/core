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
    tl07 = "zigbee2mqtt/hs/sh25_mat"
    assert _send(session, tl07, '{"temperature": 5.0}', T0) == []
    ev = _send(session, tl07, '{"temperature": 9.3}', T0 + timedelta(minutes=1))
    assert ev == []  # mới vượt, đang chờ 15 phút xác nhận
    st = session.get(PointState, session.query(Point).filter_by(code="SH25-M").one().id)
    assert st.state == "warn"
    ev = _send(session, tl07, '{"temperature": 9.4}', T0 + timedelta(minutes=16))
    assert [e.kind for e in ev] == ["alarm"] and "9,4" in ev[0].message
    # nhắc lại sau 30 phút
    ev = service.check_all(session, now=T0 + timedelta(minutes=47))
    assert [e.kind for e in ev if "SH25-M" in e.message] == ["reminder"]
    ev = _send(session, tl07, '{"temperature": 5.2}', T0 + timedelta(minutes=50))
    assert [e.kind for e in ev] == ["normal"]


def test_short_door_opening_does_not_alarm(session):
    tl01 = "zigbee2mqtt/hs/sh26_mat"
    _send(session, tl01, '{"temperature": 9.0}', T0)
    ev = _send(session, tl01, '{"temperature": 5.0}', T0 + timedelta(minutes=5))
    assert ev == []
    assert session.query(AlarmEvent).count() == 0


def test_offline_and_back(session):
    _send(session, "zigbee2mqtt/hs/sh63_mat", '{"temperature": 5.0}', T0)
    ev = service.check_all(session, now=T0 + timedelta(minutes=91))
    assert any(e.kind == "offline" and "SH63-M" in e.message for e in ev)
    ev = _send(session, "zigbee2mqtt/hs/sh63_mat", '{"temperature": 5.0}', T0 + timedelta(minutes=70))
    assert [e.kind for e in ev] == ["online"]


def test_ro_camera_threshold(session):
    p = session.query(Point).filter_by(code="RO01").one()
    _send(session, "smartlab/hs/ro01/main/value", "12.5", T0)
    ev = _send(session, "smartlab/hs/ro01/main/value", "12.8", T0 + timedelta(minutes=7))
    assert [e.kind for e in ev] == ["alarm"] and "µS/cm" in ev[0].message
    assert session.get(PointState, p.id).values["ec"] == 12.8


def test_humidity_warn(session):
    _send(session, "zigbee2mqtt/hs/mt01", '{"temperature": 24, "humidity": 68}', T0)
    st = session.get(PointState, session.query(Point).filter_by(code="MT01").one().id)
    assert st.state == "warn" and "độ ẩm" in st.message


def test_unknown_topic_ignored(session):
    assert _send(session, "zigbee2mqtt/hs/sh99_mat", '{"temperature": 5}', T0) == []


def test_fmt():
    assert alarms.fmt(-19.8) == "−19,8" and alarms.fmt(0.823, 2) == "0,82" and alarms.fmt(None) == "--"


# ---------- web ----------
def test_api_status_and_catalog(session):
    from fastapi.testclient import TestClient
    from smartlab import web

    _send(session, "zigbee2mqtt/hs/sh19_mat", '{"temperature": 4.6}', service.utcnow())
    c = TestClient(web.app)
    d = c.get("/api/status").json()
    assert d["total"] == 23  # 12 ngăn tủ + 6 phòng + 3 RO + 2 máy XN
    tl01 = next(p for p in d["points"] if p["code"] == "SH19-M")
    assert tl01["display"] == "4,6" and tl01["range"] == "2…8 °C"
    assert c.get("/tv").status_code == 200
    assert c.get("/catalog").status_code == 401
    auth = {"Authorization": "Basic " + base64.b64encode(b"admin:test-pass").decode()}
    r = c.get("/catalog", headers=auth)
    assert r.status_code == 200 and "SH19-M" in r.text
    r = c.post("/catalog/SH19-M", headers=auth, data={"name": "Hitachi hóa chất A – ngăn mát", "group": "fridge", "source": "zigbee",
               "topic": "zigbee2mqtt/hs/sh19_mat", "temp_min": "2", "temp_max": "8", "hum_min": "", "hum_max": "",
               "ec_max": "", "alarm_delay_min": "15", "offline_after_min": "60", "sort": "0", "active": "1"},
               follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    assert session.query(Point).filter_by(code="SH19-M").one().name == "Hitachi hóa chất A – ngăn mát"
    assert "Hitachi hóa chất A – ngăn mát" in session.query(AuditLog).one().detail


def test_never_seen_points_do_not_spam(session):
    ev = service.check_all(session, now=T0 + timedelta(hours=3))
    assert ev == []
    st = session.get(PointState, session.query(Point).filter_by(code="SH19-M").one().id)
    assert st.state == "offline" and st.message == "Chưa có dữ liệu"
    ev = _send(session, "zigbee2mqtt/hs/sh19_mat", '{"temperature": 5.0}', T0 + timedelta(hours=3, minutes=1))
    assert ev == []  # lần đầu có dữ liệu: không báo "có tín hiệu lại"


def test_restart_grace(session):
    _send(session, "zigbee2mqtt/hs/sh20_mat", '{"temperature": 5.0}', T0)
    start = T0 + timedelta(hours=2)  # máy chủ mất điện 2 giờ rồi khởi động lại
    ev = service.check_all(session, now=start + timedelta(minutes=5), seen_floor=start)
    assert not [e for e in ev if "SH20-M" in e.message]
    ev = service.check_all(session, now=start + timedelta(minutes=91), seen_floor=start)
    assert [e.kind for e in ev if "SH20-M" in e.message] == ["offline"]


def test_messages_use_vietnam_time(session):
    _send(session, "smartlab/hs/ro02/main/value", "15", T0)  # T0 = 07:00 UTC = 14:00 giờ VN
    ev = _send(session, "smartlab/hs/ro02/main/value", "15", T0 + timedelta(minutes=7))
    assert "Từ 14:00" in ev[0].message


def test_one_sided_freezer_threshold(session):
    # SH19 ngăn đông: an toàn khi < −20 °C, không có cận dưới
    _send(session, "zigbee2mqtt/hs/sh19_dong", '{"temperature": -18.5}', T0)
    ev = _send(session, "zigbee2mqtt/hs/sh19_dong", '{"temperature": -18.0}', T0 + timedelta(minutes=16))
    assert [e.kind for e in ev] == ["alarm"] and "≤ −20 °C" in ev[0].message
    _send(session, "zigbee2mqtt/hs/sh20_dong", '{"temperature": -50}', T0)
    st = session.get(PointState, session.query(Point).filter_by(code="SH20-D").one().id)
    assert st.state == "ok"


def test_seed_precreates_states(session):
    # mọi điểm có sẵn bản ghi trạng thái -> không có hai luồng cùng tạo một lúc
    assert session.query(PointState).count() == session.query(Point).count() == 23


# ---------- sổ theo dõi ISO, lịch sử, xuất dữ liệu ----------
from smartlab import records  # noqa: E402
from smartlab.models import LogEntry  # noqa: E402


def _vn(y, mo, d, h, mi=0):
    """Giờ Việt Nam -> UTC (naive) để gửi vào hệ thống."""
    return records.to_utc(datetime(y, mo, d, h, mi))


def test_logbook_slot_picks_nearest_and_flags(session):
    # SH25-M: 06:40 = 5,0 ; 06:58 = 9,1 (gần 07:00 nhất, vượt 8 °C)
    _send(session, "zigbee2mqtt/hs/sh25_mat", '{"temperature": 5.0}', _vn(2026, 11, 20, 6, 40))
    _send(session, "zigbee2mqtt/hs/sh25_mat", '{"temperature": 9.1}', _vn(2026, 11, 20, 6, 58))
    # MT01 đo lúc 06:10 -> ngoài cửa sổ 30 phút -> không có dữ liệu
    _send(session, "zigbee2mqtt/hs/mt01", '{"temperature": 22, "humidity": 60}', _vn(2026, 11, 20, 6, 10))
    slot = _vn(2026, 11, 20, 7)
    added = records.record_slot(session, slot)
    assert added == 21  # 12 ngăn tủ + 6 phòng + 3 RO (không tính máy XN)
    e = {session.get(Point, x.point_id).code: x for x in session.query(LogEntry).all()}
    assert e["SH25-M"].temperature == 9.1 and e["SH25-M"].status == "khong_dat"
    assert e["MT01"].status == "khong_du_lieu"
    assert records.record_slot(session, slot) == 0  # chạy lại không ghi trùng


def test_record_due_slots_catches_up(session):
    _send(session, "zigbee2mqtt/hs/sh26_mat", '{"temperature": 4.5}', _vn(2026, 11, 20, 12, 50))
    # máy chủ bật lại lúc 15:00 -> ghi bù mốc 07:00 và 13:00 hôm nay + hôm qua
    n = records.record_due_slots(session, now_utc=_vn(2026, 11, 20, 15))
    assert n == 21 * 4
    st = {(records.to_local(x.slot).hour, session.get(Point, x.point_id).code): x for x in session.query(LogEntry).all()
          if records.to_local(x.slot).day == 20}
    assert st[(13, "SH26-M")].temperature == 4.5 and st[(13, "SH26-M")].status == "dat"
    assert records.record_due_slots(session, now_utc=_vn(2026, 11, 20, 15, 30)) == 0
    # trước 07:05 chưa ghi mốc 07:00 của ngày mới
    assert records.record_due_slots(session, now_utc=_vn(2026, 11, 21, 7, 2)) == 0


def test_logbook_csv_and_monthly_export(session, tmp_path):
    _send(session, "zigbee2mqtt/hs/sh19_mat", '{"temperature": 4.6}', _vn(2026, 10, 15, 6, 55))
    records.record_slot(session, _vn(2026, 10, 15, 7))
    text = records.logbook_csv(session, 2026, 10)
    assert text.startswith("﻿Ngày,Giờ,Mã") and "2026-10-15,07:00,SH19-M" in text and "Đạt" in text
    folder = records.export_previous_month_if_due(session, now_utc=_vn(2026, 11, 1, 0, 30), base_dir=str(tmp_path))
    assert folder and sorted(os.listdir(folder)) == ["so-do-chi-tiet_2026-10.csv", "so-theo-doi_2026-10.csv"]
    detail = open(os.path.join(folder, "so-do-chi-tiet_2026-10.csv"), encoding="utf-8").read()
    assert "SH19-M" in detail and "2026-10-15 06:55:00" in detail
    assert records.export_previous_month_if_due(session, now_utc=_vn(2026, 11, 2, 9), base_dir=str(tmp_path)) is None


def test_history_api_and_pages(session):
    from fastapi.testclient import TestClient
    from smartlab import web

    now = service.utcnow()
    for i in range(30):
        _send(session, "zigbee2mqtt/hs/mt02", '{"temperature": %s, "humidity": 55}' % (22 + i % 3), now - timedelta(minutes=30 - i))
    records.record_slot(session, now - timedelta(minutes=5))
    c = TestClient(web.app)
    d = c.get("/api/history/MT02?days=1").json()
    assert d["metrics"]["temperature"]["min"] == 22 and d["metrics"]["temperature"]["max"] == 24
    assert d["metrics"]["humidity"]["count"] == 30 and d["limits"]["temp_max"] == 25
    assert d["metrics"]["temperature"]["series"][0][0].endswith("+07:00")
    assert c.get("/api/history/XX99").status_code == 404
    r = c.get("/export/readings.csv?code=MT02")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and "MT02" in r.text
    month = records.to_local(now).strftime("%Y-%m")
    page = c.get(f"/so-theo-doi?month={month}")
    assert page.status_code == 200 and "MT02" in page.text and "In sổ" in page.text
    assert c.get(f"/export/logbook.csv?month={month}").status_code == 200
    assert 'data-code' in c.get("/tv").text


def test_reset_data_keeps_points(session):
    from smartlab import db as dbmod
    from smartlab.models import Point, Reading
    from tools import reset_data
    from sqlalchemy import func, select
    with dbmod.SessionLocal() as s:
        n_points = s.scalar(select(func.count()).select_from(Point))
        p = s.scalars(select(Point)).first()
        s.add(Reading(point_id=p.id, ts=datetime(2026, 11, 1), metric="temperature", value=5))
        s.commit()
    assert reset_data.main([]) == 1          # chưa --yes thì không xóa
    assert reset_data.main(["--yes"]) == 0
    with dbmod.SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(Reading)) == 0
        assert s.scalar(select(func.count()).select_from(Point)) == n_points


# ---------- cấu hình trên web: Zalo, âm thanh ----------
AUTH = {"Authorization": "Basic " + base64.b64encode(b"admin:test-pass").decode()}


def test_settings_secret_kept_and_audited(session):
    from smartlab import settings
    settings.put(session, "zalo", {"token": "abc123456", "prefix": "[HS]"}, "admin")
    z = settings.put(session, "zalo", {"token": "  ", "prefix": "[HS2]"}, "admin")   # để trống = giữ token
    assert z["token"] == "abc123456" and z["prefix"] == "[HS2]" and z["kinds"]["alarm"] is True
    logs = session.scalars(select_audit()).all()
    assert logs and all("abc123456" not in a.detail for a in logs)
    assert settings.mask("abc123456") == "••••••" and settings.mask("x" * 20 + "WXYZ").endswith("WXYZ")


def select_audit():
    from sqlalchemy import select
    return select(AuditLog).where(AuditLog.action.like("cấu hình%"))


def test_zalo_send_respects_kinds(session, monkeypatch):
    from smartlab import notify, settings
    sent = []
    monkeypatch.setattr(notify, "call", lambda token, method, payload=None, timeout=15: sent.append((token, payload)) or {})
    settings.put(session, "zalo", {"token": "T", "recipients": [{"id": "g1", "name": "Nhóm", "on": True},
                                                                 {"id": "u2", "name": "Tắt", "on": False}],
                                   "kinds": {"normal": False}})
    p = session.query(Point).first()
    ev_alarm = AlarmEvent(point_id=p.id, ts=T0, kind="alarm", message="SH19-M nóng", notified=False)
    ev_norm = AlarmEvent(point_id=p.id, ts=T0, kind="normal", message="SH19-M bình thường", notified=False)
    session.add_all([ev_alarm, ev_norm]); session.flush()
    notify.send_events([ev_alarm, ev_norm])
    assert sent == [("T", {"chat_id": "g1", "text": "[SmartLab HS] 🔴 SH19-M nóng"})]
    assert ev_alarm.notified and ev_norm.notified


def test_settings_pages_sound_api_and_tts_cache(session, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from smartlab import config, tts, web
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path))
    c = TestClient(web.app)
    assert c.get("/cai-dat/zalo").status_code == 401
    assert "Zalo Bot" in c.get("/cai-dat/zalo", headers=AUTH).text
    r = c.post("/cai-dat/zalo", headers=AUTH, data={"token": "TK", "rid": ["g1", ""], "rname": ["Nhóm khoa", ""],
                                                     "ron": ["0"], "kinds": ["alarm", "offline"], "remind_minutes": "45",
                                                     "prefix": "[HS]"}, follow_redirects=False)
    assert r.status_code == 303
    page = c.get("/cai-dat/zalo", headers=AUTH).text
    assert "Nhóm khoa" in page and "TK" not in page.replace("TKB", "")
    r = c.post("/cai-dat/am-thanh", headers=AUTH, data={"voice": "fpt", "fpt_key": "KEY9", "fpt_voice": "giahuy",
                                                         "chime": "1", "volume": "80", "repeat_minutes": "3",
                                                         "mute_minutes": "20", "alarm_text": "Cảnh báo {ten}",
                                                         "offline_text": ""}, follow_redirects=False)
    assert r.status_code == 303
    calls = []
    monkeypatch.setattr(tts, "fpt_tts", lambda text, key, voice, speed: calls.append((text, key, voice)) or b"ID3fake")
    d = c.post("/cai-dat/am-thanh/tao-giong", headers=AUTH).json()
    assert d["ok"] and d["made"] == 18 and calls[0][1:] == ("KEY9", "giahuy")      # 21 điểm, 3 cặp trùng tên -> 18 câu
    assert c.post("/cai-dat/am-thanh/tao-giong", headers=AUTH).json()["made"] == 0  # đã có thì không tạo lại
    assert "SH25-M, SH26-M" in c.get("/cai-dat/am-thanh", headers=AUTH).text           # cảnh báo trùng tên
    s = c.get("/api/sound").json()
    assert "fpt_key" not in s and s["repeat_minutes"] == 3 and s["chime"] == "1"
    url = s["clips"]["SH19-M"]["alarm"]
    assert c.get(url).content == b"ID3fake"
    assert c.get("/audio/../../.env").status_code == 404
