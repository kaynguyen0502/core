from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config


class Base(DeclarativeBase):
    pass


def make_engine(url=None):
    url = url or config.DATABASE_URL
    kw = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        # Web và tiến trình nền cùng ghi một file: chờ khóa tới 30 giây, bật WAL
        kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
    eng = create_engine(url, **kw)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(conn, _record):
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()
    return eng


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(eng=None):
    from . import models  # noqa: F401  (đăng ký bảng)
    eng = eng or engine
    Base.metadata.create_all(eng)
    _add_missing_columns(eng)


# Cột thêm sau khi đã có dữ liệu (create_all không tự thêm cột vào bảng cũ)
_NEW_COLUMNS = [("readings", "simulated", "BOOLEAN NOT NULL DEFAULT 0"),
                ("log_entries", "simulated", "BOOLEAN NOT NULL DEFAULT 0")]


def _add_missing_columns(eng):
    from sqlalchemy import inspect, text
    insp = inspect(eng)
    with eng.begin() as conn:
        for table, col, ddl in _NEW_COLUMNS:
            if insp.has_table(table) and col not in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
