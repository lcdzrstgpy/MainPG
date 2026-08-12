from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from .orm import Base


@dataclass(frozen=True)
class ProductProcessingDatabase:
    engine: Engine
    sessions: sessionmaker[Session]

    def dispose(self) -> None:
        self.engine.dispose()


def default_storage_root() -> Path:
    repository_root = Path(__file__).resolve().parents[5]
    root = repository_root / "real-workbench" / "employee_workbench" / "product_processing"
    root.mkdir(parents=True, exist_ok=True)
    return root


def default_database_url() -> str:
    database_path = default_storage_root() / "product_processing.sqlite3"
    return f"sqlite:///{database_path.as_posix()}"


def create_database(database_url: str | None = None) -> ProductProcessingDatabase:
    url = database_url or os.getenv("PRODUCT_PROCESSING_DATABASE_URL") or default_database_url()
    parsed = make_url(url)
    connect_args = {"check_same_thread": False} if parsed.drivername == "sqlite" else {}
    engine = create_engine(url, future=True, connect_args=connect_args)
    if parsed.drivername == "sqlite":
        _configure_sqlite(engine)
    Base.metadata.create_all(engine)
    return ProductProcessingDatabase(engine, sessionmaker(engine, expire_on_commit=False))


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def on_connect(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()
