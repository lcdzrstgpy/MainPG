from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA_SQL = """
-- 数据库迁移记录表：记录哪些模块迁移已经执行，避免重复执行建表脚本。
CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id TEXT PRIMARY KEY,
    module TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 工作区/团队表：所有业务数据优先通过 workspace_id 做隔离。
CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    workspace_code TEXT NOT NULL UNIQUE,
    workspace_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 用户表：承接正式注册登录模块，也给其他业务表提供 created_by / owner_user_id。
CREATE TABLE IF NOT EXISTS customer_users (
    user_id TEXT PRIMARY KEY,
    remote_customer_id TEXT NOT NULL DEFAULT '',
    username TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'operator',
    workspace_id TEXT NOT NULL DEFAULT 'default',
    account_status TEXT NOT NULL DEFAULT 'active',
    remote_session_expires_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (workspace_id, username),
    FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id)
);

CREATE INDEX IF NOT EXISTS idx_customer_users_workspace
    ON customer_users (workspace_id, role, account_status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_customer_users_email_unique
    ON customer_users (email)
    WHERE email <> '';

-- 登录会话表：只保存 token_hash，不保存明文 token。
CREATE TABLE IF NOT EXISTS customer_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    revoked_at TEXT NOT NULL DEFAULT '',
    last_used_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    user_agent TEXT NOT NULL DEFAULT '',
    client_ip TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (user_id) REFERENCES customer_users (user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_customer_sessions_user_active
    ON customer_sessions (user_id, expires_at, revoked_at);

-- 店铺表：给每日运营、产品处理、利润活动、核价及货源等模块统一关联店铺。
CREATE TABLE IF NOT EXISTS stores (
    store_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    store_name TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT '',
    site_code TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (workspace_id, platform, site_code, store_name),
    FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id)
);

CREATE INDEX IF NOT EXISTS idx_stores_workspace
    ON stores (workspace_id, platform, site_code, status);

-- 普通配置表：保存可以返回给前端展示的 JSON，例如模型、并发数、COS bucket。
CREATE TABLE IF NOT EXISTS workbench_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

-- 密钥表：保存 API Key、COS Secret 等敏感值，接口只返回是否已配置。
CREATE TABLE IF NOT EXISTS secret_values (
    scope TEXT NOT NULL,
    name TEXT NOT NULL,
    ciphertext TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (scope, name)
);

-- 操作日志表：记录系统配置保存、发布等管理动作，便于后续审计。
CREATE TABLE IF NOT EXISTS action_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id TEXT NOT NULL DEFAULT '',
    workspace_id TEXT NOT NULL DEFAULT '',
    module TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    request_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def _module_migrations() -> list[tuple[str, str, str]]:
    """Return module SQL migrations managed by the shared local runtime database."""
    root = Path(__file__).resolve().parent
    migrations: list[tuple[str, str, str]] = []
    daily_selection_sql = root / "data_collection" / "migrations" / "001_daily_selection.sql"
    if daily_selection_sql.exists():
        migrations.append(
            (
                "data_collection:001_daily_selection",
                "data_collection",
                daily_selection_sql.read_text(encoding="utf-8"),
            )
        )
    return migrations


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate_core_schema(conn: sqlite3.Connection) -> None:
    """Keep local SQLite files created by earlier dev builds usable."""
    _ensure_column(conn, "action_logs", "workspace_id", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "action_logs", "module", "TEXT NOT NULL DEFAULT ''")


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database_path, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL 适合本地桌面运行时：读写互不容易阻塞，和开发文档的 SQLite WAL 保持一致。
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        conn.executescript(SCHEMA_SQL)
        _migrate_core_schema(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO workspaces (workspace_id, workspace_code, workspace_name, status)
            VALUES ('default', 'local-demo', '本地演示工作区', 'active')
            """
        )
        for migration_id, module, sql in _module_migrations():
            exists = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE migration_id = ?",
                (migration_id,),
            ).fetchone()
            if exists:
                continue
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations (migration_id, module) VALUES (?, ?)",
                (migration_id, module),
            )
        conn.commit()
    finally:
        conn.close()


@contextmanager
def transaction(database_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(database_path)
    try:
        conn.execute("BEGIN")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
