#!/usr/bin/env python3
"""Giả lập 23 điểm đo gửi MQTT y như thiết bị thật, để chạy thử dashboard và cảnh báo.

  python tools/simulator.py --host localhost            # gửi mỗi 10 giây
  python tools/simulator.py --scenario alarm           # SH25 nóng dần vượt 8 °C, SH63 im lặng (mất tín hiệu)

Định dạng gửi:
  Zigbee2MQTT : zigbee2mqtt/hs/sh19_mat  {"temperature":..,"battery":..,"linkquality":..}
  Camera RO   : smartlab/hs/ro01/main/value  "0.82"   và  .../main/error "no error"
  API / LIS   : smartlab/hs/ro03 {"ec":..} ; smartlab/hs/xn01 {"status":"running",...}
"""
import argparse
import json
import random
import time
from datetime import datetime
from zoneinfo import ZoneInfo

VN = ZoneInfo("Asia/Ho_Chi_Minh")

import paho.mqtt.client as mqtt

FRIDGES = {f"sh{n}_mat": 5.0 for n in ("19", "20", "42", "61", "25", "26", "63", "81")}
FRIDGES.update({"sh19_dong": -24.0, "sh20_dong": -30.0, "sh42_dong": -22.0, "sh61_dong": -20.0})
ROOMS = {f"mt{i:02d}": (22.5, 58.0) for i in range(1, 7)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--user")
    ap.add_argument("--password")
    ap.add_argument("--interval", type=float, default=10)
    ap.add_argument("--scenario", choices=["normal", "alarm"], default="normal")
    ap.add_argument("--rounds", type=int, default=0, help="0 = chạy mãi")
    a = ap.parse_args()

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="smartlab-simulator")
    if a.user:
        c.username_pw_set(a.user, a.password)
    c.connect(a.host, a.port)
    c.loop_start()

    n = 0
    while True:
        n += 1
        for code, base in FRIDGES.items():
            if a.scenario == "alarm" and code == "sh63_mat":
                continue  # giả lập mất tín hiệu
            t = base + random.uniform(-0.3, 0.3)
            if a.scenario == "alarm" and code == "sh25_mat":
                t = min(5.0 + n * 0.4, 11.0)
            c.publish(f"zigbee2mqtt/hs/{code}", json.dumps(
                {"temperature": round(t, 1), "battery": 90, "linkquality": random.randint(60, 160)}), qos=1)
        for code, (t, h) in ROOMS.items():
            c.publish(f"zigbee2mqtt/hs/{code}", json.dumps(
                {"temperature": round(t + random.uniform(-0.5, 0.5), 1),
                 "humidity": round(h + random.uniform(-2, 2), 1), "battery": 95}), qos=1)
        for code in ("ro01", "ro02"):
            c.publish(f"smartlab/hs/{code}/main/value", f"{random.uniform(0.6, 1.4):.2f}", qos=1)
            c.publish(f"smartlab/hs/{code}/main/error", "no error", qos=1)
        c.publish("smartlab/hs/ro03", json.dumps({"ec": round(random.uniform(0.5, 0.9), 2)}), qos=1)
        for code, st in (("xn01", "running"), ("xn02", "standby")):
            c.publish(f"smartlab/hs/{code}", json.dumps({
                "status": st, "last_result": datetime.now(VN).strftime("%H:%M"), "samples_today": 200 + n,
                "tat_median_min": random.randint(35, 45), "iqc": "Đạt"}), qos=1)
        print(f"Vòng {n}: đã gửi 23 điểm")
        if a.rounds and n >= a.rounds:
            break
        time.sleep(a.interval)
    time.sleep(1)
    c.loop_stop()


if __name__ == "__main__":
    main()
