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

-- 细粒度权限点表：统一收敛各业务模块的权限命名和说明。
CREATE TABLE IF NOT EXISTS permissions (
    permission_key TEXT PRIMARY KEY,
    module TEXT NOT NULL,
    action TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_permissions_module
    ON permissions (module, action);

-- 角色-权限关联表：当前先使用 admin/operator，后续可扩展更多角色。
CREATE TABLE IF NOT EXISTS role_permissions (
    role TEXT NOT NULL,
    permission_key TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (role, permission_key),
    FOREIGN KEY (permission_key) REFERENCES permissions (permission_key) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_role_permissions_permission
    ON role_permissions (permission_key, role);

-- 用户权限覆盖表：用于后续给某个用户单独授予/拒绝权限，当前业务可暂不使用。
CREATE TABLE IF NOT EXISTS user_permission_overrides (
    user_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    permission_key TEXT NOT NULL,
    effect TEXT NOT NULL CHECK (effect IN ('allow', 'deny')),
    reason TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, workspace_id, permission_key),
    FOREIGN KEY (user_id) REFERENCES customer_users (user_id) ON DELETE CASCADE,
    FOREIGN KEY (permission_key) REFERENCES permissions (permission_key) ON DELETE CASCADE
);

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

-- 本地真实账号表：第二阶段账号服务使用。后续迁移 MySQL 时保持字段语义不变。
CREATE TABLE IF NOT EXISTS auth_accounts (
    account_id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'operator',
    workspace_id TEXT NOT NULL DEFAULT 'default',
    account_status TEXT NOT NULL DEFAULT 'active',
    email_verified_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (workspace_id, username),
    FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_accounts_email_unique
    ON auth_accounts (email)
    WHERE email <> '';

CREATE INDEX IF NOT EXISTS idx_auth_accounts_workspace_status
    ON auth_accounts (workspace_id, role, account_status);

-- 本地密码凭据表：只保存密码哈希、盐和算法参数，不保存明文密码。
CREATE TABLE IF NOT EXISTS auth_password_credentials (
    account_id TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    algorithm TEXT NOT NULL DEFAULT 'pbkdf2_sha256',
    iterations INTEGER NOT NULL DEFAULT 200000,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES auth_accounts (account_id) ON DELETE CASCADE
);

-- 登录日志表：记录成功/失败，便于后续风控、审计和问题排查。
CREATE TABLE IF NOT EXISTS auth_login_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL DEFAULT '',
    username TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    success INTEGER NOT NULL,
    failure_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

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
    data_collection_migrations = [
        ("data_collection:001_daily_selection", root / "data_collection" / "migrations" / "001_daily_selection.sql"),
        (
            "data_collection:002_data_collection_plugin_queue",
            root / "data_collection" / "migrations" / "002_data_collection_plugin_queue.sql",
        ),
    ]
    for migration_id, sql_path in data_collection_migrations:
        if sql_path.exists():
            migrations.append(
                (
                    migration_id,
                    "data_collection",
                    sql_path.read_text(encoding="utf-8"),
                )
            )

    product_processing_sql = root / "modules" / "product_processing" / "migrations" / "001_product_processing.sql"
    if product_processing_sql.exists():
        migrations.append(
            (
                "product_processing:001_product_processing",
                "product_processing",
                product_processing_sql.read_text(encoding="utf-8"),
            )
        )
    price_verification_sql = root / "price_verification" / "migrations" / "001_price_verification.sql"
    if price_verification_sql.exists():
        migrations.append(
            (
                "price_verification:001_price_verification",
                "price_verification",
                price_verification_sql.read_text(encoding="utf-8"),
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


DEFAULT_PERMISSIONS: tuple[tuple[str, str, str, str], ...] = (
    ("data_collection.read", "data_collection", "read", "查看选品/采集批次和候选商品"),
    ("data_collection.collect", "data_collection", "collect", "发起单采、批量采集、关键词采集和插件采集"),
    ("data_collection.feedback", "data_collection", "feedback", "提交候选商品反馈或拒绝原因"),
    ("data_collection.confirm", "data_collection", "confirm", "确认候选商品并生成下游 handoff"),
    ("data_collection.plugin", "data_collection", "plugin", "连接浏览器插件并处理插件命令"),
    ("product_processing.read", "product_processing", "read", "查看产品草稿、任务和处理结果"),
    ("product_processing.draft_write", "product_processing", "draft_write", "新增、编辑、导入产品草稿"),
    ("product_processing.draft_delete", "product_processing", "draft_delete", "删除或清理产品草稿"),
    ("product_processing.process", "product_processing", "process", "发起产品处理、预检、重试和恢复任务"),
    ("product_processing.prompt_manage", "product_processing", "prompt_manage", "维护产品处理 AI 提示词"),
    ("product_processing.export", "product_processing", "export", "下载店小秘文件、失败原因和视频清单"),
    ("product_processing.handoff_consume", "product_processing", "handoff_consume", "消费每日选品 handoff 并生成草稿"),
    ("price_verification.read", "price_verification", "read", "查看核价批次、货源匹配批次和证据快照"),
    ("price_verification.quote_collect", "price_verification", "quote_collect", "通过只读插件采集 Temu 核价证据"),
    ("price_verification.sourcing_match", "price_verification", "sourcing_match", "创建 1688 货源匹配任务并查看候选"),
    ("price_verification.export", "price_verification", "export", "导出核价 Excel 和证据报告"),
    ("price_verification.plugin", "price_verification", "plugin", "创建配对码、连接插件会话并处理插件命令"),
    ("seller_listing.read", "seller_listing", "read", "查看卖家中心上架、核价和库存流程数据"),
    ("seller_listing.price_confirm", "seller_listing", "price_confirm", "处理核价、调价和价格待确认产品"),
    ("seller_listing.attribute_write", "seller_listing", "attribute_write", "修改产品属性、详情和库存"),
    ("seller_listing.publish", "seller_listing", "publish", "执行或确认产品上架完成"),
    ("settings.read", "settings", "read", "查看系统配置"),
    ("settings.manage", "settings", "manage", "维护系统配置、密钥和运行参数"),
    ("stores.manage", "stores", "manage", "维护店铺配置和平台站点信息"),
    ("users.manage", "users", "manage", "维护用户、角色和权限"),
)


OPERATOR_PERMISSIONS: frozenset[str] = frozenset(
    {
        "data_collection.read",
        "data_collection.collect",
        "data_collection.feedback",
        "data_collection.confirm",
        "data_collection.plugin",
        "product_processing.read",
        "product_processing.draft_write",
        "product_processing.process",
        "product_processing.export",
        "product_processing.handoff_consume",
        "price_verification.read",
        "price_verification.quote_collect",
        "price_verification.sourcing_match",
        "price_verification.export",
        "price_verification.plugin",
        "seller_listing.read",
        "seller_listing.price_confirm",
        "seller_listing.attribute_write",
    }
)


def _seed_permissions(conn: sqlite3.Connection) -> None:
    for permission_key, module, action, description in DEFAULT_PERMISSIONS:
        conn.execute(
            """
            INSERT INTO permissions (
                permission_key, module, action, description, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(permission_key) DO UPDATE SET
                module = excluded.module,
                action = excluded.action,
                description = excluded.description,
                updated_at = datetime('now')
            """,
            (permission_key, module, action, description),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO role_permissions (role, permission_key)
            VALUES ('admin', ?)
            """,
            (permission_key,),
        )
        if permission_key in OPERATOR_PERMISSIONS:
            conn.execute(
                """
                INSERT OR IGNORE INTO role_permissions (role, permission_key)
                VALUES ('operator', ?)
                """,
                (permission_key,),
            )


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
        _seed_permissions(conn)
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
