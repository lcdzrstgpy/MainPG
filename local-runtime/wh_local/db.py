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

-- 正式角色表：商业化后由 owner/admin 给员工分配角色，普通注册不应自行选择 admin。
CREATE TABLE IF NOT EXISTS roles (
    role TEXT PRIMARY KEY,
    role_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_system INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

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

-- 用户-角色关联表：支持一个员工在一个工作区下拥有一个或多个角色。
CREATE TABLE IF NOT EXISTS user_roles (
    account_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    role TEXT NOT NULL,
    assigned_by TEXT NOT NULL DEFAULT '',
    assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (account_id, workspace_id, role),
    FOREIGN KEY (role) REFERENCES roles (role),
    FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id)
);

CREATE INDEX IF NOT EXISTS idx_user_roles_workspace_role
    ON user_roles (workspace_id, role, account_id);

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

-- 远端平台账号服务会话表：由独立 customer auth 服务签发，和本地工作台 customer_sessions 分开。
CREATE TABLE IF NOT EXISTS auth_platform_sessions (
    session_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    revoked_at TEXT NOT NULL DEFAULT '',
    last_used_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    user_agent TEXT NOT NULL DEFAULT '',
    client_ip TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (account_id) REFERENCES auth_accounts (account_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_auth_platform_sessions_account_active
    ON auth_platform_sessions (account_id, expires_at, revoked_at);

-- 密码重置凭证表：忘记密码时生成一次性 token，只保存 token_hash，不保存明文 token。
CREATE TABLE IF NOT EXISTS auth_password_reset_tokens (
    reset_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    request_ip TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (account_id) REFERENCES auth_accounts (account_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_auth_password_reset_tokens_account_active
    ON auth_password_reset_tokens (account_id, expires_at, used_at);

-- 账号安全事件表：记录注册、修改密码、忘记密码、重置密码等关键安全动作。
CREATE TABLE IF NOT EXISTS auth_security_events (
    event_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    success INTEGER NOT NULL DEFAULT 1,
    ip TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_auth_security_events_account_type
    ON auth_security_events (account_id, event_type, created_at);

-- 邮箱验证凭证表：注册、改邮箱或邀请接受时验证邮箱，正式阶段通过邮件发送 token。
CREATE TABLE IF NOT EXISTS auth_email_verifications (
    verification_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    purpose TEXT NOT NULL DEFAULT 'verify_email',
    expires_at TEXT NOT NULL,
    used_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    request_ip TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_auth_email_verifications_email_active
    ON auth_email_verifications (email, purpose, expires_at, used_at);

-- 员工邀请表：商业化客户由 owner/admin 邀请员工加入工作区并指定角色。
CREATE TABLE IF NOT EXISTS account_invitations (
    invitation_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    email TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'operator',
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    accepted_at TEXT NOT NULL DEFAULT '',
    revoked_at TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id),
    FOREIGN KEY (role) REFERENCES roles (role)
);

CREATE INDEX IF NOT EXISTS idx_account_invitations_workspace_email
    ON account_invitations (workspace_id, email, expires_at, accepted_at, revoked_at);

-- 商业授权状态表：打包售卖时限制客户、域名、用户数、模块和到期时间。
CREATE TABLE IF NOT EXISTS license_state (
    license_id TEXT PRIMARY KEY,
    license_key_hash TEXT NOT NULL DEFAULT '',
    company_name TEXT NOT NULL DEFAULT '',
    workspace_limit INTEGER NOT NULL DEFAULT 1,
    user_limit INTEGER NOT NULL DEFAULT 5,
    enabled_modules_json TEXT NOT NULL DEFAULT '[]',
    expires_at TEXT NOT NULL DEFAULT '',
    machine_fingerprint TEXT NOT NULL DEFAULT '',
    domain TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'inactive',
    activated_at TEXT NOT NULL DEFAULT '',
    last_checked_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_license_state_status_expiry
    ON license_state (status, expires_at);

-- 商业授权激活日志表：记录授权激活、校验、失败原因和设备信息。
CREATE TABLE IF NOT EXISTS license_activation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    license_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    success INTEGER NOT NULL DEFAULT 1,
    failure_reason TEXT NOT NULL DEFAULT '',
    machine_fingerprint TEXT NOT NULL DEFAULT '',
    domain TEXT NOT NULL DEFAULT '',
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
        (
            "data_collection:003_plugin_command_requests",
            root / "data_collection" / "migrations" / "003_plugin_command_requests.sql",
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
    plugin_session_client_identity_sql = (
        root / "data_collection" / "migrations" / "004_plugin_session_client_identity.sql"
    )
    if plugin_session_client_identity_sql.exists():
        migrations.append(
            (
                "data_collection:004_plugin_session_client_identity",
                "data_collection",
                plugin_session_client_identity_sql.read_text(encoding="utf-8"),
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
    source_image_sync_sql = root / "modules" / "product_processing" / "migrations" / "002_source_image_sync.sql"
    if source_image_sync_sql.exists():
        migrations.append(
            (
                "product_processing:002_source_image_sync",
                "product_processing",
                source_image_sync_sql.read_text(encoding="utf-8"),
            )
        )
    source_image_sync_lease_sql = root / "modules" / "product_processing" / "migrations" / "003_source_image_sync_lease.sql"
    if source_image_sync_lease_sql.exists():
        migrations.append(
            (
                "product_processing:003_source_image_sync_lease",
                "product_processing",
                source_image_sync_lease_sql.read_text(encoding="utf-8"),
            )
        )
    profit_activity_sql = root / "modules" / "profit_activity" / "migrations" / "001_profit_activity.sql"
    if profit_activity_sql.exists():
        migrations.append(
            (
                "profit_activity:001_profit_activity",
                "profit_activity",
                profit_activity_sql.read_text(encoding="utf-8"),
            )
        )
    profit_activity_source_type_sql = (
        root / "modules" / "profit_activity" / "migrations" / "002_product_library_source_type.sql"
    )
    if profit_activity_source_type_sql.exists():
        migrations.append(
            (
                "profit_activity:002_product_library_source_type",
                "profit_activity",
                profit_activity_source_type_sql.read_text(encoding="utf-8"),
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
    retained_link_sourcing_sql = (
        root / "price_verification" / "migrations" / "002_retained_link_sourcing.sql"
    )
    if retained_link_sourcing_sql.exists():
        migrations.append(
            (
                "price_verification:002_retained_link_sourcing",
                "price_verification",
                retained_link_sourcing_sql.read_text(encoding="utf-8"),
            )
        )
    direct_quote_batches_sql = (
        root / "price_verification" / "migrations" / "003_direct_price_quote_batches.sql"
    )
    if direct_quote_batches_sql.exists():
        migrations.append(
            (
                "price_verification:003_direct_price_quote_batches",
                "price_verification",
                direct_quote_batches_sql.read_text(encoding="utf-8"),
            )
        )
    quote_capture_chunk_sku_capacity_sql = (
        root / "price_verification" / "migrations" / "004_quote_capture_chunk_sku_capacity.sql"
    )
    if quote_capture_chunk_sku_capacity_sql.exists():
        migrations.append(
            (
                "price_verification:004_quote_capture_chunk_sku_capacity",
                "price_verification",
                quote_capture_chunk_sku_capacity_sql.read_text(encoding="utf-8"),
            )
        )
    batch_selections_sql = (
        root / "price_verification" / "migrations" / "005_batch_selections.sql"
    )
    if batch_selections_sql.exists():
        migrations.append(
            (
                "price_verification:005_batch_selections",
                "price_verification",
                batch_selections_sql.read_text(encoding="utf-8"),
            )
        )
    skc_source_links_sql = (
        root / "price_verification" / "migrations" / "006_skc_source_links.sql"
    )
    if skc_source_links_sql.exists():
        migrations.append(
            (
                "price_verification:006_skc_source_links",
                "price_verification",
                skc_source_links_sql.read_text(encoding="utf-8"),
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
    ("profit_activity.read", "profit_activity", "read", "查询本人利润产品和活动筛选结果"),
    ("profit_activity.company_read", "profit_activity", "company_read", "查询本工作区/公司共享利润产品"),
    ("profit_activity.write", "profit_activity", "write", "新增、编辑和归档本人利润产品"),
    ("profit_activity.company_write", "profit_activity", "company_write", "编辑本工作区/公司共享利润产品"),
    ("profit_activity.delete", "profit_activity", "delete", "删除本人利润产品"),
    ("profit_activity.company_delete", "profit_activity", "company_delete", "删除本工作区/公司共享利润产品"),
    ("profit_activity.settings_manage", "profit_activity", "settings_manage", "维护利润活动配置和规则版本"),
    ("profit_activity.import", "profit_activity", "import", "导入产品资料 Excel 并确认入档"),
    ("profit_activity.filter", "profit_activity", "filter", "执行活动报名 Excel 利润筛选"),
    ("profit_activity.export", "profit_activity", "export", "导出产品档案和活动筛选结果"),
    ("seller_listing.read", "seller_listing", "read", "查看卖家中心上架、核价和库存流程数据"),
    ("seller_listing.price_confirm", "seller_listing", "price_confirm", "处理核价、调价和价格待确认产品"),
    ("seller_listing.attribute_write", "seller_listing", "attribute_write", "修改产品属性、详情和库存"),
    ("seller_listing.publish", "seller_listing", "publish", "执行或确认产品上架完成"),
    ("settings.read", "settings", "read", "查看系统配置"),
    ("settings.manage", "settings", "manage", "维护系统配置、密钥和运行参数"),
    ("stores.manage", "stores", "manage", "维护店铺配置和平台站点信息"),
    ("users.manage", "users", "manage", "维护用户、角色和权限"),
)


DEFAULT_ROLES: tuple[tuple[str, str, str], ...] = (
    ("owner", "老板/超级管理员", "客户公司最高权限账号，可管理授权、员工、店铺和全部模块"),
    ("admin", "管理员", "管理工作区配置、员工账号和多数业务模块"),
    ("operator", "运营", "执行日常运营、选品、产品处理等常规任务"),
    ("product_specialist", "产品处理人员", "负责产品草稿、生成、导出和下游交接"),
    ("designer", "精致作图人员", "负责图片处理、作图任务和素材协作"),
    ("pricing_specialist", "核价及货源人员", "负责核价证据采集、货源匹配和报价处理"),
    ("finance", "利润活动人员", "负责利润测算、活动筛选和成本数据维护"),
    ("viewer", "只读观察员", "仅查看被授权模块数据，不执行写入操作"),
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
        "profit_activity.read",
        "profit_activity.write",
        "profit_activity.import",
        "profit_activity.filter",
        "profit_activity.export",
        "seller_listing.read",
        "seller_listing.price_confirm",
        "seller_listing.attribute_write",
    }
)


def _seed_roles(conn: sqlite3.Connection) -> None:
    for role, role_name, description in DEFAULT_ROLES:
        conn.execute(
            """
            INSERT INTO roles (role, role_name, description, is_system, created_at, updated_at)
            VALUES (?, ?, ?, 1, datetime('now'), datetime('now'))
            ON CONFLICT(role) DO UPDATE SET
                role_name = excluded.role_name,
                description = excluded.description,
                updated_at = datetime('now')
            """,
            (role, role_name, description),
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
        conn.execute(
            """
            INSERT OR IGNORE INTO role_permissions (role, permission_key)
            VALUES ('owner', ?)
            """,
            (permission_key,),
        )
        if action == "read" or permission_key.endswith(".read"):
            conn.execute(
                """
                INSERT OR IGNORE INTO role_permissions (role, permission_key)
                VALUES ('viewer', ?)
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
        _seed_roles(conn)
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
