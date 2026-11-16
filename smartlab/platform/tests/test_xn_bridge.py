import configparser
from datetime import datetime

from sqlalchemy import create_engine, text

from tools import xn_bridge


def test_collect_from_sql(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/lis.db")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE ket_qua (sid TEXT, may TEXT, nhan TEXT, tra TEXT, tat INTEGER)"))
        for sid, tra, tat in (("1", "2026-11-16 08:00:00", 30), ("2", "2026-11-16 09:50:00", 40),
                              ("3", "2026-11-16 09:55:00", 50), ("0", "2026-11-15 23:00:00", 99)):
            c.execute(text("INSERT INTO ket_qua VALUES (:s,'P1','',:t,:a)"), {"s": sid, "t": tra, "a": tat})
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    cp.read_dict({"xn01": {
        "may": "P1",
        "samples_today": "SELECT COUNT(*) FROM ket_qua WHERE may=:may AND tra >= :dau_ngay",
        "last_result": "SELECT MAX(tra) FROM ket_qua WHERE may=:may",
        "tat_median_min": "SELECT tat FROM ket_qua WHERE may=:may AND tra >= :dau_ngay",
        "iqc": "SELECT 'Đạt'",
        "status": "",
    }})
    now = datetime(2026, 11, 16, 10, 0, tzinfo=xn_bridge.TZ)
    with eng.connect() as c:
        data, errors = xn_bridge.collect(c, cp["xn01"], now)
    assert errors == []
    assert data == {"samples_today": 3, "last_result": "09:55", "tat_median_min": 40, "iqc": "Đạt",
                    "status": "running"}


def test_collect_status_and_bad_sql(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/lis.db")
    cp = configparser.ConfigParser(interpolation=None)
    cp.read_dict({"xn01": {"last_result": "SELECT datetime('2026-11-16 09:30:00')", "iqc": "SELECT * FROM khong_co"}})
    now = datetime(2026, 11, 16, 10, 0, tzinfo=xn_bridge.TZ)
    with eng.connect() as c:
        data, errors = xn_bridge.collect(c, cp["xn01"], now)
    assert data["last_result"] == "09:30" and len(errors) == 1
