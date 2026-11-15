"""Tiến trình nền: nghe MQTT, lưu số đo, kiểm tra mất tín hiệu, gửi Zalo, nhịp tim watchdog.

Chạy: python -m smartlab.worker
"""
import logging
import threading
import time

import paho.mqtt.client as mqtt

from . import config, notify, records, service
from .db import SessionLocal, init_db

log = logging.getLogger("smartlab.worker")
CHECK_EVERY_S = 30
# Luồng nhận MQTT và luồng kiểm tra định kỳ cùng sửa trạng thái: cho chạy lần lượt
LOCK = threading.Lock()
HEARTBEAT_EVERY_S = 60


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code.is_failure:
        log.error("Kết nối MQTT thất bại: %s", reason_code)
        return
    for t in config.MQTT_SUBSCRIBE:
        client.subscribe(t, qos=1)
    log.info("Đã kết nối MQTT %s:%s, nghe %s", config.MQTT_HOST, config.MQTT_PORT, config.MQTT_SUBSCRIBE)


def on_message(client, userdata, msg):
    try:
        with LOCK, SessionLocal() as s:
            events = service.handle_message(s, msg.topic, msg.payload)
            if events:
                notify.send_events(events)
                s.commit()
    except Exception:  # không để một bản tin lỗi làm dừng worker
        log.exception("Lỗi xử lý bản tin %s", msg.topic)


def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()
    with SessionLocal() as s:
        service.ensure_states(s)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="smartlab-worker")
    if config.MQTT_USER:
        client.username_pw_set(config.MQTT_USER, config.MQTT_PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(1, 30)
    client.connect_async(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
    client.loop_start()

    started = service.utcnow()
    last_hb = 0.0
    while True:
        try:
            with LOCK, SessionLocal() as s:
                events = service.check_all(s, remind_minutes=config.REMIND_MINUTES, seen_floor=started)
                if events:
                    notify.send_events(events)
                    s.commit()
        except Exception:
            log.exception("Lỗi kiểm tra định kỳ")
        try:
            with LOCK, SessionLocal() as s:
                n = records.record_due_slots(s)
                if n:
                    log.info("Đã ghi sổ theo dõi: %s dòng", n)
                folder = records.export_previous_month_if_due(s)
                if folder:
                    log.info("Đã xuất dữ liệu tháng trước vào %s", folder)
        except Exception:
            log.exception("Lỗi ghi sổ / xuất dữ liệu")
        if time.monotonic() - last_hb >= HEARTBEAT_EVERY_S:
            notify.heartbeat()
            last_hb = time.monotonic()
        time.sleep(CHECK_EVERY_S)


if __name__ == "__main__":
    run()
