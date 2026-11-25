"""Đọc số đo từ API HTTP (vd máy RO Roche: GET + Bearer token) -> đưa vào như số đo thật.

Cấu hình ở trang /cai-dat/api: mỗi nguồn = mã điểm đo + URL + token + (tùy chọn) đường dẫn trường số + chu kỳ.
Không ghi đường dẫn thì tự tìm số: ưu tiên các khóa ec / value / val / conductivity / data / result.
"""
import json
import logging
import time

import httpx
from sqlalchemy import select

from . import service, settings
from .models import Point

log = logging.getLogger("smartlab.api")
METRIC = {"ro": "ec", "fridge": "temperature", "freezer": "temperature", "room": "temperature"}
PREFERRED = ("ec", "value", "val", "conductivity", "cond", "data", "result", "values", "payload")


def fetch(src, timeout=20):
    headers = {"Accept": "application/json"}
    if src.get("token"):
        headers["Authorization"] = "Bearer " + src["token"]
    try:
        r = httpx.get(src["url"], headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        raise RuntimeError(f"không gọi được API: {e}") from e
    if r.status_code != 200:
        raise RuntimeError(f"API trả lỗi {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError:
        return r.text.strip()


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip().replace(",", "."))
        except ValueError:
            return None
    return None


def extract(data, path=""):
    """Lấy số từ dữ liệu API. path dạng 'data.0.value'; trống = tự tìm."""
    if path:
        cur = data
        for part in path.split("."):
            if isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            elif isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                raise RuntimeError(f"không thấy trường '{path}' trong dữ liệu API")
        n = _num(cur)
        if n is None:
            raise RuntimeError(f"trường '{path}' không phải số: {cur!r}")
        return n
    found = _auto(data, 0)
    if found is None:
        raise RuntimeError("không tìm thấy số trong dữ liệu API, hãy ghi đường dẫn trường")
    return found


def _auto(d, depth):
    n = _num(d)
    if n is not None or depth > 4:
        return n
    if isinstance(d, dict):
        keys = sorted(d, key=lambda k: (str(k).lower() not in PREFERRED,
                                        PREFERRED.index(str(k).lower()) if str(k).lower() in PREFERRED else 0))
        for k in keys:
            if str(k).lower() in ("id", "device_id", "timestamp", "time", "ts", "iat", "exp", "status", "code"):
                continue
            v = _auto(d[k], depth + 1)
            if v is not None:
                return v
    if isinstance(d, list):
        for v in d[-1:] + d[:-1]:            # ưu tiên phần tử cuối (thường là số mới nhất)
            r = _auto(v, depth + 1)
            if r is not None:
                return r
    return None


class Poller:
    def __init__(self):
        self.last = {}

    def due(self, session, now=None):
        """Các nguồn tới hạn gọi: [(src, topic, metric)]. Chỉ đọc CSDL, không gọi mạng."""
        now = now or time.monotonic()
        out = []
        for src in settings.get(session, "api")["sources"]:
            code = src.get("code")
            if not (code and src.get("url")):
                continue
            if now - self.last.get(code, -1e9) < max(1, int(src.get("every_min") or 5)) * 60:
                continue
            self.last[code] = now
            p = session.scalar(select(Point).where(Point.code == code, Point.active.is_(True)))
            if p is not None and p.group in METRIC:
                out.append((src, p.topic, METRIC[p.group]))
        return out

    @staticmethod
    def read(jobs):
        """Gọi API (không giữ khóa CSDL): [(topic, payload)]."""
        out = []
        for src, topic, metric in jobs:
            try:
                out.append((topic, json.dumps({metric: extract(fetch(src), src.get("path", ""))}).encode()))
            except RuntimeError as e:
                log.warning("API %s: %s", src.get("code"), e)
        return out

    @staticmethod
    def apply(session, results):
        events = []
        for topic, payload in results:
            events += service.handle_message(session, topic, payload)
        return events

    def tick(self, session, now=None):
        results = self.read(self.due(session, now))
        return len(results), self.apply(session, results)


def api_codes(session):
    return {s["code"] for s in settings.get(session, "api")["sources"] if s.get("code") and s.get("url")}
