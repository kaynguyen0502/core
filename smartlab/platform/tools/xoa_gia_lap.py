"""Xóa toàn bộ số đo GIẢ LẬP (readings.simulated, log_entries.simulated) và tắt giả lập. Có sao lưu trước khi xóa.

    nssm stop SmartLab-Worker
    C:\\SmartLabHS\\venv\\Scripts\\python.exe tools\\xoa_gia_lap.py          # chỉ đếm
    C:\\SmartLabHS\\venv\\Scripts\\python.exe tools\\xoa_gia_lap.py --yes    # xóa thật
    nssm start SmartLab-Worker
"""
import argparse
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, func, select  # noqa: E402

from smartlab import config, db, settings  # noqa: E402
from smartlab.models import LogEntry, PointState, Reading  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args(argv)
    bind = db.SessionLocal.kw.get("bind")
    db.init_db(bind)
    with db.SessionLocal() as s:
        n_r = s.scalar(select(func.count()).select_from(Reading).where(Reading.simulated.is_(True)))
        n_l = s.scalar(select(func.count()).select_from(LogEntry).where(LogEntry.simulated.is_(True)))
        print(f"Số đo giả lập: {n_r} dòng; dòng sổ theo dõi từ số giả lập: {n_l}")
        if not a.yes:
            print("Chưa xóa gì. Thêm --yes để xóa thật.")
            return 1
        url = str(bind.url) if bind is not None else config.DATABASE_URL
        if url.startswith("sqlite:///") and os.path.isfile(url[len("sqlite:///"):]):
            path = url[len("sqlite:///"):]
            bak = f"{path}.truoc-xoa-gia-lap-{datetime.now():%Y%m%d-%H%M}.bak"
            shutil.copy2(path, bak)
            print("Đã sao lưu:", bak)
        s.execute(delete(Reading).where(Reading.simulated.is_(True)))
        s.execute(delete(LogEntry).where(LogEntry.simulated.is_(True)))
        for st in s.scalars(select(PointState)):
            if (st.extra or {}).get("simulated"):
                st.extra = {**st.extra, "simulated": False}
                st.values, st.last_seen = {}, None          # ô sẽ về "Chưa có dữ liệu" tới khi có số đo thật
        s.commit()
        settings.put(s, "simulate", {"values": {}}, "xoa_gia_lap")
        print("Đã xóa số đo giả lập và tắt giả lập.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
