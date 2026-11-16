"""Cầu nối máy xét nghiệm: đọc CSDL MySQL sẵn có (LIS / Monitoring) -> gửi lên dashboard qua MQTT.

Mọi câu truy vấn nằm trong file xn_bridge.ini (mẫu: tools/xn_bridge.example.ini), không sửa code.

    python -m tools.xn_bridge --schema          # liệt kê bảng/cột của CSDL (không đọc dữ liệu bệnh nhân)
    python -m tools.xn_bridge --once            # chạy thử 1 vòng, in ra bản tin, KHÔNG gửi
    python -m tools.xn_bridge --once --send     # chạy 1 vòng và gửi
    python -m tools.xn_bridge                   # chạy liên tục (dịch vụ SmartLab-XNBridge)

Mỗi mục [xn01], [xn02]... là một máy. Mỗi khóa là một câu SELECT, lấy cột đầu tiên:
  samples_today   : 1 số
  last_result     : thời điểm (datetime) hoặc chuỗi "HH:MM"
  tat_median_min  : 1 số, HOẶC nhiều dòng (mỗi dòng TAT 1 mẫu, phút) -> tự tính trung vị
  iqc             : chuỗi ("Đạt" / "Không đạt"...)
  status          : running | standby | error | maintenance. Bỏ trống -> tự suy ra từ last_result:
                    có kết quả trong running_window_min phút gần nhất = Đang chạy, ngược lại Chờ mẫu.
Tham số dùng được trong câu SQL: :dau_ngay (00:00 hôm nay), :bay_gio, :may (giá trị khóa "may" của mục).
"""
import argparse
import configparser
import json
import logging
import os
import statistics
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, inspect, text  # noqa: E402

from smartlab import config  # noqa: E402

log = logging.getLogger("xn_bridge")
FIELDS = ("status", "last_result", "samples_today", "tat_median_min", "iqc")
TZ = ZoneInfo(config.TIMEZONE)


def load(path):
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
    cp.optionxform = str
    if not cp.read(path, encoding="utf-8-sig"):
        sys.exit(f"Không thấy file cấu hình {path} (chép từ tools/xn_bridge.example.ini rồi sửa).")
    return cp


def _clean(v):
    if isinstance(v, datetime):
        return v
    if hasattr(v, "is_integer"):              # float / Decimal
        f = float(v)
        return int(f) if f.is_integer() else round(f, 1)
    return v


def collect(conn, section, now_local):
    params = {"dau_ngay": now_local.replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None),
              "bay_gio": now_local.replace(tzinfo=None), "may": section.get("may", "")}
    out, errors = {}, []
    for f in FIELDS:
        sql = section.get(f, "").strip()
        if not sql:
            continue
        try:
            rows = [r[0] for r in conn.execute(text(sql), params) if r[0] is not None]
        except Exception as e:  # noqa: BLE001 - báo lỗi từng câu, không dừng cả vòng
            errors.append(f"{f}: {e.__class__.__name__}: {str(e).splitlines()[0][:200]}")
            continue
        if not rows:
            continue
        if f == "tat_median_min":
            out[f] = _clean(statistics.median(float(r) for r in rows))
        else:
            out[f] = _clean(rows[0])
    last = out.get("last_result")
    if isinstance(last, str) and len(last) > 8:   # "2026-11-16 09:55:00" dạng chuỗi
        try:
            last = datetime.fromisoformat(last)
        except ValueError:
            pass
    if isinstance(last, datetime):
        if "status" not in out:
            window = timedelta(minutes=int(section.get("running_window_min", "10")))
            out["status"] = "running" if params["bay_gio"] - last <= window else "standby"
        out["last_result"] = last.strftime("%H:%M")
    elif last is not None:
        out["last_result"] = str(last)[:5]
    return out, errors


def schema(engine):
    insp = inspect(engine)
    for t in insp.get_table_names():
        cols = ", ".join(f"{c['name']} ({c['type']})" for c in insp.get_columns(t))
        print(f"{t}: {cols}")


def mqtt_client():
    import paho.mqtt.client as mqtt
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="smartlab-xn-bridge")
    if config.MQTT_USER:
        c.username_pw_set(config.MQTT_USER, config.MQTT_PASSWORD)
    c.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
    c.loop_start()
    return c


def run_once(engine, cp, client=None):
    now_local = datetime.now(TZ)
    with engine.connect() as conn:
        for name in cp.sections():
            if name == "chung":
                continue
            data, errors = collect(conn, cp[name], now_local)
            for e in errors:
                log.warning("[%s] %s", name, e)
            topic = cp[name].get("topic", f"smartlab/hs/{name}")
            payload = json.dumps(data, ensure_ascii=False)
            if client is None:
                print(f"{topic}  {payload}")
            elif data:
                client.publish(topic, payload, qos=1)
                log.info("%s %s", topic, payload)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(os.getcwd(), "xn_bridge.ini"))
    ap.add_argument("--schema", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()
    cp = load(a.config)
    chung = cp["chung"] if cp.has_section("chung") else {}
    url = chung.get("db_url", "")
    if not url:
        sys.exit("Chưa khai báo db_url trong mục [chung].")
    engine = create_engine(url, pool_pre_ping=True, pool_recycle=3600)
    if a.schema:
        return schema(engine)
    if a.once:
        return run_once(engine, cp, mqtt_client() if a.send else None) or time.sleep(1 if a.send else 0)
    interval = int(chung.get("interval_sec", "120"))
    client = mqtt_client()
    while True:
        try:
            run_once(engine, cp, client)
        except Exception:  # noqa: BLE001 - mất kết nối CSDL thì thử lại vòng sau
            log.exception("Lỗi đọc CSDL, thử lại sau %s giây", interval)
        time.sleep(interval)


if __name__ == "__main__":
    main()
