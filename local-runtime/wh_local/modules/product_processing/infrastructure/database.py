from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .orm import Base
from . import dimension_canvas_orm as _dimension_canvas_orm  # noqa: F401
from . import preview_image_orm as _preview_image_orm  # noqa: F401
from . import media_asset_orm as _media_asset_orm  # noqa: F401


@dataclass(frozen=True)
class ProductProcessingDatabase:
    engine: Engine
    sessions: sessionmaker[Session]
    shop_intake_lock: RLock = field(default_factory=RLock, repr=False, compare=False)

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
    engine_options: dict[str, object] = {"future": True, "connect_args": connect_args}
    if parsed.drivername == "sqlite" and (parsed.database in {None, "", ":memory:"}):
        # Finalization uses worker threads; an in-memory SQLite database must share
        # one connection or each worker observes an unrelated empty schema.
        engine_options["poolclass"] = StaticPool
    engine = create_engine(url, **engine_options)
    if parsed.drivername == "sqlite":
        _configure_sqlite(engine)
    Base.metadata.create_all(engine)
    _ensure_columns(engine)
    _remove_legacy_candidate_unique_constraint(engine)
    _ensure_shop_candidate_unique_index(engine)
    return ProductProcessingDatabase(engine, sessionmaker(engine, expire_on_commit=False))


# 轻量列补齐：老库已建表时 create_all 不会新增列，这里按需 ALTER TABLE 补列。
_MIGRATION_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "product_processing_drafts": [
        ("preview_revision", "INTEGER NOT NULL DEFAULT 0"),
        ("preview_overrides_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("media_contract_version", "INTEGER NOT NULL DEFAULT 1"),
    ],
    "product_processing_combo_sources": [
        ("is_main", "BOOLEAN NOT NULL DEFAULT 0"),
    ],
    "product_processing_dimension_items": [
        ("render_input_hash", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("rendered_input_hash", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("publish_claim_token", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("publish_claimed_at", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ],
    "product_processing_preview_image_assets": [
        ("workspace_id", "VARCHAR(255) NOT NULL DEFAULT 'local'"),
        ("task_id", "INTEGER NOT NULL DEFAULT 0"),
        ("product_draft_id", "INTEGER NOT NULL DEFAULT 0"),
        ("origin", "VARCHAR(32) NOT NULL DEFAULT 'source'"),
        ("source_asset_id", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("media_asset_id", "VARCHAR(36) NOT NULL DEFAULT ''"),
        ("source_kind", "VARCHAR(32) NOT NULL DEFAULT ''"),
        ("identity_hash", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("access_token", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("managed_path", "TEXT NOT NULL DEFAULT ''"),
        ("source_url", "TEXT NOT NULL DEFAULT ''"),
        ("content_hash", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("content_type", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("byte_size", "INTEGER NOT NULL DEFAULT 0"),
        ("width", "INTEGER NOT NULL DEFAULT 0"),
        ("height", "INTEGER NOT NULL DEFAULT 0"),
        ("availability", "VARCHAR(32) NOT NULL DEFAULT 'materializing'"),
        ("public_url", "TEXT NOT NULL DEFAULT ''"),
        ("error_code", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("error_message", "TEXT NOT NULL DEFAULT ''"),
        ("materialize_claim_token", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("materialize_claimed_at", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("created_at", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("updated_at", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ],
    "product_processing_dimension_assets": [
        ("source_media_asset_id", "VARCHAR(36) NOT NULL DEFAULT ''"),
    ],
    "product_processing_preview_publications": [
        ("status", "VARCHAR(32) NOT NULL DEFAULT 'pending'"),
        ("public_url", "TEXT NOT NULL DEFAULT ''"),
        ("content_type", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("byte_size", "INTEGER NOT NULL DEFAULT 0"),
        ("claim_token", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("claimed_at", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("error_code", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("error_message", "TEXT NOT NULL DEFAULT ''"),
        ("created_at", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("updated_at", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ],
    "product_processing_preview_finalize_runs": [
        ("workspace_id", "VARCHAR(255) NOT NULL DEFAULT 'local'"),
        ("task_id", "INTEGER NOT NULL DEFAULT 0"),
        ("idempotency_key", "VARCHAR(255) NOT NULL DEFAULT ''"),
        ("request_hash", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("snapshot_hash", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("snapshot_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("status", "VARCHAR(32) NOT NULL DEFAULT 'queued'"),
        ("total_count", "INTEGER NOT NULL DEFAULT 0"),
        ("published_count", "INTEGER NOT NULL DEFAULT 0"),
        ("failed_count", "INTEGER NOT NULL DEFAULT 0"),
        ("errors_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("claim_token", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("claimed_at", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("workbook_path", "TEXT NOT NULL DEFAULT ''"),
        ("row_count", "INTEGER NOT NULL DEFAULT 0"),
        ("product_count", "INTEGER NOT NULL DEFAULT 0"),
        ("created_at", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("updated_at", "VARCHAR(64) NOT NULL DEFAULT ''"),
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
    if (
        engine.dialect.name == "sqlite"
        and "product_processing_preview_image_assets" in existing_tables
    ):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE product_processing_preview_image_assets "
                    "SET access_token = lower(hex(randomblob(24))) "
                    "WHERE access_token = ''"
                )
            )
    if (
        engine.dialect.name == "sqlite"
        and "product_processing_preview_finalize_runs" in existing_tables
    ):
        # Older preview-finalization experiments had snapshot identity only.
        # Backfill deterministic keys before adding the request-key boundary.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE product_processing_preview_finalize_runs "
                    "SET idempotency_key = 'snapshot:' || snapshot_hash "
                    "WHERE idempotency_key = ''"
                )
            )
            connection.execute(
                text(
                    "UPDATE product_processing_preview_finalize_runs "
                    "SET request_hash = snapshot_hash WHERE request_hash = ''"
                )
            )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_preview_finalize_idempotency_legacy "
                    "ON product_processing_preview_finalize_runs "
                    "(workspace_id, task_id, idempotency_key)"
                )
            )


def _remove_legacy_candidate_unique_constraint(engine: Engine) -> None:
    """Allow the same source candidate to create one draft per confirmed handoff.

    Older SQLite databases enforced ``UNIQUE(workspace_id, candidate_id)`` at
    table level. SQLite cannot drop that auto-index directly, so rebuild only
    this table in one transaction while preserving every column, row and
    explicit index. Handoff IDs remain unique and are the replay boundary.
    """
    if engine.dialect.name != "sqlite":
        return
    constraint_pattern = re.compile(
        r",\s*(?:CONSTRAINT\s+[\"`\[]?uq_product_processing_workspace_candidate[\"`\]]?\s+)?"
        r"UNIQUE\s*\(\s*workspace_id\s*,\s*candidate_id\s*\)",
        flags=re.IGNORECASE,
    )
    with engine.connect() as connection:
        table_sql = connection.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'product_processing_drafts'"
            )
        ).scalar_one_or_none()
        if not table_sql or constraint_pattern.search(str(table_sql)) is None:
            return
        explicit_indexes = [
            str(row[0])
            for row in connection.execute(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'index' AND tbl_name = 'product_processing_drafts' "
                    "AND sql IS NOT NULL ORDER BY name"
                )
            )
            if row[0]
        ]
        rebuilt_sql, replacements = constraint_pattern.subn("", str(table_sql), count=1)
        if replacements != 1:
            raise RuntimeError("failed to remove legacy product draft candidate constraint")
        temporary_table = "product_processing_drafts__candidate_reentry"
        rebuilt_sql, replacements = re.subn(
            r"^CREATE\s+TABLE\s+[\"`\[]?product_processing_drafts[\"`\]]?",
            f"CREATE TABLE {temporary_table}",
            rebuilt_sql,
            count=1,
            flags=re.IGNORECASE,
        )
        if replacements != 1:
            raise RuntimeError("failed to prepare product draft table migration")
        columns = [
            str(row[1])
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(product_processing_drafts)"
            )
        ]
        quoted_columns = ", ".join(f'"{column}"' for column in columns)
        connection.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            with connection.begin():
                connection.exec_driver_sql(rebuilt_sql)
                connection.exec_driver_sql(
                    f"INSERT INTO {temporary_table} ({quoted_columns}) "
                    f"SELECT {quoted_columns} FROM product_processing_drafts"
                )
                connection.exec_driver_sql("DROP TABLE product_processing_drafts")
                connection.exec_driver_sql(
                    f"ALTER TABLE {temporary_table} RENAME TO product_processing_drafts"
                )
                for index_sql in explicit_indexes:
                    connection.exec_driver_sql(index_sql)
                violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise RuntimeError("product draft candidate migration broke foreign keys")
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
def _ensure_shop_candidate_unique_index(engine: Engine) -> None:
    """Enforce one direct shop draft per workspace/candidate."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_product_processing_shop_candidate "
                "ON product_processing_drafts (workspace_id, candidate_id) "
                "WHERE source_type = 'onebound_api' "
                "AND handoff_id IS NULL AND candidate_id IS NOT NULL"
            )
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
