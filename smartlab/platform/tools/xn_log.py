"""Đọc log Roche CommServer (cobas pro) -> gửi số liệu máy xét nghiệm lên dashboard qua MQTT.

Log dạng: ...\\CommServer\\800--3.log, mỗi bản tin XML của máy có trong log:
  <SampleResult> <Sample SampleID=.. SampleArrivalDateTime=.. Control="false|true">
                    <Result DateTime=.. UniqueID=..> <SymmetricTargetValue Target=.. StandardDeviation=../>
                    <ResultPart Part="1">giá trị</ResultPart>
  <InstrumentStatus> <Instrument InstrumentState="7" InstrumentStateDescription="Operation"/>

Tính cho từng máy, theo NGÀY HÔM NAY (giờ máy chủ):
  samples_today  : số mã mẫu bệnh nhân khác nhau có kết quả
  last_result    : giờ kết quả gần nhất (HH:MM)
  tat_median_min : trung vị (kết quả cuối của mẫu − giờ mẫu vào máy), phút = TAT TRONG MÁY
  iqc            : mẫu QC trong ngày theo z = (giá trị − đích)/SD: mọi |z|≤2 "Đạt"; có 2<|z|≤3 "Cảnh báo";
                   có |z|>3 "Không đạt"; chưa có QC "Chưa chạy"   (đánh giá Westgard đầy đủ ở phần mềm IQC)
  status         : từ InstrumentStatus gần nhất (Operation = Đang chạy, Stand by = Chờ mẫu...)

Chỉ đọc phần MỚI ghi thêm của log (nhớ vị trí đọc trong file trạng thái); log bị xoay vòng thì đọc lại từ đầu.
Cấu hình: xn_log.ini (mẫu tools/xn_log.example.ini).
    python tools\\xn_log.py --once     # đọc 1 lần, in bản tin, không gửi
    python tools\\xn_log.py            # chạy liên tục (dịch vụ SmartLab-XNLog)
"""
import argparse
import configparser
import json
import logging
import os
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smartlab import config  # noqa: E402

log = logging.getLogger("xn_log")

BLOCK = re.compile(r"<(SampleResult|InstrumentStatus)\b.*?</\1>", re.S)
SAMPLE = re.compile(r"<Sample\b([^>]*)>(.*?)</Sample>", re.S)
RESULT = re.compile(r"<Result\b([^>]*)>(.*?)</Result>", re.S)
ATTR = re.compile(r'(\w+)="([^"]*)"')
VALUE = re.compile(r'<ResultPart Part="1"[^>]*>([^<]*)</ResultPart>')
TARGET = re.compile(r'<SymmetricTargetValue Target="([^"]*)" StandardDeviation="([^"]*)"')
STATE = re.compile(r'InstrumentState="(\d+)" InstrumentStateDescription="([^"]*)"')
MAX_CARRY = 4 * 1024 * 1024

STATE_MAP = {"Operation": "running", "Operation(CLAS)": "running", "Stand by": "standby",
             "Preparation": "Chuẩn bị", "Reagent registration": "Nạp thuốc thử", "Maintenance": "maintenance",
             "Sleep": "Ngủ", "Stop": "error", "Sampling stop": "Dừng hút mẫu"}


def _ts(s):
    try:
        return datetime.strptime(s[:14], "%Y%m%d%H%M%S")
    except (TypeError, ValueError):
        return None


def _f(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def new_day(day):
    return {"day": day, "uids": [], "samples": {}, "qc_z": [], "last_result": None}


class Analyzer:
    """Trạng thái tích lũy của một máy (lưu được ra JSON)."""

    def __init__(self, data=None):
        d = data or {}
        self.offset = d.get("offset", 0)
        self.status = d.get("status")
        self.today = d.get("today") or new_day(None)

    def to_json(self):
        return {"offset": self.offset, "status": self.status, "today": self.today}

    def _roll(self, day):
        if self.today["day"] != day:
            self.today = new_day(day)

    def feed(self, text, today):
        """Xử lý đoạn log mới. Trả về phần đuôi chưa trọn bản tin (đọc tiếp lần sau)."""
        self._roll(today)
        uids = set(self.today["uids"])
        end = 0
        for m in BLOCK.finditer(text):
            end = m.end()
            block = m.group(0)
            if m.group(1) == "InstrumentStatus":
                st = STATE.search(block)
                if st:
                    self.status = STATE_MAP.get(st.group(2), st.group(2))
                continue
            for sattr, sbody in SAMPLE.findall(block):
                sa = dict(ATTR.findall(sattr))
                control = sa.get("Control") == "true"
                for rattr, rbody in RESULT.findall(sbody):
                    ra = dict(ATTR.findall(rattr))
                    if ra.get("UniqueID", "").endswith("#COUNT"):
                        continue          # số đếm thô của module miễn dịch (e801), không phải kết quả
                    when = _ts(ra.get("DateTime"))
                    uid = ra.get("UniqueID") or f'{sa.get("SampleID")}#{ra.get("TestCode")}#{ra.get("DateTime")}'
                    if when is None or when.date().isoformat() != today or uid in uids:
                        continue          # bản tin lặp lại (log ghi 2 lần) hoặc không thuộc hôm nay
                    uids.add(uid)
                    if self.today["last_result"] is None or when.isoformat() > self.today["last_result"]:
                        self.today["last_result"] = when.isoformat()
                    if control:
                        tv, val = TARGET.search(rbody), VALUE.search(rbody)
                        if tv and val:
                            target, sd, v = _f(tv.group(1)), _f(tv.group(2)), _f(val.group(1))
                            if None not in (target, sd, v) and sd > 0:
                                self.today["qc_z"].append(round((v - target) / sd, 2))
                        continue
                    sid = sa.get("SampleID") or "?"
                    arrival = _ts(sa.get("SampleArrivalDateTime"))
                    s = self.today["samples"].setdefault(sid, [arrival.isoformat() if arrival else None, None])
                    if s[1] is None or when.isoformat() > s[1]:
                        s[1] = when.isoformat()
        self.today["uids"] = sorted(uids)
        rest = text[end:]
        return rest[-MAX_CARRY:]

    def payload(self, today):
        self._roll(today)
        t = self.today
        out = {"samples_today": len(t["samples"])}
        if t["last_result"]:
            out["last_result"] = t["last_result"][11:16]
        tats = []
        for arrival, last in t["samples"].values():
            if arrival and last:
                m = (datetime.fromisoformat(last) - datetime.fromisoformat(arrival)).total_seconds() / 60
                if 0 <= m < 24 * 60:
                    tats.append(m)
        if tats:
            out["tat_median_min"] = round(statistics.median(tats))
        z = [abs(x) for x in t["qc_z"]]
        if not z:
            out["iqc"] = "Chưa chạy"
        elif max(z) > 3:
            out["iqc"] = "Không đạt"
        elif max(z) > 2:
            out["iqc"] = "Cảnh báo"
        else:
            out["iqc"] = "Đạt"
        if self.status:
            out["status"] = self.status
        return out


def read_new(path, an, carry, today):
    """Đọc phần mới của file log, cập nhật an. Trả về carry mới."""
    size = os.path.getsize(path)
    if size < an.offset:                 # log đã xoay vòng / tạo file mới
        log.info("%s: log mới, đọc lại từ đầu", path)
        an.offset, carry = 0, ""
    if size == an.offset:
        return carry
    with open(path, "rb") as f:
        f.seek(an.offset)
        data = f.read(size - an.offset)
    an.offset = size
    return an.feed(carry + data.decode("utf-8", "replace"), today)


def load_ini(path):
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
    cp.optionxform = str
    if not cp.read(path, encoding="utf-8-sig"):
        sys.exit(f"Không thấy {path} (chép từ tools/xn_log.example.ini).")
    return cp


def connect_share(chung, paths):
    """Đăng nhập thư mục chia sẻ (dịch vụ chạy tài khoản SYSTEM không tự có quyền)."""
    user, pw = chung.get("share_user", ""), chung.get("share_password", "")
    if not user:
        return
    for share in {"\\".join(p.split("\\")[:4]) for p in paths if p.startswith("\\\\")}:
        r = subprocess.run(["net", "use", share, pw, f"/user:{user}", "/persistent:no"],
                           capture_output=True, text=True)
        if r.returncode != 0 and "1219" not in (r.stderr or ""):   # 1219 = đã kết nối sẵn
            log.warning("net use %s: %s", share, (r.stderr or r.stdout).strip())


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(os.getcwd(), "xn_log.ini"))
    ap.add_argument("--state", default=os.path.join(os.getcwd(), "data_xn_log.json"))
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--day", help="(kiểm tra) tính cho ngày YYYY-MM-DD thay vì hôm nay")
    a = ap.parse_args()
    cp = load_ini(a.config)
    chung = cp["chung"] if cp.has_section("chung") else {}
    machines = {n: cp[n] for n in cp.sections() if n != "chung"}
    connect_share(chung, [m.get("log", "") for m in machines.values()])
    state = {}
    if os.path.isfile(a.state) and not a.once:
        try:
            with open(a.state, encoding="utf-8") as f:
                state = json.load(f)
        except ValueError:
            state = {}
    ans = {n: Analyzer(state.get(n)) for n in machines}
    carry = {n: "" for n in machines}
    client = None
    if not a.once:
        import paho.mqtt.client as mqtt
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="smartlab-xn-log")
        if config.MQTT_USER:
            client.username_pw_set(config.MQTT_USER, config.MQTT_PASSWORD)
        client.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
        client.loop_start()
    interval = int(chung.get("interval_sec", "60"))
    while True:
        today = a.day or datetime.now().date().isoformat()
        for n, sec in machines.items():
            try:
                carry[n] = read_new(sec["log"], ans[n], carry[n], today)
            except OSError as e:
                log.warning("[%s] không đọc được %s: %s", n, sec.get("log"), e)
                continue
            topic = sec.get("topic", f"smartlab/hs/{n}")
            payload = json.dumps(ans[n].payload(today), ensure_ascii=False)
            if client is None:
                print(f"{topic}  {payload}")
            else:
                client.publish(topic, payload, qos=1)
                log.info("%s %s", topic, payload)
        if a.once:
            return
        tmp = a.state + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({n: an.to_json() for n, an in ans.items()}, f)
        os.replace(tmp, a.state)
        time.sleep(interval)


if __name__ == "__main__":
    main()
