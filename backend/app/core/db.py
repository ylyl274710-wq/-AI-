"""数据库引擎与会话。"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,      # 长连接失效后自动重连，避免「数据库异常」直接抛给用户
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False,
                            expire_on_commit=False, class_=Session)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ping() -> tuple[bool, str]:
    """健康检查用：探测数据库与 pgvector 扩展。"""
    try:
        with engine.connect() as conn:
            ver = conn.execute(text("SELECT version()")).scalar_one()
            has_vector = conn.execute(
                text("SELECT count(*) FROM pg_extension WHERE extname='vector'")
            ).scalar_one()
        short = str(ver).split(",")[0]
        return True, f"{short}; pgvector={'yes' if has_vector else 'NO'}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
