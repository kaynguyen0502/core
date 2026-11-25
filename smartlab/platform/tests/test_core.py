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
    settings.put(session, "zalo", {"api_key": "abc123456", "account_id": "A1"}, "admin")
    z = settings.put(session, "zalo", {"api_key": "••••3456", "account_id": "A2"}, "admin")   # ô còn che = giữ key
    assert z["api_key"] == "abc123456" and z["account_id"] == "A2" and z["on_alarm"] is True
    logs = session.scalars(select_audit()).all()
    assert logs and all("abc123456" not in a.detail for a in logs)
    assert settings.mask("abc123456") == "••••••" and settings.mask("x" * 20 + "WXYZ").endswith("WXYZ")


def select_audit():
    from sqlalchemy import select
    return select(AuditLog).where(AuditLog.action.like("cấu hình%"))


class _Resp:
    def __init__(self, code=200, data=None):
        self.status_code, self._d, self.text = code, data or {}, ""

    def json(self):
        return self._d


def test_zalo_send_respects_switches_and_logs(session, monkeypatch):
    from smartlab import notify, settings
    from smartlab.models import ZaloLog
    sent = []
    monkeypatch.setattr(notify.httpx, "post", lambda url, json=None, headers=None, timeout=None:
                        sent.append((url, json, headers)) or _Resp())
    settings.put(session, "zalo", {"enabled": True, "base_url": "http://z:3000", "api_key": "K", "account_id": "ACC",
                                   "recipients": [{"thread_id": "g1", "thread_type": "group", "name": "trực khoa"}],
                                   "on_alarm_end": False, "dashboard_url": "http://hs:8100"})
    p = session.query(Point).first()
    ev_alarm = AlarmEvent(point_id=p.id, ts=T0, kind="alarm", message="VƯỢT NGƯỠNG SH19-M", notified=False)
    ev_norm = AlarmEvent(point_id=p.id, ts=T0, kind="normal", message="ĐÃ BÌNH THƯỜNG SH19-M", notified=False)
    session.add_all([ev_alarm, ev_norm]); session.commit()      # worker cũng commit trước khi luồng gửi ghi nhật ký
    notify.send_events([ev_alarm, ev_norm])
    assert len(sent) == 1
    url, body, headers = sent[0]
    assert url == "http://z:3000/api/public/messages/send" and headers == {"X-Api-Key": "K"}
    assert body == {"zaloAccountId": "ACC", "threadId": "g1", "threadType": "group",
                    "content": "🔴 VƯỢT NGƯỠNG SH19-M\nLúc: 14:00 08/11/2026\nXem: http://hs:8100/tv"}
    assert ev_alarm.notified and not ev_norm.notified
    log = session.query(ZaloLog).one()
    assert log.ok and log.to == "Nhóm trực khoa"


def test_settings_pages_sound_api_and_tts_cache(session, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from smartlab import config, tts, web
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path))
    c = TestClient(web.app)
    assert c.get("/cai-dat/zalo").status_code == 401
    assert "Tìm nick Zalo" in c.get("/cai-dat/zalo", headers=AUTH).text
    assert "CRM" not in c.get("/cai-dat/zalo", headers=AUTH).text
    bad = c.post("/api/notify/settings", headers=AUTH, json={"enabled": True})
    assert bad.status_code == 400 and "cần đủ" in bad.json()["error"]
    d = c.post("/api/notify/settings", headers=AUTH, json={
        "enabled": True, "base_url": "http://z:3000/", "api_key": "SECRETKEY12345", "account_id": "ACC",
        "recipients": [{"thread_id": "g1", "thread_type": "group", "name": "Nhóm khoa"}, {"thread_id": " "}],
        "on_alarm": True, "repeat_minutes": 15}).json()
    assert d["api_key"] == "••••••2345" and d["base_url"] == "http://z:3000" and len(d["recipients"]) == 1
    assert c.get("/api/notify/settings", headers=AUTH).json()["repeat_minutes"] == 15
    assert c.get("/api/notify/logs", headers=AUTH).json() == {"logs": []}
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


def _fake_sa():
    import json as _json
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode()
    return _json.dumps({"type": "service_account", "client_email": "tts@demo.iam.gserviceaccount.com",
                        "private_key": pem, "token_uri": "https://oauth2.googleapis.com/token"})


def test_google_tts_upload_build_and_token(session, tmp_path, monkeypatch):
    import base64 as b64
    from fastapi.testclient import TestClient
    from smartlab import config, tts, web
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path))
    sa = _fake_sa()
    c = TestClient(web.app)
    r = c.post("/cai-dat/am-thanh", headers=AUTH, files={"google_file": ("key.json", sa.encode(), "application/json")},
               data={"voice": "google", "google_voice": "vi-VN-Chirp3-HD-Despina", "alarm_text": "Cảnh báo {ma} {ten}",
                     "offline_text": ""}, follow_redirects=False)
    assert r.status_code == 303 and "msg=File" not in r.headers["location"]
    page = c.get("/cai-dat/am-thanh", headers=AUTH).text
    assert "tts@demo.iam.gserviceaccount.com" in page and "PRIVATE KEY" not in page
    assert "google_credentials" not in c.get("/api/sound").json()
    calls = []

    class FakeHttp:
        def post(self, url, data=None, json=None, headers=None, timeout=None):
            calls.append(url)
            if "oauth2" in url:
                assert data["grant_type"].endswith("jwt-bearer") and data["assertion"].count(".") == 2
                return _Resp(200, {"access_token": "AT"})
            assert headers["Authorization"] == "Bearer AT" and json["voice"]["name"] == "vi-VN-Chirp3-HD-Despina"
            return _Resp(200, {"audioContent": b64.b64encode(b"ID3google").decode()})
    monkeypatch.setattr(tts, "httpx", type("M", (), {"post": FakeHttp().post, "HTTPError": Exception, "Client": None}))
    tts._google_token.update(value="", exp=0, who="")
    d = c.post("/cai-dat/am-thanh/tao-giong", headers=AUTH).json()
    assert d["ok"] and d["made"] == 21, d                    # có {ma} nên 21 câu khác nhau
    assert calls.count("https://oauth2.googleapis.com/token") == 1   # token dùng lại
    url = c.get("/api/sound").json()["clips"]["SH25-M"]["alarm"]
    assert c.get(url).content == b"ID3google"
    bad = c.post("/cai-dat/am-thanh", headers=AUTH, files={"google_file": ("x.json", b"not json", "application/json")},
                 data={"voice": "google"}, follow_redirects=False)
    from urllib.parse import unquote
    assert "không đúng" in unquote(bad.headers["location"])


# ---------- điều khiển tivi ----------
class _FakeWS:
    def __init__(self, replies):
        self.replies, self.sent, self.closed = list(replies), [], False

    def recv(self, timeout=None):
        if not self.replies:
            raise TimeoutError
        return self.replies.pop(0)

    def send(self, m):
        import json as _j
        self.sent.append(_j.loads(m))

    def close(self):
        self.closed = True


def test_tv_remote_pair_open_and_keys(monkeypatch):
    import json as _j
    import websockets.sync.client as wsc
    from smartlab import tv_remote
    fake = _FakeWS([_j.dumps({"event": "ms.channel.connect", "data": {"token": "T123"}})])
    seen = {}
    monkeypatch.setattr(wsc, "connect", lambda url, **kw: seen.setdefault("url", url) and fake)
    monkeypatch.setattr(tv_remote.time, "sleep", lambda s: None)
    tok = tv_remote.run("192.168.103.152", "", tv_remote.open_dashboard_steps("http://hs:8100/tv?sound=1", 10))
    assert tok == "T123" and fake.closed and "token" not in seen["url"]
    assert fake.sent[0]["params"]["data"] == {"appId": "org.tizen.browser", "action_type": "NATIVE_LAUNCH",
                                              "metaTag": "http://hs:8100/tv?sound=1"}
    assert fake.sent[1]["params"]["DataOfCmd"] == "KEY_ENTER"
    denied = _FakeWS([_j.dumps({"event": "ms.channel.unauthorized"})])
    monkeypatch.setattr(wsc, "connect", lambda url, **kw: denied)
    with pytest.raises(tv_remote.TvError):
        tv_remote.run("192.168.103.152", "", [])


def test_tv_wake_packet(monkeypatch):
    import socket as _s
    from smartlab import tv_remote
    got = []
    monkeypatch.setattr(_s.socket, "sendto", lambda self, data, addr: got.append((data, addr)) or len(data), raising=False)
    targets = tv_remote.wake("AA:BB:CC:DD:EE:01", "192.168.103.152", "192.168.103.255")
    assert "192.168.103.255" in targets and "255.255.255.255" in targets
    assert got and got[0][0] == b"\xff" * 6 + bytes.fromhex("aabbccddee01") * 16
    with pytest.raises(tv_remote.TvError):
        tv_remote.wake("xx", "1.2.3.4")


def test_tv_auto_opens_on_power_and_daily(session, monkeypatch):
    from smartlab import settings, tv_auto, tv_remote
    settings.put(session, "tv", {"enabled": True, "ip": "192.168.103.152", "mac": "AA:BB:CC:DD:EE:01",
                                 "dashboard_url": "http://hs:8100/tv?sound=1", "daily_time": "07:00"})
    states, opened, woke = ["standby"], [], []
    monkeypatch.setattr(tv_remote, "power_state", lambda ip: states[0])
    monkeypatch.setattr(tv_auto, "open_dashboard", lambda s, cfg=None: opened.append(1))
    monkeypatch.setattr(tv_remote, "wake", lambda *a: woke.append(a))
    a = tv_auto.Auto()
    assert a.tick(session, datetime(2026, 11, 22, 6, 0)) is None and not woke        # chưa tới giờ
    a.tick(session, datetime(2026, 11, 22, 7, 0))
    assert woke and not opened                                                       # tới giờ, tivi chờ -> bật
    states[0] = "on"
    assert a.tick(session, datetime(2026, 11, 22, 7, 1)) == "tivi vừa bật" and len(opened) == 1
    assert a.tick(session, datetime(2026, 11, 22, 7, 2)) is None                     # không mở lại liên tục


def test_tv_settings_api(session, monkeypatch):
    from fastapi.testclient import TestClient
    from smartlab import settings, tv_auto, web
    c = TestClient(web.app)
    assert "Kết nối tivi" in c.get("/cai-dat/tivi", headers=AUTH).text
    assert c.post("/api/tv/settings", headers=AUTH, json={"ip": "abc"}).status_code == 400
    assert c.post("/api/tv/settings", headers=AUTH, json={"ip": "192.168.103.152", "daily_time": "7h"}).status_code == 400
    d = c.post("/api/tv/settings", headers=AUTH, json={"ip": "192.168.103.152", "daily_time": "07:00",
                                                         "dashboard_url": "http://hs:8100/tv?sound=1"}).json()
    assert d["ip"] == "192.168.103.152" and "token" not in d and d["paired"] is False
    settings.put(session, "tv", {"token": "T1"})
    session.expire_all()
    assert c.get("/api/tv/settings", headers=AUTH).json()["paired"] is True
    calls = []
    monkeypatch.setattr(tv_auto, "open_dashboard", lambda s, cfg=None: calls.append(cfg["ip"]))
    assert c.post("/api/tv/do/open", headers=AUTH).json()["ok"] and calls == ["192.168.103.152"]
    assert c.post("/api/tv/do/xyz", headers=AUTH).status_code == 404
    c.post("/api/tv/settings", headers=AUTH, json={"ip": "192.168.103.153"})            # đổi tivi -> phải cho phép lại
    session.expire_all()
    assert settings.get(session, "tv")["token"] == ""


def test_simulated_values_hidden_flagged_in_db_and_deletable(session):
    from fastapi.testclient import TestClient
    from smartlab import records, web
    from smartlab.models import LogEntry, Reading
    from tools import xoa_gia_lap
    c = TestClient(web.app)
    r = c.post("/cai-dat/gia-lap", headers=AUTH, data={"v_RO01": "0,08", "v_RO02": "0.00", "v_RO03": "0.1"},
               follow_redirects=False)
    assert "3" in r.headers["location"]
    pts = {p["code"]: p for p in c.get("/api/status").json()["points"]}
    assert pts["RO01"]["display"] == "0,08" and pts["RO02"]["display"] == "0,00" and pts["RO03"]["display"] == "0,10"
    assert "GIẢ LẬP" not in c.get("/tv").text
    session.expire_all()
    _send(session, "smartlab/hs/ro01", '{"ec": 0.5}', service.utcnow() - timedelta(minutes=40))   # 1 số đo thật cũ
    records.record_slot(session, service.utcnow().replace(microsecond=0))
    e = session.query(LogEntry).join(Point).filter(Point.code == "RO01").one()
    assert e.ec == 0.08 and e.simulated and e.note == ""
    y, m = records.to_local(service.utcnow()).year, records.to_local(service.utcnow()).month
    page = c.get(f"/so-theo-doi?month={y:04d}-{m:02d}").text
    assert "GL" not in page and "giả lập" not in page.lower()
    assert session.query(Reading).filter(Reading.simulated.is_(True)).count() == 3
    assert xoa_gia_lap.main([]) == 1                                    # chỉ đếm
    assert xoa_gia_lap.main(["--yes"]) == 0
    session.expire_all()
    assert session.query(Reading).filter(Reading.simulated.is_(True)).count() == 0
    assert session.query(Reading).count() == 1                          # số đo thật còn nguyên
    assert session.query(LogEntry).filter(LogEntry.simulated.is_(True)).count() == 0
    from smartlab import settings
    assert settings.get(session, "simulate")["values"] == {}


def test_init_db_adds_new_columns_to_old_tables(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    eng = create_engine(f"sqlite:///{tmp_path}/old.db")
    with eng.begin() as cn:
        cn.execute(text("CREATE TABLE readings (id INTEGER PRIMARY KEY, point_id INT, ts DATETIME, metric VARCHAR(16), value FLOAT)"))
        cn.execute(text("INSERT INTO readings (point_id, ts, metric, value) VALUES (1, '2026-11-01', 'ec', 0.5)"))
    db.init_db(eng)
    assert "simulated" in {c["name"] for c in inspect(eng).get_columns("readings")}
    with eng.connect() as cn:
        assert cn.execute(text("SELECT simulated FROM readings")).scalar() == 0


# ---------- đọc số đo qua API thiết bị ----------
def test_api_extract_auto_and_path():
    from smartlab import api_poll
    assert api_poll.extract(0.08) == 0.08
    assert api_poll.extract({"device_id": "A-TPN-119", "value": "0,08", "timestamp": 1795573645}) == 0.08
    assert api_poll.extract({"ok": True, "data": {"id": 119, "ec": 0.12, "temp": 25.1}}) == 0.12
    assert api_poll.extract({"data": [{"value": 0.5}, {"value": 0.07}]}) == 0.07          # phần tử mới nhất
    assert api_poll.extract({"data": {"a": 1, "b": 2}}, "data.b") == 2
    with pytest.raises(RuntimeError):
        api_poll.extract({"data": {}}, "data.x")
    with pytest.raises(RuntimeError):
        api_poll.extract({"status": "ok"})


def test_api_source_polls_ro01_and_stops_simulation(session, monkeypatch):
    from fastapi.testclient import TestClient
    from smartlab import api_poll, settings, simulate, web
    from smartlab.models import Reading
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append((url, headers))
        return _Resp(200, {"device_id": "A-TPN-119", "value": 0.06})
    monkeypatch.setattr(api_poll.httpx, "get", fake_get)
    c = TestClient(web.app)
    assert "API thiết bị" in c.get("/cai-dat/api", headers=AUTH).text
    assert c.post("/api/sources", headers=AUTH, json={"sources": [{"code": "XX9", "url": "https://a"}]}).status_code == 400
    d = c.post("/api/sources", headers=AUTH, json={"sources": [
        {"code": "RO01", "url": "https://iot.example/api/getvalue", "token": "TOKEN_SECRET_123456", "every_min": 5}]}).json()
    assert d["sources"][0]["token"].startswith("•") and "TOKEN_SECRET" not in str(d)
    c.post("/api/sources", headers=AUTH, json={"sources": [{**d["sources"][0]}]})           # lưu lại, token che -> giữ
    session.expire_all()
    assert settings.get(session, "api")["sources"][0]["token"] == "TOKEN_SECRET_123456"
    t = c.post("/api/sources/test/RO01", headers=AUTH).json()
    assert t["value"] == 0.06 and "A-TPN-119" in t["raw"]
    assert calls[-1][1]["Authorization"] == "Bearer TOKEN_SECRET_123456"
    settings.put(session, "simulate", {"values": {"RO01": 0.08, "RO02": 0.0}})
    assert simulate.inject(session)[0] == 1                                                # RO01 có API -> thôi giả lập
    p = api_poll.Poller()
    assert p.tick(session, now=1000)[0] == 1
    assert p.tick(session, now=1100)[0] == 0                                               # chưa đủ 5 phút
    pts = {x["code"]: x for x in c.get("/api/status").json()["points"]}
    assert pts["RO01"]["display"] == "0,06"
    assert session.query(Reading).filter(Reading.simulated.is_(False), Reading.metric == "ec").count() == 1
