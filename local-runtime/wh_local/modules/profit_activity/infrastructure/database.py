from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from .orm import Base


@dataclass(frozen=True)
class ProfitActivityDatabase:
    engine: Engine
    sessions: sessionmaker[Session]

    def dispose(self) -> None:
        self.engine.dispose()


def create_database(database_url: str | None = None) -> ProfitActivityDatabase:
    """建立 SQLite 连接，并为每个连接启用 WAL、外键和忙等待。"""
    url = database_url or os.getenv("PROFIT_ACTIVITY_DATABASE_URL") or _default_database_url()
    parsed = make_url(url)
    engine = create_engine(url, future=True, connect_args={"check_same_thread": False} if parsed.drivername == "sqlite" else {})
    if parsed.drivername == "sqlite":
        _configure_sqlite(engine)
    Base.metadata.create_all(engine)
    return ProfitActivityDatabase(engine=engine, sessions=sessionmaker(engine, expire_on_commit=False))


def _default_database_url() -> str:
    root = Path(__file__).resolve().parents[5]
    database_path = root / "real-workbench" / "employee_workbench" / "data" / "profit_activity.db"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{database_path.as_posix()}"


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def on_connect(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()
