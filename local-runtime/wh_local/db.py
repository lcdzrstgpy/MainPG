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
-- remote_token：远端账号服务的 wh_auth_* token 原文，仅用于工作台登出时联动撤销云端登录态。
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
    remote_token TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (user_id) REFERENCES customer_users (user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_customer_sessions_user_active
    ON customer_sessions (user_id, expires_at, revoked_at);

-- 本地真实账号表：第二阶段账号服务使用。后续迁移 MySQL 时保持字段语义不变。
-- login_status：账号当前登录状态（offline/online），云端认证服务维护，用于单端登录限制。
CREATE TABLE IF NOT EXISTS auth_accounts (
    account_id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'operator',
    workspace_id TEXT NOT NULL DEFAULT 'default',
    account_status TEXT NOT NULL DEFAULT 'active',
    login_status TEXT NOT NULL DEFAULT 'offline',
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
    attempts INTEGER NOT NULL DEFAULT 0,
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

-- 注册邀请码表：管理员在服务器上生成邀请码，用户注册时必须提供有效邀请码
-- （一个邀请码可被多个用户重复使用，用 max_uses / used_count 控制可用次数）。
CREATE TABLE IF NOT EXISTS invitation_codes (
    code TEXT PRIMARY KEY,
    max_uses INTEGER NOT NULL DEFAULT 100,
    used_count INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_by_admin_id TEXT NOT NULL DEFAULT '',
    creation_operation_id TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_invitation_codes_status
    ON invitation_codes (used_count, expires_at);

-- 邀请码使用明细：注册成功时记录邀请码与账号的归属关系。
-- 历史版本只累计 used_count，无法可靠反推旧账号，因此从本表上线后开始精确记录。
CREATE TABLE IF NOT EXISTS invitation_code_usages (
    code TEXT NOT NULL,
    account_id TEXT NOT NULL UNIQUE,
    username TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    used_at TEXT NOT NULL,
    PRIMARY KEY (code, account_id),
    FOREIGN KEY (code) REFERENCES invitation_codes (code),
    FOREIGN KEY (account_id) REFERENCES auth_accounts (account_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_invitation_code_usages_code
    ON invitation_code_usages (code, used_at DESC);

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

-- 积分钱包：只允许平台账号服务端写入；本地工作台不得直接修改余额。
CREATE TABLE IF NOT EXISTS billing_wallets (
    account_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    points_balance INTEGER NOT NULL DEFAULT 0,
    locked_points INTEGER NOT NULL DEFAULT 0,
    manual_frozen_points INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 0,
    ledger_head_hash TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES auth_accounts (account_id) ON DELETE CASCADE,
    CHECK (points_balance >= 0),
    CHECK (locked_points >= 0),
    CHECK (manual_frozen_points >= 0)
);

CREATE INDEX IF NOT EXISTS idx_billing_wallets_workspace
    ON billing_wallets (workspace_id, account_id);

-- 支付订单：真实支付以第三方异步回调验签为准；前端/本地运行时只能创建 pending 订单。
CREATE TABLE IF NOT EXISTS billing_payment_orders (
    order_id TEXT PRIMARY KEY,
    out_trade_no TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    provider TEXT NOT NULL CHECK (provider IN ('wechat', 'alipay')),
    package_id TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CNY',
    points INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'paid', 'closed', 'failed', 'refunded')),
    gateway_transaction_id TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    paid_at TEXT NOT NULL DEFAULT '',
    cancelled_at TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    raw_notify_ciphertext TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (account_id) REFERENCES auth_accounts (account_id) ON DELETE CASCADE,
    UNIQUE (account_id, idempotency_key),
    CHECK (amount_cents > 0),
    CHECK (points > 0)
);

CREATE INDEX IF NOT EXISTS idx_billing_payment_orders_account_status
    ON billing_payment_orders (account_id, status, created_at);

-- 积分账本：后续扣费/充值均追加写入，并用 previous_hash + row_hash 做篡改检测。
CREATE TABLE IF NOT EXISTS billing_point_ledger (
    entry_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    direction TEXT NOT NULL CHECK (direction IN ('credit', 'debit', 'lock', 'unlock')),
    points_delta INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    previous_hash TEXT NOT NULL DEFAULT '',
    row_hash TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES auth_accounts (account_id) ON DELETE CASCADE,
    UNIQUE (account_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_billing_point_ledger_account_time
    ON billing_point_ledger (account_id, created_at);

-- 计费事件：业务模块消耗积分时先向服务器登记并由服务器扣减，严禁信任本地自报余额。
CREATE TABLE IF NOT EXISTS billing_usage_events (
    usage_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    module TEXT NOT NULL,
    action TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    points_charged INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL,
    source_ref TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'charged' CHECK (status IN ('charged', 'reversed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES auth_accounts (account_id) ON DELETE CASCADE,
    UNIQUE (account_id, idempotency_key),
    CHECK (quantity > 0),
    CHECK (points_charged >= 0)
);

-- AI 调用计费事件：先冻结预估积分，提供方完成后按实际成本结算或失败解锁。
-- 与上面的旧版即时扣费表并存，供采用 reserve/settle 协议的新业务模块使用。
CREATE TABLE IF NOT EXISTS billing_ai_usage_events (
    usage_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    feature_key TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    reserved_points INTEGER NOT NULL,
    charged_points INTEGER NOT NULL DEFAULT 0,
    refunded_points INTEGER NOT NULL DEFAULT 0,
    cost_multiplier REAL NOT NULL DEFAULT 1,
    min_charge_points INTEGER NOT NULL DEFAULT 0,
    quantity INTEGER NOT NULL DEFAULT 1,
    actual_cost_cny REAL NOT NULL DEFAULT 0,
    provider TEXT NOT NULL DEFAULT '',
    provider_key_id TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    source_ref TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'reserved'
        CHECK (status IN ('reserved', 'succeeded', 'failed')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    settled_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (account_id) REFERENCES auth_accounts (account_id) ON DELETE CASCADE,
    UNIQUE (account_id, idempotency_key),
    CHECK (reserved_points >= 0),
    CHECK (charged_points >= 0),
    CHECK (refunded_points >= 0),
    CHECK (quantity > 0)
);

CREATE INDEX IF NOT EXISTS idx_billing_ai_usage_events_account_status
    ON billing_ai_usage_events (account_id, status, created_at);

-- 服务端 AI 网关请求账本：同一计费用量的同一规范化请求仅调用上游一次。
-- 只保存已清理的业务响应，不保存平台 token、上游 key 或请求正文。
CREATE TABLE IF NOT EXISTS billing_ai_gateway_requests (
    usage_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    account_id TEXT NOT NULL,
    feature_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'in_progress'
        CHECK (status IN ('in_progress', 'succeeded', 'failed')),
    response_json TEXT NOT NULL DEFAULT '',
    attempt_count INTEGER NOT NULL DEFAULT 1,
    lease_expires_at TEXT NOT NULL DEFAULT '',
    phase TEXT NOT NULL DEFAULT 'claimed',
    provider_task_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (usage_id, request_hash),
    FOREIGN KEY (usage_id) REFERENCES billing_ai_usage_events (usage_id) ON DELETE CASCADE,
    FOREIGN KEY (account_id) REFERENCES auth_accounts (account_id) ON DELETE CASCADE,
    CHECK (attempt_count > 0)
);

CREATE INDEX IF NOT EXISTS idx_billing_ai_gateway_requests_usage_status
    ON billing_ai_gateway_requests (usage_id, status, created_at);

-- Server-side billing rules.  Monetary values stay in integer tenths of a
-- point so pricing such as 3.5 points is exact and never relies on float
-- arithmetic.  Only the platform server writes this singleton row.
CREATE TABLE IF NOT EXISTS billing_pricing_rules (
    rule_id INTEGER PRIMARY KEY CHECK (rule_id = 1),
    rule_version INTEGER NOT NULL DEFAULT 1,
    point_unit_scale INTEGER NOT NULL DEFAULT 10,
    points_per_cny INTEGER NOT NULL DEFAULT 100,
    text_reserve_units INTEGER NOT NULL DEFAULT 50,
    text_charge_units INTEGER NOT NULL DEFAULT 50,
    image_reserve_units INTEGER NOT NULL DEFAULT 400,
    image_charge_units INTEGER NOT NULL DEFAULT 350,
    min_client_version TEXT NOT NULL DEFAULT '',
    effective_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_by TEXT NOT NULL DEFAULT 'system',
    CHECK (point_unit_scale = 10),
    CHECK (points_per_cny > 0),
    CHECK (text_reserve_units >= text_charge_units),
    CHECK (image_reserve_units >= image_charge_units),
    CHECK (text_charge_units >= 0),
    CHECK (image_charge_units >= 0)
);

CREATE TABLE IF NOT EXISTS billing_runtime_meta (
    meta_key TEXT PRIMARY KEY,
    meta_value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 子项定价：一条产品链接按处理子项拆分为 title/description/product_dimensions/
-- four_grid/detail_images，每个子项独立单价。规则由服务端定价引擎维护，
-- 每次改价写入新 rule_version（与 billing_pricing_rules.rule_version 联动）。
CREATE TABLE IF NOT EXISTS billing_pricing_items (
    rule_version INTEGER NOT NULL,
    feature_key TEXT NOT NULL,
    charge_points INTEGER NOT NULL DEFAULT 0,
    intercept_refund_ratio REAL NOT NULL DEFAULT 0.5,
    no_return_refund_ratio REAL NOT NULL DEFAULT 1.0,
    effective_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (rule_version, feature_key),
    CHECK (charge_points >= 0),
    CHECK (intercept_refund_ratio >= 0 AND intercept_refund_ratio <= 1),
    CHECK (no_return_refund_ratio >= 0 AND no_return_refund_ratio <= 1)
);

CREATE INDEX IF NOT EXISTS idx_billing_pricing_items_version
    ON billing_pricing_items (rule_version);

-- 定价变更审计：只追加，不改不删；保存变更前后完整定价 JSON 快照。
CREATE TABLE IF NOT EXISTS billing_pricing_changelog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_version INTEGER NOT NULL,
    changed_by TEXT NOT NULL,
    change_reason TEXT NOT NULL DEFAULT '',
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_billing_pricing_changelog_version
    ON billing_pricing_changelog (rule_version, created_at);

-- 密钥发放审计：客户端批量冻结时下发短期密钥，记录归属与过期时间（不含明文）。
CREATE TABLE IF NOT EXISTS billing_key_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grant_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    freeze_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    key_label TEXT NOT NULL DEFAULT '',
    granted_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    revoked_at TEXT NOT NULL DEFAULT '',
    UNIQUE (grant_id)
);

CREATE INDEX IF NOT EXISTS idx_billing_key_grants_account_time
    ON billing_key_grants (account_id, granted_at);

CREATE INDEX IF NOT EXISTS idx_billing_key_grants_freeze
    ON billing_key_grants (freeze_id);

-- 批次冻结：客户端提交一批链接时按 N×45 预扣积分，处理完成后按子项明细结算。
-- status=frozen 且超过 TTL 未结算时由服务端定时任务释放全部冻结。
CREATE TABLE IF NOT EXISTS billing_batch_freezes (
    freeze_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    link_count INTEGER NOT NULL,
    scope_json TEXT NOT NULL DEFAULT '[]',
    frozen_points INTEGER NOT NULL,
    charged_points INTEGER NOT NULL DEFAULT 0,
    refunded_points INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'frozen'
        CHECK (status IN ('frozen', 'settled', 'released')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    settled_at TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT (datetime('now', '+7 days')),
    FOREIGN KEY (account_id) REFERENCES auth_accounts (account_id) ON DELETE CASCADE,
    CHECK (link_count > 0),
    CHECK (frozen_points >= 0)
);

CREATE INDEX IF NOT EXISTS idx_billing_batch_freezes_account_status
    ON billing_batch_freezes (account_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_billing_batch_freezes_expiry
    ON billing_batch_freezes (status, expires_at);

-- 批次结算明细：每条链接的每个子项处理结果（成功/拦截/无返回），
-- 服务端据此按当前定价规则计算扣费与退款，客户端上报后落库供对账。
CREATE TABLE IF NOT EXISTS billing_batch_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    freeze_id TEXT NOT NULL,
    link_idx INTEGER NOT NULL,
    feature_key TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('success', 'intercept', 'no_return')),
    charge_points INTEGER NOT NULL DEFAULT 0,
    refund_points INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (freeze_id, link_idx, feature_key),
    FOREIGN KEY (freeze_id) REFERENCES billing_batch_freezes (freeze_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_billing_batch_items_freeze
    ON billing_batch_items (freeze_id);
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
        (
            "data_collection:004_plugin_session_client_identity",
            root / "data_collection" / "migrations" / "004_plugin_session_client_identity.sql",
        ),
        (
            "data_collection:005_shop_collection",
            root / "data_collection" / "migrations" / "005_shop_collection.sql",
        ),
        (
            "data_collection:006_shop_collection_lease_tokens",
            root / "data_collection" / "migrations" / "006_shop_collection_lease_tokens.sql",
        ),
        (
            "data_collection:007_sku_repull_outbox",
            root / "data_collection" / "migrations" / "007_sku_repull_outbox.sql",
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
    shop_candidate_uniqueness_sql = (
        root / "modules" / "product_processing" / "migrations" / "004_shop_candidate_uniqueness.sql"
    )
    if shop_candidate_uniqueness_sql.exists():
        migrations.append(
            (
                "product_processing:004_shop_candidate_uniqueness",
                "product_processing",
                shop_candidate_uniqueness_sql.read_text(encoding="utf-8"),
            )
        )
    pod_customization_migrations = (
        "001_pod_customization",
        "002_direct_listing_trials",
        "003_style_grid_v2",
        "004_style_grid_publications",
        "005_dianxiaomi_exports",
        "006_pod_titles",
        "007_requested_count_upgrade",
    )
    for migration_name in pod_customization_migrations:
        sql_path = (
            root
            / "modules"
            / "pod_customization"
            / "migrations"
            / f"{migration_name}.sql"
        )
        if sql_path.exists():
            migrations.append(
                (
                    f"pod_customization:{migration_name}",
                    "pod_customization",
                    sql_path.read_text(encoding="utf-8"),
                )
            )
    ai_service_sql = root / "modules" / "ai_service" / "migrations" / "001_ai_service.sql"
    if ai_service_sql.exists():
        migrations.append(
            (
                "ai_service:001_ai_service",
                "ai_service",
                ai_service_sql.read_text(encoding="utf-8"),
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
    profit_activity_dynamic_sites_sql = (
        root / "modules" / "profit_activity" / "migrations" / "003_dynamic_sites.sql"
    )
    if profit_activity_dynamic_sites_sql.exists():
        migrations.append(
            (
                "profit_activity:003_dynamic_sites",
                "profit_activity",
                profit_activity_dynamic_sites_sql.read_text(encoding="utf-8"),
            )
        )
    profit_activity_attachment_image_sql = (
        root / "modules" / "profit_activity" / "migrations" / "004_product_library_attachment_image.sql"
    )
    if profit_activity_attachment_image_sql.exists():
        migrations.append(
            (
                "profit_activity:004_product_library_attachment_image",
                "profit_activity",
                profit_activity_attachment_image_sql.read_text(encoding="utf-8"),
            )
        )
    profit_activity_threshold_sql = (
        root / "modules" / "profit_activity" / "migrations" / "005_activity_threshold_configuration.sql"
    )
    if profit_activity_threshold_sql.exists():
        migrations.append(
            (
                "profit_activity:005_activity_threshold_configuration",
                "profit_activity",
                profit_activity_threshold_sql.read_text(encoding="utf-8"),
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
    price_verification_forward_migrations = (
        ("007_prescreen_settings", "007_prescreen_settings.sql"),
        ("008_batch_sourcing_sessions", "008_batch_sourcing_sessions.sql"),
    )
    for migration_name, filename in price_verification_forward_migrations:
        sql_path = root / "price_verification" / "migrations" / filename
        if sql_path.exists():
            migrations.append(
                (
                    f"price_verification:{migration_name}",
                    "price_verification",
                    sql_path.read_text(encoding="utf-8"),
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
    _ensure_column(
        conn,
        "billing_wallets",
        "manual_frozen_points",
        "INTEGER NOT NULL DEFAULT 0",
    )
    # 登录状态字段：账号级单端登录限制（云端认证服务与本地工作台共用同一 schema）。
    _ensure_column(conn, "auth_accounts", "login_status", "TEXT NOT NULL DEFAULT 'offline'")
    # 本地会话表保存远端 wh_auth_* token，登出时联动撤销云端登录态。
    _ensure_column(conn, "customer_sessions", "remote_token", "TEXT NOT NULL DEFAULT ''")
    # 邮箱验证码失败次数用于限制暴力尝试；旧数据库平滑补列。
    _ensure_column(conn, "auth_email_verifications", "attempts", "INTEGER NOT NULL DEFAULT 0")
    # Durable AI gateway recovery state. These fields contain only non-secret
    # usage/provider task identifiers and are safe to add to legacy databases.
    _ensure_column(conn, "billing_ai_gateway_requests", "lease_expires_at", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "billing_ai_gateway_requests", "phase", "TEXT NOT NULL DEFAULT 'claimed'")
    _ensure_column(conn, "billing_ai_gateway_requests", "provider_task_id", "TEXT NOT NULL DEFAULT ''")
    _migrate_billing_points_to_tenths(conn)


def _migrate_billing_points_to_tenths(conn: sqlite3.Connection) -> None:
    """Move the legacy integer-point ledger to exact 0.1-point units once.

    The public balance remains numerically unchanged because API responses
    divide by the active scale.  The marker makes startup safe to repeat on
    both existing server databases and fresh local development databases.
    """
    marker = conn.execute(
        "SELECT meta_value FROM billing_runtime_meta WHERE meta_key = 'point_unit_scale'"
    ).fetchone()
    if marker is None:
        for table, columns in (
            ("billing_wallets", ("points_balance", "locked_points", "manual_frozen_points")),
            ("billing_payment_orders", ("points",)),
            ("billing_point_ledger", ("points_delta", "balance_after")),
            ("billing_usage_events", ("points_charged",)),
            (
                "billing_ai_usage_events",
                ("reserved_points", "charged_points", "refunded_points", "min_charge_points"),
            ),
        ):
            present = {
                str(row["name"])
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for column in columns:
                if column in present:
                    conn.execute(f"UPDATE {table} SET {column} = {column} * 10")
        conn.execute(
            "INSERT INTO billing_runtime_meta (meta_key, meta_value) VALUES ('point_unit_scale', '10')"
        )
    conn.execute(
        """
        INSERT INTO billing_pricing_rules (
            rule_id, rule_version, point_unit_scale, points_per_cny,
            text_reserve_units, text_charge_units,
            image_reserve_units, image_charge_units, updated_by
        )
        VALUES (1, 1, 10, 100, 50, 50, 400, 350, 'system')
        ON CONFLICT(rule_id) DO NOTHING
        """
    )
    # Roll out the product-link bundle policy once for databases created before
    # it existed.  A marker prevents every startup from overwriting an
    # administrator's later pricing revision.
    bundle_marker = conn.execute(
        "SELECT meta_value FROM billing_runtime_meta WHERE meta_key = 'product_link_bundle_pricing_v2'"
    ).fetchone()
    if bundle_marker is None:
        conn.execute(
            """
            UPDATE billing_pricing_rules
            SET rule_version = rule_version + 1,
                points_per_cny = 100,
                text_reserve_units = 50,
                text_charge_units = 50,
                image_reserve_units = 400,
                image_charge_units = 350,
                updated_at = datetime('now'),
                updated_by = 'system:product_link_bundle_v2'
            WHERE rule_id = 1
            """
        )
        conn.execute(
            "INSERT INTO billing_runtime_meta (meta_key, meta_value) VALUES ('product_link_bundle_pricing_v2', 'applied')"
        )
    # Seed the per-subitem pricing for the current rule version.  A marker
    # prevents every startup from overwriting an administrator's later revision;
    # when pricing already has items for the active version the marker stays
    # untouched and nothing is re-inserted.
    item_marker = conn.execute(
        "SELECT meta_value FROM billing_runtime_meta WHERE meta_key = 'billing_pricing_items_seeded'"
    ).fetchone()
    if item_marker is None:
        rule = conn.execute(
            "SELECT rule_version FROM billing_pricing_rules WHERE rule_id = 1"
        ).fetchone()
        rule_version = int(rule["rule_version"]) if rule is not None else 1
        existing_items = conn.execute(
            "SELECT 1 FROM billing_pricing_items WHERE rule_version = ? LIMIT 1",
            (rule_version,),
        ).fetchone()
        if existing_items is None:
            # 单位（tenths of a point）：8/8/7/12/10 积分 = 80/80/70/120/100 单位，合计 450 单位。
            default_items = (
                ("title", 80, 0.5, 1.0),
                ("description", 80, 0.5, 1.0),
                ("product_dimensions", 70, 0.5, 1.0),
                ("four_grid", 120, 0.5, 1.0),
                ("detail_images", 100, 0.5, 1.0),
            )
            for feature_key, charge, intercept_ratio, no_return_ratio in default_items:
                conn.execute(
                    """
                    INSERT INTO billing_pricing_items (
                        rule_version, feature_key, charge_points,
                        intercept_refund_ratio, no_return_refund_ratio
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (rule_version, feature_key, charge, intercept_ratio, no_return_ratio),
                )
        conn.execute(
            "INSERT OR REPLACE INTO billing_runtime_meta (meta_key, meta_value) "
            "VALUES ('billing_pricing_items_seeded', 'applied')"
        )


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
    ("pod_customization.read", "pod_customization", "read", "查看 POD 模板、个人批次和生成结果"),
    ("pod_customization.create", "pod_customization", "create", "创建和重试 POD 图片与标题任务"),
    ("pod_customization.template_manage", "pod_customization", "template_manage", "维护工作区共享 POD 模板"),
    ("pod_customization.export", "pod_customization", "export", "导出本人 POD 结果和店小秘文件"),
    ("ai_service.read", "ai_service", "read", "查看本地 AI 服务会话和素材"),
    ("ai_service.create", "ai_service", "create", "发起 AI 对话、上传素材和创建商品图"),
    ("ai_service.delete", "ai_service", "delete", "删除本人 AI 会话和素材"),
    ("ai_service.settings_manage", "ai_service", "settings_manage", "维护 AI 模型白名单和创作模板"),
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
        "pod_customization.read",
        "pod_customization.create",
        "pod_customization.template_manage",
        "pod_customization.export",
        "ai_service.read",
        "ai_service.create",
        "ai_service.delete",
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
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
