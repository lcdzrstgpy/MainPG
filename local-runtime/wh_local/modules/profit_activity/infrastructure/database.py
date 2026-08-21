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


def create_database(database_url: str | Path | None = None) -> ProfitActivityDatabase:
    """建立 SQLite 连接，并为每个连接启用 WAL、外键和忙等待。"""
    url = _database_url(database_url)
    parsed = make_url(url)
    engine = create_engine(url, future=True, connect_args={"check_same_thread": False} if parsed.drivername == "sqlite" else {})
    if parsed.drivername == "sqlite":
        _configure_sqlite(engine)
    Base.metadata.create_all(engine)
    _migrate_legacy_tables(engine)
    return ProfitActivityDatabase(engine=engine, sessions=sessionmaker(engine, expire_on_commit=False))


def _database_url(database_url: str | Path | None) -> str:
    if isinstance(database_url, Path):
        database_url.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{database_url.as_posix()}"
    return database_url or os.getenv("PROFIT_ACTIVITY_DATABASE_URL") or _default_database_url()


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


def _migrate_legacy_tables(engine: Engine) -> None:
    """Keep local databases created by earlier module versions usable."""
    additions = {
        "profit_activity_settings": {
            "workspace_id": "TEXT NOT NULL DEFAULT 'default'",
            "save_root": "TEXT NOT NULL DEFAULT ''",
            "activity_threshold_configured": "BOOLEAN NOT NULL DEFAULT 0",
        },
        "profit_activity_records": {
            "workspace_id": "TEXT NOT NULL DEFAULT 'default'",
            "visibility": "TEXT NOT NULL DEFAULT 'shared'",
            "source_type": "TEXT NOT NULL DEFAULT 'manual'",
            "store_name": "TEXT NOT NULL DEFAULT ''",
            "created_by": "TEXT NOT NULL DEFAULT ''",
            "created_by_username": "TEXT NOT NULL DEFAULT 'local'",
            "image_path": "TEXT NOT NULL DEFAULT ''",
            "attachment_image_path": "TEXT NOT NULL DEFAULT ''",
            "source_main_image_url": "TEXT NOT NULL DEFAULT ''",
            "source_image_path": "TEXT NOT NULL DEFAULT ''",
            "source_groups_json": "TEXT NOT NULL DEFAULT '[]'",
            "source_url": "TEXT NOT NULL DEFAULT ''",
            "refund_rate": "NUMERIC NOT NULL DEFAULT 0",
        },
        "profit_activity_runs": {"workspace_id": "TEXT NOT NULL DEFAULT 'default'"},
        "profit_activity_decisions": {"workspace_id": "TEXT NOT NULL DEFAULT 'default'"},
        "profit_activity_import_sessions": {"workspace_id": "TEXT NOT NULL DEFAULT 'default'"},
        "profit_activity_import_tasks": {"workspace_id": "TEXT NOT NULL DEFAULT 'default'"},
        "profit_activity_filter_tasks": {"workspace_id": "TEXT NOT NULL DEFAULT 'default'"},
    }
    if engine.dialect.name != "sqlite":
        return
    threshold_state_added = False
    with engine.begin() as connection:
        for table, columns in additions.items():
            existing = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, definition in columns.items():
                if name not in existing:
                    connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                    if table == "profit_activity_settings" and name == "activity_threshold_configured":
                        threshold_state_added = True
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_profit_activity_records_workspace_store "
            "ON profit_activity_records (workspace_id, store_name)"
        )
        if threshold_state_added:
            connection.exec_driver_sql(
                """
                UPDATE profit_activity_settings
                SET activity_threshold_configured = CASE
                        WHEN CAST(activity_min_net_profit AS REAL) = 8.0
                         AND CAST(activity_profit_rate_threshold AS REAL) = 0.2 THEN 0
                        ELSE 1
                    END,
                    activity_min_net_profit = CASE
                        WHEN CAST(activity_min_net_profit AS REAL) = 8.0
                         AND CAST(activity_profit_rate_threshold AS REAL) = 0.2 THEN 0
                        ELSE activity_min_net_profit
                    END,
                    activity_profit_rate_threshold = CASE
                        WHEN CAST(activity_min_net_profit AS REAL) = 8.0
                         AND CAST(activity_profit_rate_threshold AS REAL) = 0.2 THEN 0
                        ELSE activity_profit_rate_threshold
                    END
                """
            )
