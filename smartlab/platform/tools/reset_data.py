"""Xóa dữ liệu chạy thử trước khi bàn giao (giữ nguyên danh mục điểm đo, ngưỡng, nhật ký sửa danh mục).

Xóa: số đo, sổ theo dõi, sự kiện cảnh báo, trạng thái hiện tại. Không đụng tới Zigbee2MQTT (cảm biến vẫn ghép).
Chạy (dừng worker trước):
    nssm stop SmartLab-Worker
    C:\\SmartLabHS\\venv\\Scripts\\python.exe tools\\reset_data.py --yes
    nssm start SmartLab-Worker
"""
import argparse
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, func, select  # noqa: E402

from smartlab import config, db  # noqa: E402
from smartlab.models import AlarmEvent, LogEntry, PointState, Reading  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="xác nhận xóa")
    a = ap.parse_args(argv)
    db.init_db(db.SessionLocal.kw.get("bind"))
    with db.SessionLocal() as s:
        counts = {m.__tablename__: s.scalar(select(func.count()).select_from(m))
                  for m in (Reading, LogEntry, AlarmEvent, PointState)}
        print("Sẽ xóa:", ", ".join(f"{k}={v}" for k, v in counts.items()))
        if not a.yes:
            print("Chưa xóa gì. Thêm --yes để xóa thật.")
            return 1
        url = config.DATABASE_URL
        if url.startswith("sqlite:///"):
            path = url[len("sqlite:///"):]
            if os.path.isfile(path):
                bak = f"{path}.truoc-ban-giao-{datetime.now():%Y%m%d-%H%M}.bak"
                shutil.copy2(path, bak)
                print("Đã sao lưu:", bak)
        for m in (Reading, LogEntry, AlarmEvent, PointState):
            s.execute(delete(m))
        s.commit()
        print("Đã xóa dữ liệu chạy thử. Chạy lại SmartLab-Worker để bắt đầu ghi mới.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
