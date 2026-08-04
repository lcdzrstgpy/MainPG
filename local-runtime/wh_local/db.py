from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA_SQL = """
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
    action TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    request_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


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
    with connect(database_path) as conn:
        conn.executescript(SCHEMA_SQL)


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
