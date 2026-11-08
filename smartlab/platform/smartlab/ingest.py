"""Đọc bản tin MQTT -> (topic gốc của điểm đo, số đo, thông tin phụ).

Hỗ trợ 3 dạng:
- Zigbee2MQTT : zigbee2mqtt/hs/tl01  {"temperature": 4.6, "humidity": 58, "battery": 90, "linkquality": 120}
- Camera AI-on-the-edge : smartlab/hs/ro01/main/value "0.82" ; .../main/error "no error"
- SmartLab chung (API, LIS, máy XN) : smartlab/hs/xn01 {"status": "running", "samples_today": 412, ...}
"""
import json

METRICS = ("temperature", "humidity", "ec")
_Z2M_IGNORE_SUFFIX = ("/set", "/get", "/availability")
_AIOE_VALUE = "/main/value"
_AIOE_ERROR = "/main/error"


def _num(v):
    try:
        f = float(str(v).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # loại NaN


def parse(topic: str, payload: bytes):
    """Trả về (base_topic, values, extra) hoặc None nếu bản tin không phải số đo."""
    text = payload.decode("utf-8", "replace").strip()

    if topic.startswith("zigbee2mqtt/bridge"):
        return None
    if topic.endswith(_AIOE_VALUE):
        v = _num(text)
        if v is None:
            return None
        return topic[: -len(_AIOE_VALUE)], {"ec": v}, {}
    if topic.endswith(_AIOE_ERROR):
        msg = "" if text.lower() in ("", "no error") else text
        return topic[: -len(_AIOE_ERROR)], {}, {"read_error": msg}
    if topic.endswith(_Z2M_IGNORE_SUFFIX):
        return None
    # Các nhánh con khác của camera (rate, raw, json, timestamp...) bỏ qua
    if "/main/" in topic:
        return None

    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None

    values, extra = {}, {}
    for k, v in data.items():
        if k in METRICS:
            n = _num(v)
            if n is not None:
                values[k] = n
        elif k in ("battery", "linkquality", "voltage", "status", "last_result", "samples_today",
                   "tat_median_min", "iqc", "message"):
            extra[k] = v
    if not values and not extra:
        return None
    return topic, values, extra
