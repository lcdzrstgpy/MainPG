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
            "us_domestic_fee": "NUMERIC NOT NULL DEFAULT 2.5",
            "us_shipping_subsidy": "NUMERIC NOT NULL DEFAULT 21",
            "us_refund_rate": "NUMERIC NOT NULL DEFAULT 0.05",
            "co_domestic_fee": "NUMERIC NOT NULL DEFAULT 2.5",
            "co_shipping_subsidy": "NUMERIC NOT NULL DEFAULT 21",
            "co_refund_rate": "NUMERIC NOT NULL DEFAULT 0.05",
            "pe_first_mile_rate": "NUMERIC NOT NULL DEFAULT 80",
            "pe_first_mile_fixed": "NUMERIC NOT NULL DEFAULT 0",
            "pe_domestic_fee": "NUMERIC NOT NULL DEFAULT 2.5",
            "pe_shipping_subsidy": "NUMERIC NOT NULL DEFAULT 21",
            "pe_refund_rate": "NUMERIC NOT NULL DEFAULT 0.05",
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
    site_fee_columns_added = False
    with engine.begin() as connection:
        for table, columns in additions.items():
            existing = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, definition in columns.items():
                if name not in existing:
                    connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                    if table == "profit_activity_settings" and name == "activity_threshold_configured":
                        threshold_state_added = True
                    if table == "profit_activity_settings" and name in {
                        "us_domestic_fee", "us_shipping_subsidy", "us_refund_rate",
                        "co_domestic_fee", "co_shipping_subsidy", "co_refund_rate",
                        "pe_first_mile_rate", "pe_first_mile_fixed", "pe_domestic_fee",
                        "pe_shipping_subsidy", "pe_refund_rate",
                    }:
                        site_fee_columns_added = True
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
        if site_fee_columns_added:
            connection.exec_driver_sql(
                """
                UPDATE profit_activity_settings
                SET us_first_mile_rate = CASE WHEN CAST(us_first_mile_rate AS REAL) = 0 THEN 72 ELSE us_first_mile_rate END,
                    us_first_mile_fixed = CASE WHEN CAST(us_first_mile_fixed AS REAL) = 0 THEN 5 ELSE us_first_mile_fixed END,
                    co_first_mile_rate = CASE WHEN CAST(co_first_mile_rate AS REAL) IN (0, 80) THEN 70 ELSE co_first_mile_rate END,
                    ec_first_mile_rate = CASE WHEN CAST(ec_first_mile_rate AS REAL) = 0 THEN 108 ELSE ec_first_mile_rate END,
                    ec_domestic_fee = CASE WHEN CAST(ec_domestic_fee AS REAL) = 0 THEN 2.5 ELSE ec_domestic_fee END,
                    ec_shipping_subsidy = CASE WHEN CAST(ec_shipping_subsidy AS REAL) = 0 THEN 15 ELSE ec_shipping_subsidy END,
                    ec_shipping_subsidy_price_limit = CASE WHEN CAST(ec_shipping_subsidy_price_limit AS REAL) = 0 THEN 120 ELSE ec_shipping_subsidy_price_limit END,
                    ec_end_fee = CASE WHEN CAST(ec_end_fee AS REAL) = 0 THEN 27 ELSE ec_end_fee END,
                    ec_refund_rate = CASE WHEN CAST(ec_refund_rate AS REAL) = 0 THEN 0.05 ELSE ec_refund_rate END,
                    us_domestic_fee = CASE WHEN CAST(domestic_fee AS REAL) = 0 THEN 2.5 ELSE domestic_fee END,
                    us_shipping_subsidy = CASE WHEN CAST(shipping_subsidy AS REAL) = 0 THEN 21 ELSE shipping_subsidy END,
                    us_refund_rate = CASE WHEN CAST(refund_rate AS REAL) = 0 THEN 0.05 ELSE refund_rate END,
                    co_domestic_fee = CASE WHEN CAST(domestic_fee AS REAL) = 0 THEN 2.5 ELSE domestic_fee END,
                    co_shipping_subsidy = CASE WHEN CAST(shipping_subsidy AS REAL) = 0 THEN 21 ELSE shipping_subsidy END,
                    co_refund_rate = CASE WHEN CAST(refund_rate AS REAL) = 0 THEN 0.05 ELSE refund_rate END,
                    pe_first_mile_rate = CASE WHEN CAST(pe_first_mile_rate AS REAL) = 0 THEN 80 ELSE pe_first_mile_rate END,
                    pe_domestic_fee = CASE WHEN CAST(pe_domestic_fee AS REAL) = 0 THEN 2.5 ELSE pe_domestic_fee END,
                    pe_shipping_subsidy = CASE WHEN CAST(pe_shipping_subsidy AS REAL) = 0 THEN 21 ELSE pe_shipping_subsidy END,
                    pe_refund_rate = CASE WHEN CAST(pe_refund_rate AS REAL) = 0 THEN 0.05 ELSE pe_refund_rate END
                """
            )
