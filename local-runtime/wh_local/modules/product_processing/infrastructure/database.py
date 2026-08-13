from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from .orm import Base
from . import dimension_canvas_orm as _dimension_canvas_orm  # noqa: F401


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
    _ensure_columns(engine)
    return ProductProcessingDatabase(engine, sessionmaker(engine, expire_on_commit=False))


# 轻量列补齐：老库已建表时 create_all 不会新增列，这里按需 ALTER TABLE 补列。
_MIGRATION_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "product_processing_drafts": [
        ("preview_revision", "INTEGER NOT NULL DEFAULT 0"),
        ("preview_overrides_json", "TEXT NOT NULL DEFAULT '{}'"),
    ],
    "product_processing_dimension_items": [
        ("render_input_hash", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("rendered_input_hash", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("publish_claim_token", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("publish_claimed_at", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ],
}


def _ensure_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table, columns in _MIGRATION_COLUMNS.items():
        if table not in existing_tables:
            continue
        existing = {column["name"] for column in inspector.get_columns(table)}
        with engine.begin() as connection:
            for name, definition in columns:
                if name not in existing:
                    connection.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                    )


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def on_connect(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()
